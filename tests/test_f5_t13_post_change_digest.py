from __future__ import annotations

import json
import pytest
from src.f5_t13_post_change_digest import (
    MAX_DIGEST_CHARS,
    POST_CHANGE_CUTOFF,
    F5_T13_SCHEMA_VERSION,
    F5_T13_DIGEST_JSON_FILENAME,
    F5_T13_DIGEST_MD_FILENAME,
    build_f5_t13_post_change_digest,
)


def _make_signal(signal_id: str, created_at: str, sent: bool = True, net_r: float | None = None, symbol: str = "BTC", side: str = "LONG", **kwargs) -> dict:
    row = {
        "record_type": "signal",
        "signal_id": signal_id,
        "created_at": created_at,
        "sent_to_telegram": sent,
        "telegram_notified": sent,
        "symbol": symbol,
        "side": side,
        "net_r": net_r,
        "mfe": kwargs.get("mfe"),
        "mae": kwargs.get("mae"),
        "closed_at": kwargs.get("closed_at"),
        "primary_tp_hit": kwargs.get("primary_tp_hit", False),
        "real_stop_loss_hit": kwargs.get("real_stop_loss_hit", False),
        "no_progress_exit": kwargs.get("no_progress_exit", False),
        "mfe_stall_exit": kwargs.get("mfe_stall_exit", False),
        "time_stop_exit": kwargs.get("time_stop_exit", False),
        "breakeven_stop_hit": kwargs.get("breakeven_stop_hit", False),
        "runner_breakeven_stop_hit": kwargs.get("runner_breakeven_stop_hit", False),
        "cancelled": kwargs.get("cancelled", False),
        "killzone": kwargs.get("killzone"),
        "market_regime": kwargs.get("market_regime"),
    }
    return row


def _make_candidate(candidate_id: str, created_at: str, blocked: bool = False, blocked_reason: str = "", **kwargs) -> dict:
    row = {
        "record_type": "candidate",
        "candidate_id": candidate_id,
        "created_at": created_at,
        "blocked": blocked,
        "blocked_reason": blocked_reason,
        "net_r": kwargs.get("net_r"),
        "mfe": kwargs.get("mfe"),
        "mae": kwargs.get("mae"),
    }
    return row


# ---------------------------------------------------------------------------
# Test 1: Digest generates without error with minimal data
# ---------------------------------------------------------------------------


def test_digest_generates_with_empty_data() -> None:
    result = build_f5_t13_post_change_digest(facts=[])
    digest = result["json"]
    assert digest["read_only"] is True
    assert digest["schema_version"] == F5_T13_SCHEMA_VERSION
    assert "F5_T13" in result["markdown"]


# ---------------------------------------------------------------------------
# Test 2: Block F5_T13 appears with schema_version
# ---------------------------------------------------------------------------


def test_digest_schema_version_present() -> None:
    facts = [_make_signal("s1", "2026-07-01 16:00:00", net_r=1.0)]
    result = build_f5_t13_post_change_digest(facts=facts)
    digest = result["json"]
    assert digest["schema_version"] == F5_T13_SCHEMA_VERSION
    assert digest["section"] == "f5_t13_post_change_strategy_impact_digest"
    assert digest["read_only"] is True


# ---------------------------------------------------------------------------
# Test 3: Post-change cutoff filters correctly (pre-change excluded)
# ---------------------------------------------------------------------------


def test_cutoff_filters_pre_change_data() -> None:
    facts = [
        _make_signal("s_pre", "2026-07-01 14:00:00", net_r=1.0),  # Before cutoff
        _make_signal("s_post", "2026-07-01 16:00:00", net_r=-1.0),  # After cutoff
    ]
    result = build_f5_t13_post_change_digest(facts=facts)
    digest = result["json"]
    sections = digest.get("sections", {})
    core = sections.get("B_core_summary", {})
    # post_change_sent_to_telegram should be 1, not 2
    assert core.get("post_change_sent_to_telegram") == 1
    assert core.get("post_change_official_signals") == 1


# ---------------------------------------------------------------------------
# Test 4: Candidate snapshots NOT counted as trades
# ---------------------------------------------------------------------------


def test_candidates_not_counted_as_trades() -> None:
    facts = [
        _make_signal("s1", "2026-07-01 16:00:00", net_r=1.0),
        _make_candidate("c1", "2026-07-01 16:00:00", blocked=True, blocked_reason="rvol_low"),
        _make_candidate("c2", "2026-07-01 16:00:00", blocked=False),
    ]
    result = build_f5_t13_post_change_digest(facts=facts)
    digest = result["json"]
    sections = digest.get("sections", {})
    core = sections.get("B_core_summary", {})
    # Only 1 signal, not 3 trades
    assert core.get("post_change_official_signals") == 1
    assert core.get("post_change_sent_to_telegram") == 1
    denom = sections.get("denominators", {})
    assert denom.get("candidate_snapshots_total") == 2


# ---------------------------------------------------------------------------
# Test 5: sent_to_telegram is primary denominator
# ---------------------------------------------------------------------------


