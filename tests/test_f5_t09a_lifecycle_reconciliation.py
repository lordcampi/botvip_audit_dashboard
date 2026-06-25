from __future__ import annotations

from src.f5_t09a_lifecycle_reconciliation import build_telegram_lifecycle_reconciliation_v2


def test_win_protected_runner_time_stop_negative_is_visual_contradiction() -> None:
    facts = [{
        "record_type": "signal",
        "signal_id": "244",
        "symbol": "NEAR/USDT:USDT",
        "side": "SHORT",
        "setup_type": "OFA_SWEEP_RECLAIM",
        "created_at": "2026-06-24 10:00:00",
        "status": "CLOSED",
        "exit_reason": "time_stop",
        "primary_tp_hit": True,
        "official_result": "WIN",
        "official_result_locked": True,
        "time_stop_exit": True,
        "net_r": -0.17,
        "event_types": "PRIMARY_TP_HIT,BREAKEVEN_ARMED,SL_MOVED_TO_BREAKEVEN,TIME_STOP_EXIT",
    }]
    events = [
        {"signal_id": "244", "event_type": "PRIMARY_TP_HIT"},
        {"signal_id": "244", "event_type": "TIME_STOP_EXIT"},
    ]
    result = build_telegram_lifecycle_reconciliation_v2(facts=facts, events=events, signals=[])
    assert result["summary"]["visual_contradiction_count"] == 1
    row = result["rows"][0]
    assert row["official_result"] == "WIN_PROTECTED"
    assert row["final_public_result"] == "WIN_PROTECTED"
    assert row["runner_result"] == "RUNNER_TIME_STOP_NEGATIVE_VISUAL_RISK"
    assert row["visual_contradiction"] is True
    assert "must not be counted as official loss" in row["interpretation_note"]


def test_runner_breakeven_after_primary_tp_keeps_win_protected() -> None:
    facts = [{
        "record_type": "signal",
        "signal_id": "425",
        "symbol": "NEAR/USDT:USDT",
        "side": "SHORT",
        "status": "WON",
        "exit_reason": "runner_breakeven_stop",
        "primary_tp_hit": True,
        "official_result_locked": True,
        "runner_breakeven_stop_hit": True,
        "net_r": 0.4,
    }]
    result = build_telegram_lifecycle_reconciliation_v2(facts=facts, events=[], signals=[])
    row = result["rows"][0]
    assert row["official_result"] == "WIN_PROTECTED"
    assert row["runner_result"] == "RUNNER_BREAKEVEN_STOP"
    assert row["final_public_result"] == "WIN_PROTECTED"
    assert row["visual_contradiction"] is False


def test_official_loss_without_primary_tp_is_not_win_protected() -> None:
    facts = [{
        "record_type": "signal",
        "signal_id": "427",
        "symbol": "SUI/USDT:USDT",
        "side": "SHORT",
        "status": "LOST",
        "exit_reason": "stop_loss",
        "primary_tp_hit": False,
        "real_stop_loss_hit": True,
        "net_r": -1.3,
    }]
    result = build_telegram_lifecycle_reconciliation_v2(facts=facts, events=[], signals=[])
    assert result["summary"]["official_result_counts"]["LOSS_OFFICIAL"] == 1
    assert result["summary"]["final_public_result_counts"]["LOSS_OFFICIAL"] == 1
    assert result["summary"]["visual_contradiction_count"] == 0


def test_candidates_are_not_counted_as_official_signals() -> None:
    facts = [
        {"record_type": "candidate", "candidate_id": "1", "primary_tp_hit": True},
        {"record_type": "signal", "signal_id": "1", "status": "PENDING"},
    ]
    result = build_telegram_lifecycle_reconciliation_v2(facts=facts, events=[], signals=[])
    assert result["summary"]["signals_total"] == 1
