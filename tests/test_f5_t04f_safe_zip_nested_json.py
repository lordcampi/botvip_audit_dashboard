from __future__ import annotations

import json
from pathlib import Path

from src.safe_zip_chunking import prepare_zip_files_for_char_limit, validate_zip_input_char_limit


def test_nested_large_json_dict_is_split_without_large_metadata_prefix(tmp_path: Path) -> None:
    path = tmp_path / "12_t02_no_progress_reclaim_zone_pf.json"
    payload = {
        "schema_version": "x",
        "small_guardrails": ["a", "b"],
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
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    files = prepare_zip_files_for_char_limit([path], report_dir=tmp_path, max_chars=8000)

    assert path not in files
    assert any(p.name == "12_t02_no_progress_reclaim_zone_pf_index.json" for p in files)
    part_files = [p for p in files if "_part" in p.name]
    assert len(part_files) > 2
    for part in part_files:
        data = json.loads(part.read_text(encoding="utf-8"))
        assert data["schema_version"] == "f5_t04a_json_nested_part_v1"
        assert "json_path" in data
        assert len(part.read_text(encoding="utf-8")) <= 8000
    validate_zip_input_char_limit(files, max_chars=8000)