def test_sent_to_telegram_is_primary_denominator() -> None:
    facts = [
        _make_signal("s1", "2026-07-01 16:00:00", sent=True, net_r=2.0),
        _make_signal("s2", "2026-07-01 17:00:00", sent=False, net_r=-1.0),  # Not sent
        _make_signal("s3", "2026-07-01 18:00:00", sent=True, net_r=1.0),
    ]
    result = build_f5_t13_post_change_digest(facts=facts)
    digest = result["json"]
    sections = digest.get("sections", {})
    core = sections.get("B_core_summary", {})
    denom = sections.get("denominators", {})
    # 3 official signals, but only 2 sent
    assert core.get("post_change_official_signals") == 3
    assert core.get("post_change_sent_to_telegram") == 2
    assert denom.get("sent_to_telegram") == 2
    assert core.get("denominator_used") == "sent_to_telegram"
    # Metrics should reflect only the 2 sent signals
    assert core.get("gross_profit_r") == 3.0


# ---------------------------------------------------------------------------
# Test 6: No risk_context_gate events shows 0 without failing
# ---------------------------------------------------------------------------


def test_no_risk_context_gate_events_handled_gracefully() -> None:
    facts = [
        _make_signal("s1", "2026-07-01 16:00:00", net_r=0.5),
        _make_candidate("c1", "2026-07-01 16:00:00", blocked=True, blocked_reason="rvol_low"),
    ]
    result = build_f5_t13_post_change_digest(facts=facts)
    digest = result["json"]
    sections = digest.get("sections", {})
    rc = sections.get("L_risk_context_gate", {})
    assert rc.get("risk_context_gate_events_found") == 0
    assert rc.get("risk_context_gate_blocked_count") == 0
    assert "interpretation" in rc
    # Markdown shows "Events found: 0" not the raw JSON key
    assert "Events found: 0" in result["markdown"]


# ---------------------------------------------------------------------------
# Test 7: Digest size < 95,000 characters
# ---------------------------------------------------------------------------


def test_digest_size_within_limits() -> None:
    facts = []
    for i in range(50):
        facts.append(_make_signal(
            f"s{i}", f"2026-07-01 16:00:{i:02d}",
            net_r=1.0 if i % 2 == 0 else -0.5,
            symbol=f"SYM{i % 15}",
            side="LONG" if i % 2 == 0 else "SHORT",
            mfe=0.3,
            mae=0.1,
            closed_at=f"2026-07-01 18:00:{i:02d}",
            primary_tp_hit=(i % 2 == 0),
            real_stop_loss_hit=(i % 3 == 0),
            no_progress_exit=(i % 4 == 0),
            time_stop_exit=(i % 5 == 0),
            killzone=(i % 2 == 0),
            market_regime="TRENDING" if i % 2 == 0 else "RANGING",
        ))
    for i in range(100):
        facts.append(_make_candidate(
            f"c{i}", f"2026-07-01 16:00:{i:02d}",
            blocked=(i % 3 == 0),
            blocked_reason="ofa_live_rvol_too_low" if i % 3 == 0 else "",
        ))

    result = build_f5_t13_post_change_digest(facts=facts)
    digest = result["json"]
    json_str = json.dumps(digest, ensure_ascii=False, default=str)
    assert len(json_str) <= MAX_DIGEST_CHARS, f"Digest size {len(json_str)} exceeds {MAX_DIGEST_CHARS}"


# ---------------------------------------------------------------------------
# Test 8: read_only is True
# ---------------------------------------------------------------------------


def test_read_only_true() -> None:
    result = build_f5_t13_post_change_digest(facts=[])
    assert result["json"]["read_only"] is True


# ---------------------------------------------------------------------------
# Test 9: JSON parseable (round-trip)
# ---------------------------------------------------------------------------


def test_json_round_trip() -> None:
    facts = [
        _make_signal("s1", "2026-07-01 16:00:00", net_r=1.5, symbol="BTC", side="LONG"),
        _make_signal("s2", "2026-07-01 17:00:00", net_r=-0.8, symbol="ETH", side="SHORT"),
    ]
    result = build_f5_t13_post_change_digest(facts=facts)
    json_str = json.dumps(result["json"], ensure_ascii=False, default=str)
    parsed = json.loads(json_str)
    assert parsed["read_only"] is True
    assert parsed["schema_version"] == F5_T13_SCHEMA_VERSION


# ---------------------------------------------------------------------------
# Test 10: Empty post-change data doesn't break digest
# ---------------------------------------------------------------------------


def test_empty_post_change_data() -> None:
    facts = [
        _make_signal("s_pre", "2026-07-01 10:00:00", net_r=1.0),  # Before cutoff
        _make_candidate("c_pre", "2026-07-01 10:00:00", blocked=True, blocked_reason="rvol_low"),
    ]
    result = build_f5_t13_post_change_digest(facts=facts)
    digest = result["json"]
    assert digest.get("post_change_data_available") is False
    assert "No post-change data" in result["markdown"]
    # Should not have sections key
    assert "sections" not in digest


