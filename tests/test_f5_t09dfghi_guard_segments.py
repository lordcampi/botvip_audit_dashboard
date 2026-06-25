from __future__ import annotations

from src.f5_t09dfghi_guard_segments import build_f5_t09dfghi_guard_filter_outputs


def test_guard_matrix_computes_avoided_and_missed_value_without_counting_candidates_as_signals() -> None:
    facts = [
        {"record_type": "candidate", "candidate_id": "1", "symbol": "ETH", "side": "LONG", "blocked_reason": "live_guard:ofa_live_rvol_too_low", "hypothetical_result": "lost", "mae": -1.0, "mfe": 0.1},
        {"record_type": "candidate", "candidate_id": "2", "symbol": "ETH", "side": "LONG", "blocked_reason": "live_guard:ofa_live_rvol_too_low", "hypothetical_result": "won", "net_rr": 1.4, "mfe": 1.6},
        {"record_type": "signal", "signal_id": "9", "symbol": "BTC", "primary_tp_hit": True, "net_r": 1.0},
    ]
    output = build_f5_t09dfghi_guard_filter_outputs(facts=facts, events=[], signals=[], candidates=[])
    matrix = output["guard_shadow_outcome_matrix"]
    assert matrix["candidate_shadow_denominator"] == 2
    assert matrix["matched_guard_rows"] == 2
    guard = matrix["matrix_by_guard"]["ofa_live_rvol_too_low"]
    assert guard["avoided_losses_r"] == 1.0
    assert guard["missed_winners_r"] == 1.4
    assert guard["net_guard_value_r"] == -0.4


def test_low_vol_and_btc_bias_sections_keep_official_and_shadow_separate() -> None:
    facts = [
        {"record_type": "signal", "signal_id": "11", "symbol": "NEAR", "side": "LONG", "market_regime": "LOW_VOLATILITY", "btc_trend": "BEARISH", "primary_tp_hit": True, "net_r": 1.2, "reclaim_ok": True},
        {"record_type": "candidate", "candidate_id": "22", "symbol": "SUI", "side": "SHORT", "market_regime": "LOW_VOLATILITY", "btc_trend": "BULLISH", "blocked_reason": "ofa_low_vol_shadow_only", "hypothetical_result": "lost", "reclaim_ok": False},
    ]
    output = build_f5_t09dfghi_guard_filter_outputs(facts=facts, events=[], signals=[], candidates=[])
    low_vol = output["low_vol_winners_vs_losers"]
    assert low_vol["official_signals"]["low_vol_rows"] == 1
    assert low_vol["candidate_shadow"]["low_vol_rows"] == 1
    btc = output["btc_bias_conflict_reclaim_quality"]
    assert btc["official_signals"]["conflict_rows"] == 1
    assert btc["candidate_shadow"]["conflict_rows"] == 1


def test_copyability_and_atr_use_candidate_metadata_json() -> None:
    facts = [{"record_type": "candidate", "candidate_id": "5", "symbol": "BTC", "side": "LONG", "blocked_reason": "copyability_rr_degraded", "hypothetical_result": "won"}]
    candidates = [{"id": "5", "metadata_json": '{"copyability_score": 74, "atr_extension": 1.8, "mfe_first_3m_r": 0.22, "net_rr": 1.1}'}]
    output = build_f5_t09dfghi_guard_filter_outputs(facts=facts, events=[], signals=[], candidates=candidates)
    copyability = output["copyability_score_bucket_outcome"]["candidate_shadow"]
    assert copyability["70_74"]["rows"] == 1
    atr = output["atr_extension_shadow_outcomes"]["candidate_shadow"]
    assert atr["rows_with_atr_extension"] == 1
    assert atr["by_outcome"]["hypothetical_win"]["missed_winners_r"] == 1.1
