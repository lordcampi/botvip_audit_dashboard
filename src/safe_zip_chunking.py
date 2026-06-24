"""F5_T04a Safe ZIP Chunking Guard for BotVIP Daily AI Reporter.

Reporting-only utility. It does not read or write the BotVIP DB and does not
change strategy, thresholds, scanner, lifecycle, Telegram runtime, or schema.

Invariant for AI_REVIEW zip packages:
- No generated text file included in the ZIP may exceed max_chars characters.
- Large JSON files are split into valid deterministic JSON parts.
- Large CSV files are split by rows with headers preserved.
- Large TXT/MD files are split by lines.
- Split indexes are generated and can be referenced from README/manifest.
"""
from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from io import StringIO
from pathlib import Path
from typing import Any, Iterable

DEFAULT_ZIP_CHAR_LIMIT = 100000
DEFAULT_SAFE_TARGET_CHARS = 95000
TEXT_SUFFIXES = {".json", ".csv", ".txt", ".md", ".log"}


@dataclass(frozen=True)
class ChunkResult:
    original_file: str
    included_files: list[Path]
    index_file: Path | None
    was_split: bool
    original_chars: int
    max_chars: int
    strategy: str


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _write_text(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")
    return path


def _char_count(path: Path) -> int:
    return len(_read_text(path))


def _json_dumps(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2, default=str) + "\n"


def _part_name(path: Path, part_number: int) -> Path:
    return path.with_name(f"{path.stem}_part{part_number:03d}{path.suffix}")


def _index_name(path: Path) -> Path:
    return path.with_name(f"{path.stem}_index.json")


def _relative_name(path: Path, base_dir: Path) -> str:
    try:
        return path.relative_to(base_dir).as_posix()
    except ValueError:
        return path.name


def _write_index(
    *,
    original: Path,
    parts: list[Path],
    base_dir: Path,
    max_chars: int,
    original_chars: int,
    strategy: str,
) -> Path:
    index_path = _index_name(original)
    payload = {
        "schema_version": "f5_t04a_safe_zip_chunk_index_v1",
        "original_file": _relative_name(original, base_dir),
        "split_reason": "char_limit_exceeded",
        "max_chars_per_file": max_chars,
        "original_chars": original_chars,
        "strategy": strategy,
        "part_count": len(parts),
        "parts": [_relative_name(part, base_dir) for part in parts],
        "note": "The original large file is intentionally excluded from the AI_REVIEW zip when this index is present.",
    }
    _write_text(index_path, _json_dumps(payload))
    if _char_count(index_path) > max_chars:
        raise ValueError(f"Chunk index exceeds limit: {index_path}")
    return index_path


def _append_item_chunks(
    *,
    original: Path,
    items: list[Any],
    make_payload,
    base_dir: Path,
    max_chars: int,
    original_chars: int,
    strategy: str,
) -> ChunkResult:
    parts: list[Path] = []
    current: list[Any] = []

    def flush() -> None:
        if not current:
            return
        part_path = _part_name(original, len(parts) + 1)
        payload = make_payload(list(current), len(parts) + 1)
        text = _json_dumps(payload)
        if len(text) > max_chars:
            raise ValueError(f"Single JSON chunk exceeds limit for {original}")
        _write_text(part_path, text)
        parts.append(part_path)
        current.clear()

    for item in items:
        tentative = current + [item]
        text = _json_dumps(make_payload(tentative, len(parts) + 1))
        if len(text) > max_chars and current:
            flush()
            tentative = [item]
            text = _json_dumps(make_payload(tentative, len(parts) + 1))
        if len(text) > max_chars:
            raise ValueError(f"Single JSON item exceeds limit for {original}")
        current.append(item)
    flush()

    index_path = _write_index(
        original=original,
        parts=parts,
        base_dir=base_dir,
        max_chars=max_chars,
        original_chars=original_chars,
        strategy=strategy,
    )
    return ChunkResult(
        original_file=_relative_name(original, base_dir),
        included_files=[index_path] + parts,
        index_file=index_path,
        was_split=True,
        original_chars=original_chars,
        max_chars=max_chars,
        strategy=strategy,
    )


def split_json_file(path: Path, *, base_dir: Path, max_chars: int = DEFAULT_ZIP_CHAR_LIMIT) -> ChunkResult:
    original_chars = _char_count(path)
    if original_chars <= max_chars:
        return ChunkResult(_relative_name(path, base_dir), [path], None, False, original_chars, max_chars, "none")

    data = json.loads(_read_text(path))

    if isinstance(data, list):
        return _append_item_chunks(
            original=path,
            items=data,
            base_dir=base_dir,
            max_chars=max_chars,
            original_chars=original_chars,
            strategy="json_list_items",
            make_payload=lambda chunk, part: {
                "schema_version": "f5_t04a_json_part_v1",
                "split_from": _relative_name(path, base_dir),
                "part_number": part,
                "content_type": "json_list_items",
                "items": chunk,
            },
        )

    if isinstance(data, dict):
        list_keys = [key for key, value in data.items() if isinstance(value, list)]
        if list_keys:
            key = max(list_keys, key=lambda k: len(data.get(k) or []))
            prefix = {k: v for k, v in data.items() if k != key}
            return _append_item_chunks(
                original=path,
                items=list(data.get(key) or []),
                base_dir=base_dir,
                max_chars=max_chars,
                original_chars=original_chars,
                strategy=f"json_dict_list_key:{key}",
                make_payload=lambda chunk, part: {
                    "schema_version": "f5_t04a_json_part_v1",
                    "split_from": _relative_name(path, base_dir),
                    "part_number": part,
                    "content_type": "json_dict_list_key",
                    "list_key": key,
                    "metadata": prefix,
                    key: chunk,
                },
            )

        items = list(data.items())
        return _append_item_chunks(
            original=path,
            items=items,
            base_dir=base_dir,
            max_chars=max_chars,
            original_chars=original_chars,
            strategy="json_dict_top_level_keys",
            make_payload=lambda chunk, part: {
                "schema_version": "f5_t04a_json_part_v1",
                "split_from": _relative_name(path, base_dir),
                "part_number": part,
                "content_type": "json_dict_top_level_keys",
                "data": {k: v for k, v in chunk},
            },
        )

    raise ValueError(f"Unsupported large JSON root type for safe splitting: {path}")


def split_csv_file(path: Path, *, base_dir: Path, max_chars: int = DEFAULT_ZIP_CHAR_LIMIT) -> ChunkResult:
    original_chars = _char_count(path)
    if original_chars <= max_chars:
        return ChunkResult(_relative_name(path, base_dir), [path], None, False, original_chars, max_chars, "none")

    text = _read_text(path)
    reader = csv.reader(StringIO(text))
    rows = list(reader)
    if not rows:
        return ChunkResult(_relative_name(path, base_dir), [path], None, False, original_chars, max_chars, "none")

    header = rows[0]
    data_rows = rows[1:]
    parts: list[Path] = []
    current: list[list[str]] = []

    def render(rows_for_part: list[list[str]]) -> str:
        buf = StringIO()
        writer = csv.writer(buf, lineterminator="\n")
        writer.writerow(header)
        writer.writerows(rows_for_part)
        return buf.getvalue()

    def flush() -> None:
        if not current:
            return
        part_path = _part_name(path, len(parts) + 1)
        part_text = render(current)
        if len(part_text) > max_chars:
            raise ValueError(f"Single CSV chunk exceeds limit for {path}")
        _write_text(part_path, part_text)
        parts.append(part_path)
        current.clear()

    for row in data_rows:
        tentative = current + [row]
        if len(render(tentative)) > max_chars and current:
            flush()
            tentative = [row]
        if len(render(tentative)) > max_chars:
            raise ValueError(f"Single CSV row exceeds limit for {path}")
        current.append(row)
    flush()

    index_path = _write_index(
        original=path,
        parts=parts,
        base_dir=base_dir,
        max_chars=max_chars,
        original_chars=original_chars,
        strategy="csv_rows_with_header",
    )
    return ChunkResult(_relative_name(path, base_dir), [index_path] + parts, index_path, True, original_chars, max_chars, "csv_rows_with_header")


def split_text_file(path: Path, *, base_dir: Path, max_chars: int = DEFAULT_ZIP_CHAR_LIMIT) -> ChunkResult:
    original_chars = _char_count(path)
    if original_chars <= max_chars:
        return ChunkResult(_relative_name(path, base_dir), [path], None, False, original_chars, max_chars, "none")

    lines = _read_text(path).splitlines(keepends=True)
    parts: list[Path] = []
    current = ""

    def flush() -> None:
        nonlocal current
        if not current:
            return
        part_path = _part_name(path, len(parts) + 1)
        if len(current) > max_chars:
            raise ValueError(f"Single text chunk exceeds limit for {path}")
        _write_text(part_path, current)
        parts.append(part_path)
        current = ""

    for line in lines:
        if len(line) > max_chars:
            raise ValueError(f"Single line exceeds safe ZIP char limit in {path}")
        if len(current) + len(line) > max_chars and current:
            flush()
        current += line
    flush()

    index_path = _write_index(
        original=path,
        parts=parts,
        base_dir=base_dir,
        max_chars=max_chars,
        original_chars=original_chars,
        strategy="text_lines",
    )
    return ChunkResult(_relative_name(path, base_dir), [index_path] + parts, index_path, True, original_chars, max_chars, "text_lines")


def split_file_for_zip(path: Path, *, base_dir: Path, max_chars: int = DEFAULT_ZIP_CHAR_LIMIT) -> ChunkResult:
    suffix = path.suffix.lower()
    if suffix not in TEXT_SUFFIXES:
        return ChunkResult(_relative_name(path, base_dir), [path], None, False, 0, max_chars, "binary_or_unchecked")
    if suffix == ".json":
        return split_json_file(path, base_dir=base_dir, max_chars=max_chars)
    if suffix == ".csv":
        return split_csv_file(path, base_dir=base_dir, max_chars=max_chars)
    return split_text_file(path, base_dir=base_dir, max_chars=max_chars)


def _dedupe_paths(paths: Iterable[Path]) -> list[Path]:
    seen: set[str] = set()
    result: list[Path] = []
    for path in paths:
        key = str(Path(path))
        if key not in seen:
            seen.add(key)
            result.append(Path(path))
    return result


def _update_readme(readme_path: Path | None, split_results: list[ChunkResult], *, base_dir: Path, max_chars: int) -> None:
    if not readme_path or not readme_path.exists():
        return
    split_items = [item for item in split_results if item.was_split]
    marker = "<!-- F5_T04A_SAFE_ZIP_CHUNKING -->"
    original = _read_text(readme_path)
    if marker in original:
        original = original.split(marker, 1)[0].rstrip() + "\n"
    if not split_items:
        extra = (
            f"\n{marker}\n\n"
            "## F5_T04a Safe ZIP Chunking\n"
            f"All generated files included in this ZIP are within the {max_chars} character limit.\n"
        )
    else:
        lines = [
            f"\n{marker}",
            "",
            "## F5_T04a Safe ZIP Chunking",
            f"No generated file included in this ZIP is allowed to exceed {max_chars} characters.",
            "Large files are excluded from the ZIP and replaced by index + part files.",
            "",
            "Split files:",
        ]
        for item in split_items:
            index_name = _relative_name(item.index_file, base_dir) if item.index_file else "none"
            part_names = [_relative_name(part, base_dir) for part in item.included_files if part != item.index_file]
            lines.append(f"- {item.original_file} -> {index_name} ({len(part_names)} parts)")
        extra = "\n".join(lines) + "\n"
    _write_text(readme_path, original.rstrip() + "\n" + extra)


def _update_manifest(manifest_path: Path | None, split_results: list[ChunkResult], *, base_dir: Path, max_chars: int) -> None:
    if not manifest_path or not manifest_path.exists() or manifest_path.suffix.lower() != ".json":
        return
    try:
        manifest = json.loads(_read_text(manifest_path))
    except json.JSONDecodeError:
        return
    split_items = [item for item in split_results if item.was_split]
    manifest["f5_t04a_safe_zip_chunking"] = {
        "schema_version": "f5_t04a_safe_zip_chunking_manifest_v1",
        "max_chars_per_file": max_chars,
        "status": "applied",
        "split_file_count": len(split_items),
        "split_files": [
            {
                "original_file": item.original_file,
                "original_chars": item.original_chars,
                "strategy": item.strategy,
                "index_file": _relative_name(item.index_file, base_dir) if item.index_file else None,
                "included_files": [_relative_name(path, base_dir) for path in item.included_files],
            }
            for item in split_items
        ],
        "note": "Original files above the limit are excluded from the AI_REVIEW zip and replaced by deterministic parts.",
    }
    _write_text(manifest_path, _json_dumps(manifest))


def validate_zip_input_char_limit(files: Iterable[Path], *, max_chars: int = DEFAULT_ZIP_CHAR_LIMIT) -> None:
    violations: list[str] = []
    for file_path in files:
        path = Path(file_path)
        if not path.exists() or path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        chars = _char_count(path)
        if chars > max_chars:
            violations.append(f"{path} has {chars} chars > {max_chars}")
        if path.suffix.lower() == ".json":
            json.loads(_read_text(path))
    if violations:
        raise ValueError("Safe ZIP char limit violations: " + "; ".join(violations))


def prepare_zip_files_for_char_limit(
    files: Iterable[Path],
    *,
    report_dir: Path,
    manifest_path: Path | None = None,
    readme_path: Path | None = None,
    max_chars: int = DEFAULT_ZIP_CHAR_LIMIT,
) -> list[Path]:
    """Return ZIP input files after replacing oversized text files with safe parts."""
    base_dir = Path(report_dir)
    original_files = _dedupe_paths(Path(p) for p in files if Path(p).exists())

    first_pass: list[ChunkResult] = []
    for path in original_files:
        first_pass.append(split_file_for_zip(path, base_dir=base_dir, max_chars=max_chars))

    _update_manifest(manifest_path, first_pass, base_dir=base_dir, max_chars=max_chars)
    _update_readme(readme_path, first_pass, base_dir=base_dir, max_chars=max_chars)

    # Re-run after README/manifest updates, because those files may have changed.
    final_files: list[Path] = []
    final_results: list[ChunkResult] = []
    for path in original_files:
        if not path.exists():
            continue
        result = split_file_for_zip(path, base_dir=base_dir, max_chars=max_chars)
        final_results.append(result)
        final_files.extend(result.included_files)

    final_files = _dedupe_paths(final_files)
    validate_zip_input_char_limit(final_files, max_chars=max_chars)
    return final_files
