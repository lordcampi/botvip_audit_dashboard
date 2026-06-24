from __future__ import annotations

import json
from pathlib import Path

from src.safe_zip_chunking import prepare_zip_files_for_char_limit, validate_zip_input_char_limit


def _big_payload() -> dict:
    return {
        "schema_version": "x",
        "small_1": {"a": 1},
        "small_2": {"b": 2},
        "profit_factor_diagnostics": {
            "by_symbol": {
                f"SYM{i}": {"rows": [{"signal_id": j, "text": "x" * 200} for j in range(15)]}
                for i in range(80)
            }
        },
        "data_quality_score_by_signal": {
            "sample_bad": [{"signal_id": i, "text": "y" * 250} for i in range(300)]
        },
    }


def test_compact_nested_json_uses_fewer_reasonably_full_parts(tmp_path: Path) -> None:
    path = tmp_path / "12_t02_no_progress_reclaim_zone_pf.json"
    path.write_text(json.dumps(_big_payload(), indent=2), encoding="utf-8")

    files = prepare_zip_files_for_char_limit([path], report_dir=tmp_path, max_chars=8000)

    assert path not in files
    assert any(p.name == "12_t02_no_progress_reclaim_zone_pf_index.json" for p in files)
    part_files = [p for p in files if "_part" in p.name]
    assert part_files
    sizes = [len(p.read_text(encoding="utf-8")) for p in part_files]
    assert all(size <= 8000 for size in sizes)
    if len(sizes) > 1:
        assert min(sizes[:-1]) >= 5000
    for part in part_files:
        data = json.loads(part.read_text(encoding="utf-8"))
        assert data["schema_version"] == "f5_t04a_json_compact_part_v1"
        assert data["content_type"] == "json_compact_entries"
        assert data["entries"]
    validate_zip_input_char_limit(files, max_chars=8000)
