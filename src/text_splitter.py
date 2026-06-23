from __future__ import annotations

from pathlib import Path


def split_text(text: str, max_chars: int = 120000) -> list[str]:
    if max_chars <= 1000:
        raise ValueError("max_chars must be > 1000")
    if len(text) <= max_chars:
        return [text]

    parts: list[str] = []
    remaining = text
    while remaining:
        if len(remaining) <= max_chars:
            parts.append(remaining)
            break
        cut = remaining.rfind("\n\n", 0, max_chars)
        if cut < max_chars * 0.5:
            cut = remaining.rfind("\n", 0, max_chars)
        if cut < max_chars * 0.5:
            cut = max_chars
        parts.append(remaining[:cut].rstrip())
        remaining = remaining[cut:].lstrip()
    return parts


def write_split_text(text: str, output_dir: str | Path, prefix: str, max_chars: int = 120000) -> list[Path]:
    selected = Path(output_dir)
    selected.mkdir(parents=True, exist_ok=True)
    parts = split_text(text, max_chars=max_chars)
    paths: list[Path] = []
    total = len(parts)
    for idx, part in enumerate(parts, start=1):
        path = selected / f"{prefix}_part_{idx:02d}.txt"
        header = f"Part {idx}/{total}\n\n"
        path.write_text(header + part, encoding="utf-8", newline="\n")
        paths.append(path)
    return paths
