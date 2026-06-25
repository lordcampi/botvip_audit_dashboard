from __future__ import annotations

from src.f5_t09bc_no_progress_mfe import build_f5_t09bc_no_progress_mfe_outputs, build_mfe_capture_efficiency_by_exit_reason, build_no_progress_root_cause_v3


def test_no_progress_v3_uses_metrics_json_mfe_and_multilabel_buckets() -> None:
    facts = [{"record_type": "signal", "signal_id": "101", "symbol": "NEAR/USDT:USDT", "side": "LONG", "market_regime": "LOW_VOLATILITY", "btc_trend": "BEARISH", "exit_reason": "no_progress", "no_progress_exit": True, "net_r": -0.22, "time_to_entry_minutes": 4.5, "time_to_close_minutes": 18, "score": 78, "sent_to_telegram": True, "data_gap_events": 0}]
    signals = [{"id": "101", "metrics_json": '{"mfe_r": 0.12, "mae_r": -0.25, "copyability_score": 72, "reclaim_score": 1}'}]
    result = build_no_progress_root_cause_v3(facts=facts, events=[], signals=signals)
    row = result["representative_examples"][0]
    assert result["read_only"] is True
    assert result["official_no_progress_count"] == 1
    assert row["mfe_r"] == 0.12
    for bucket in ["mfe_lt_0_15R", "btc_bias_conflict", "low_vol_no_expansion", "copyability_degraded", "entered_too_late", "reclaim_score_1", "adverse_first_minutes", "spread_sensitive_symbol"]:
        assert bucket in row["root_cause_buckets"]


def test_mfe_capture_efficiency_computes_ratio_by_exit_reason() -> None:
    facts = [
        {"record_type": "signal", "signal_id": "201", "symbol": "ETH/USDT:USDT", "side": "SHORT", "status": "CLOSED", "exit_reason": "time_stop", "time_stop_exit": True, "net_r": 0.20, "mfe": 0.80, "mae": -0.10, "time_to_close_minutes": 20},
        {"record_type": "signal", "signal_id": "202", "symbol": "BTC/USDT:USDT", "side": "LONG", "status": "LOST", "exit_reason": "stop_loss", "real_stop_loss_hit": True, "net_r": -1.0, "mfe": 0.40, "mae": -1.0, "time_to_close_minutes": 7},
    ]
    result = build_mfe_capture_efficiency_by_exit_reason(facts=facts, events=[], signals=[])
    rows = {row["signal_id"]: row for row in result["rows"]}
    assert result["closed_rows_evaluated"] == 2
    assert rows["201"]["capture_ratio"] == 0.25
    assert rows["202"]["capture_ratio"] == -2.5
    assert result["segments"]["by_exit_reason"]["time_stop"]["count"] == 1
    assert result["data_quality"]["capture_ratio_known"] == 2


def test_builder_returns_both_sections_and_ignores_candidates_as_denominator() -> None:
    facts = [{"record_type": "candidate", "candidate_id": "c1", "mfe": 99, "mae": -99}, {"record_type": "signal", "signal_id": "1", "exit_reason": "no_progress", "no_progress_exit": True}]
    result = build_f5_t09bc_no_progress_mfe_outputs(facts=facts, events=[], signals=[], candidates=[])
    assert set(result) == {"no_progress_root_cause_v3", "mfe_capture_efficiency_by_exit_reason"}
    assert result["no_progress_root_cause_v3"]["official_signal_denominator"] == 1
    assert result["mfe_capture_efficiency_by_exit_reason"]["official_signal_denominator"] == 1