# ---------------------------------------------------------------------------
# Test 11: telegram_notified mismatch warning
# ---------------------------------------------------------------------------


def test_telegram_notified_mismatch_warning() -> None:
    facts = [
        _make_signal("s1", "2026-07-01 16:00:00", sent=True, net_r=1.0),
    ]
    # Override telegram_notified to create mismatch
    facts[0]["telegram_notified"] = False
    result = build_f5_t13_post_change_digest(facts=facts)
    digest = result["json"]
    denom = digest.get("sections", {}).get("denominators", {})
    assert denom.get("telegram_notified_mismatch_note") is not None
    assert "WARNING" in str(denom.get("telegram_notified_mismatch_note", ""))


# ---------------------------------------------------------------------------
# Test 12: Guard value from guard_matrix works (optional param)
# ---------------------------------------------------------------------------


def test_guard_value_from_guard_matrix() -> None:
    facts = [
        _make_signal("s1", "2026-07-01 16:00:00", net_r=2.0),
        _make_candidate("c1", "2026-07-01 16:00:00", blocked=True, blocked_reason="ofa_live_rvol_too_low"),
    ]
    guard_matrix = {
        "matrix_by_guard": {
            "ofa_live_rvol_too_low": {
                "rows": 5,
                "avoided_losses_r": 3.0,
                "missed_winners_r": 1.0,
                "net_guard_value_r": 2.0,
                "profit_factor_if_allowed": 0.5,
            }
        }
    }
    result = build_f5_t13_post_change_digest(facts=facts, guard_matrix=guard_matrix)
    digest = result["json"]
    guard_val = digest.get("sections", {}).get("K_guard_value", {})
    guards = guard_val.get("guards", [])
    assert len(guards) >= 1
    assert guards[0]["guard_name"] == "ofa_live_rvol_too_low"
    assert guards[0]["net_guard_value_r"] == 2.0


# ---------------------------------------------------------------------------
# Test 13: Winrate calculation
# ---------------------------------------------------------------------------


def test_winrate_calculation() -> None:
    facts = [
        _make_signal("s1", "2026-07-01 16:00:00", net_r=1.0),
        _make_signal("s2", "2026-07-01 17:00:00", net_r=1.5),
        _make_signal("s3", "2026-07-01 18:00:00", net_r=-1.0),
    ]
    result = build_f5_t13_post_change_digest(facts=facts)
    core = result["json"]["sections"]["B_core_summary"]
    assert core["winners"] == 2
    assert core["losers"] == 1
    assert core["winrate"] == pytest.approx(2 / 3, 0.01)


# ---------------------------------------------------------------------------
# Test 14: Pre/post comparison when both available
# ---------------------------------------------------------------------------


def test_pre_post_comparison_both_available() -> None:
    facts = [
        _make_signal("s_pre1", "2026-07-01 10:00:00", net_r=2.0),
        _make_signal("s_pre2", "2026-07-01 12:00:00", net_r=-1.0),
        _make_signal("s_post1", "2026-07-01 16:00:00", net_r=1.5),
        _make_signal("s_post2", "2026-07-01 17:00:00", net_r=0.5),
        _make_signal("s_post3", "2026-07-01 18:00:00", net_r=-0.5),
    ]
    result = build_f5_t13_post_change_digest(facts=facts)
    pp = result["json"]["sections"]["C_pre_post_comparison"]
    assert pp["pre_change"]["available"] is True
    assert pp["pre_change"]["sent_to_telegram"] == 2
    assert pp["post_change"]["available"] is True
    assert pp["post_change"]["sent_to_telegram"] == 3


# ---------------------------------------------------------------------------
# Test 15: All files present (Markdown + JSON)
# ---------------------------------------------------------------------------


def test_both_outputs_generated() -> None:
    facts = [_make_signal("s1", "2026-07-01 16:00:00", net_r=1.0)]
    result = build_f5_t13_post_change_digest(facts=facts)
    assert "json" in result
    assert "markdown" in result
    assert isinstance(result["json"], dict)
    assert isinstance(result["markdown"], str)
    assert "F5_T13" in result["markdown"]


# ---------------------------------------------------------------------------
# Test 16: Markdown output contains all major sections
# ---------------------------------------------------------------------------


def test_markdown_contains_all_sections() -> None:
    facts = []
    for i in range(5):
        facts.append(_make_signal(
            f"s{i}", f"2026-07-01 16:00:{i:02d}",
            net_r=1.0, symbol="BTC", side="LONG",
            primary_tp_hit=True, closed_at=f"2026-07-01 18:00:{i:02d}",
            killzone=True, market_regime="TRENDING",
        ))
    result = build_f5_t13_post_change_digest(facts=facts)
    md = result["markdown"]
    assert "### Denominators" in md
    assert "### Core Metrics" in md
    assert "### Pre vs Post Comparison" in md
    assert "### By Direction" in md
    assert "### Exit Reasons" in md
    assert "### Interpretation & Recommendation" in md