from __future__ import annotations

from src.f5_t04i_slim_sections import F5_T03B_SLIM_FILENAME, build_f5_t03b_slim_sections


def test_slim_reduces_large_data_quality_section() -> None:
    full = {
        "schema_version": "full",
        "source": "test",
        "read_only": True,
        "data_quality_score_by_signal": {
            "rows": [
                {"signal_id": i, "symbol": "BTC", "text": "x" * 1000}
                for i in range(500)
            ]
        },
        "no_progress_diagnostics_v2": {"total": 3, "sample": [{"a": 1}]},
    }
    slim = build_f5_t03b_slim_sections(full)
    assert slim["schema_version"] == "f5_t04i_slim_f5_t03b_sections_v1"
    assert slim["read_only"] is True
    assert slim["full_file_policy"]["full_file_excluded_from_ai_zip"] is True
    rows = slim["sections"]["data_quality_score_by_signal"]["rows"]
    assert rows["type"] == "list_slimmed"
    assert rows["sample_count"] <= 20
    assert rows["omitted_count"] > 0


def test_filename_constant() -> None:
    assert F5_T03B_SLIM_FILENAME == "f5_t03b_integration_sections_slim.json"
