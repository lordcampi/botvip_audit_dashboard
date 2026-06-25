from __future__ import annotations

from src.f5_t10_super_digest import build_f5_t09_super_digest


def test_super_digest_limits_large_inputs_and_keeps_policy() -> None:
    big_rows = [{"signal_id": i, "text": "x" * 1000, "visual_contradiction": i % 2 == 0} for i in range(100)]
    result = build_f5_t09_super_digest(
        lifecycle_reconciliation={"schema_version": "a", "summary": {"signals_total": 100, "visual_contradiction_count": 5, "official_result_counts": {"WIN_PROTECTED": 10}}, "rows": big_rows},
        no_progress_v3={"schema_version": "b", "official_no_progress_count": 3, "bucket_counts": {"mfe_zero": 2}, "representative_examples": big_rows},
        mfe_capture={"schema_version": "c", "closed_rows_evaluated": 9, "data_quality": {"mfe_known": 8}, "mfe_capture_leak_examples": big_rows},
        guard_matrix={"schema_version": "d", "candidate_shadow_denominator": 20, "matched_guard_rows": 7, "matrix_by_guard": {"g": {"rows": 7, "avoided_losses_r": 2, "missed_winners_r": 1, "net_guard_value_r": 1}}},
        low_vol={"schema_version": "e", "official_signals": {"denominator": 1, "low_vol_rows": 1}, "candidate_shadow": {"denominator": 2, "low_vol_rows": 2}},
        copyability={"schema_version": "f", "buckets": ["lt_70"], "candidate_shadow": {"lt_70": {"rows": 1}}},
        atr_extension={"schema_version": "g", "candidate_shadow": {"rows_with_atr_extension": 4}},
        btc_bias={"schema_version": "h", "definition": "x", "candidate_shadow": {"conflict_rows": 5}},
        symbol_alpha={"schema_version": "i", "target_symbols": ["SUI"], "matched_rows": 6, "ranking": {"alpha_potential_symbols": big_rows, "noisy_or_negative_symbols": []}},
    )
    digest = result["json"]
    assert digest["read_only"] is True
    assert "20_no_progress_root_cause_v3.json" in digest["files_policy"]["full_server_files_excluded_from_ai_zip"]
    assert len(digest["sections"]["no_progress_root_cause_v3"]["top_loss_contributors"]) <= 10
    assert "F5_T09 AI Super Digest" in result["markdown"]
