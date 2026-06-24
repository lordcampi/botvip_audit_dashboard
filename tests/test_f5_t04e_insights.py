from __future__ import annotations

from src.f5_t04e_insights import build_f5_t04e_outputs, build_loss_contribution


def test_loss_contribution_uses_only_signal_rows() -> None:
    facts = [
        {"record_type": "signal", "signal_id": "1", "symbol": "BTC", "side": "LONG", "setup_type": "OFA", "net_r": -1.0, "exit_reason": "stop_loss", "real_stop_loss_hit": True, "data_gap_events": 0},
        {"record_type": "signal", "signal_id": "2", "symbol": "ETH", "side": "LONG", "setup_type": "OFA", "net_r": 2.0, "primary_tp_hit": True, "data_gap_events": 0},
        {"record_type": "candidate", "candidate_id": 3, "symbol": "BTC", "net_r": -99.0},
    ]
    result = build_loss_contribution(facts=facts)
    assert result["official_signal_denominator"] == 2
    assert result["total_loss_abs_r"] == 1.0
    assert result["total_net_r"] == 1.0


def test_loss_contribution_segments_by_outcome() -> None:
    facts = [
        {"record_type": "signal", "signal_id": "1", "symbol": "BTC", "net_r": -1.0, "real_stop_loss_hit": True},
        {"record_type": "signal", "signal_id": "2", "symbol": "BTC", "net_r": -0.2, "no_progress_exit": True},
    ]
    result = build_loss_contribution(facts=facts)
    outcome_segments = result["by_dimension"]["outcome"]["segments"]
    names = {row["segment"] for row in outcome_segments}
    assert "real_stop_loss_hit" in names
    assert "no_progress_exit" in names


def test_ai_insight_summary_contains_guardrails() -> None:
    outputs = build_f5_t04e_outputs(
        facts=[],
        lifecycle={"signals_total": 0, "sent_to_telegram": 0, "primary_tp_hit": 0, "real_stop_loss_hit": 0, "no_progress_exit": 0, "candidates_total": 0, "near_miss_candidates": 0},
        blocked_summary={},
        t02_diagnostics={},
        f5_t04bcd_sections={"zone_mapping_quality": {"unknown_zone_rate": 0}, "entity_scope_reconciliation": {"entities": {"official_signals": {"row_count_from_facts": 0}}, "do_not_double_count": []}},
    )
    assert set(outputs) == {"loss_contribution", "ai_insight_summary"}
    assert outputs["ai_insight_summary"]["read_only"] is True
    assert "No automatic changes." in outputs["ai_insight_summary"]["guardrails"]
