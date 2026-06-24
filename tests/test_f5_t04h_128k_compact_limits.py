from __future__ import annotations

import json
from pathlib import Path

from src.safe_zip_chunking import (
    DEFAULT_SAFE_TARGET_CHARS,
    DEFAULT_ZIP_CHAR_LIMIT,
    prepare_zip_files_for_char_limit,
    validate_zip_input_char_limit,
)


def test_f5_t04h_defaults_are_copilot_safe_margin() -> None:
    assert DEFAULT_ZIP_CHAR_LIMIT == 128000
    assert DEFAULT_SAFE_TARGET_CHARS == 120000


def test_compact_json_parts_respect_128k_limit(tmp_path: Path) -> None:
    path = tmp_path / "large_nested.json"
    payload = {
        "nested": {
            f"K{i}": [{"j": j, "text": "x" * 600} for j in range(30)]
            for i in range(120)
        },
        "small": {"ok": True},
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    files = prepare_zip_files_for_char_limit([path], report_dir=tmp_path, max_chars=DEFAULT_ZIP_CHAR_LIMIT)
    assert path not in files
    part_files = [p for p in files if "_part" in p.name]
    assert part_files
    sizes = [len(p.read_text(encoding="utf-8")) for p in part_files]
    assert all(size <= DEFAULT_ZIP_CHAR_LIMIT for size in sizes)
    if len(sizes) > 1:
        assert max(sizes) > 100000
    for file_path in files:
        if file_path.suffix == ".json":
            json.loads(file_path.read_text(encoding="utf-8"))
    validate_zip_input_char_limit(files, max_chars=DEFAULT_ZIP_CHAR_LIMIT)
