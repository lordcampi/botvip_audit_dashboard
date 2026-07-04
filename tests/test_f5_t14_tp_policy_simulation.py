from __future__ import annotations

import json
import pytest
from src.f5_t14_tp_policy_simulation import (
    MAX_DIGEST_CHARS,
    POST_CHANGE_CUTOFF,
    F5_T14_SCHEMA_VERSION,
    F5_T14_DIGEST_JSON_FILENAME,
    F5_T14_DIGEST_MD_FILENAME,
    build_f5_t14_tp_policy_simulation,
    _estimate_tp1_r,
    _estimate_mfe_r,
    _estimate_mae_r,
    _is_true,
    _core_metrics,
)


def _make_signal(signal_id: str, created_at: str, sent: bool = True, net_r: float | None = None, symbol: str = "BTC", side: str = "LONG", **kwargs) -> dict:
    row = {
        "record_type": "signal",
        "signal_id": signal_id,
        "created_at": created_at,
        "sent_to_telegram": sent,
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
        "primary_tp_distance": kwargs.get("primary_tp_distance"),
        "sl_distance": kwargs.get("sl_distance"),
        "entry_price": kwargs.get("entry_price"),
        "sl_price": kwargs.get("sl_price"),
        "exit_reason": kwargs.get("exit_reason"),
    }
    return row


def _make_candidate(candidate_id: str, created_at: str, **kwargs) -> dict:
    row = {
        "record_type": "candidate",
        "candidate_id": candidate_id,
        "created_at": created_at,
        "blocked": kwargs.get("blocked", False),
        "blocked_reason": kwargs.get("blocked_reason", ""),
        "net_r": kwargs.get("net_r"),
        "mfe": kwargs.get("mfe"),
        "mae": kwargs.get("mae"),
    }
    return row


# ---------------------------------------------------------------------------
# Test 1: Digest generates without error with empty data
# ---------------------------------------------------------------------------


def test_digest_generates_with_empty_data() -> None:
    result = build_f5_t14_tp_policy_simulation(facts=[])
    digest = result["json"]
    assert digest["read_only"] is True
    assert digest["schema_version"] == F5_T14_SCHEMA_VERSION
    assert "F5_T14" in result["markdown"]


# ---------------------------------------------------------------------------
# Test 2: read_only is True
# ---------------------------------------------------------------------------


def test_read_only_true() -> None:
    result = build_f5_t14_tp_policy_simulation(facts=[])
    assert result["json"]["read_only"] is True


# ---------------------------------------------------------------------------
# Test 3: Schema version present
# ---------------------------------------------------------------------------


def test_schema_version_present() -> None:
    facts = [_make_signal("s1", "2026-07-01 16:00:00", net_r=1.0)]
    result = build_f5_t14_tp_policy_simulation(facts=facts)
    digest = result["json"]
    assert digest["schema_version"] == F5_T14_SCHEMA_VERSION
    assert digest["section"] == "f5_t14_tp_policy_simulation"


# ---------------------------------------------------------------------------
# Test 4: Candidate snapshots NOT counted as trades
# ---------------------------------------------------------------------------


def test_candidates_not_counted_as_trades() -> None:
    facts = [
        _make_signal("s1", "2026-07-01 16:00:00", net_r=1.0),
        _make_candidate("c1", "2026-07-01 16:00:00", blocked=True, blocked_reason="rvol_low"),
    ]
    result = build_f5_t14_tp_policy_simulation(facts=facts)
    digest = result["json"]
    # Candidate should not appear in current policy signal count
    sections = digest.get("sections", {})
    current = sections.get("A_current_policy", {})
    assert current.get("total_sent") == 1  # Only the signal, not the candidate


# ---------------------------------------------------------------------------
# Test 5: sent_to_telegram is primary denominator
# ---------------------------------------------------------------------------


def test_sent_to_telegram_is_primary_denominator() -> None:
    facts = [
        _make_signal("s1", "2026-07-01 16:00:00", sent=True, net_r=2.0),
        _make_signal("s2", "2026-07-01 17:00:00", sent=False, net_r=-1.0),  # Not sent
        _make_signal("s3", "2026-07-01 18:00:00", sent=True, net_r=1.0),
    ]
    result = build_f5_t14_tp_policy_simulation(facts=facts)
    digest = result["json"]
    sections = digest.get("sections", {})
    current = sections.get("A_current_policy", {})
    assert current.get("total_sent") == 2  # Only 2 sent
    assert current.get("denominator") == "sent_to_telegram"


# ---------------------------------------------------------------------------
# Test 6: Cutoff filters pre-change signals
# ---------------------------------------------------------------------------


def test_cutoff_filters_pre_change() -> None:
    facts = [
        _make_signal("s_pre", "2026-07-01 14:00:00", net_r=1.0),  # Before cutoff
        _make_signal("s_post", "2026-07-01 16:00:00", net_r=-1.0),  # After cutoff
    ]
    result = build_f5_t14_tp_policy_simulation(facts=facts)
    digest = result["json"]
    sections = digest.get("sections", {})
    current = sections.get("A_current_policy", {})
    assert current.get("total_sent") == 1  # Only post-change
    assert current.get("post_change_data_available", True) is True


# ---------------------------------------------------------------------------
# Test 7: TP1 hit distribution with MFE data
# ---------------------------------------------------------------------------


def test_tp1_hit_distribution_with_mfe() -> None:
    facts = [
        _make_signal("s1", "2026-07-01 16:00:00", net_r=1.0, primary_tp_hit=True,
                     mfe=1.0, closed_at="2026-07-01 18:00:00",
                     primary_tp_distance=100, sl_distance=100),
        _make_signal("s2", "2026-07-01 17:00:00", net_r=1.0, primary_tp_hit=True,
                     mfe=1.5, closed_at="2026-07-01 19:00:00",
                     primary_tp_distance=100, sl_distance=100),
    ]
    result = build_f5_t14_tp_policy_simulation(facts=facts)
    digest = result["json"]
    sections = digest.get("sections", {})
    tp1 = sections.get("B_tp1_hit_analysis", {})
    assert tp1.get("count_tp1_hit") == 2
    assert tp1.get("available") is True
    assert tp1.get("reached_tp1_and_extended") >= 0  # Should classify
    # One extended, one only TP1
    assert tp1.get("reached_tp1_and_extended") + tp1.get("reached_tp1_only_no_extension") == 2


# ---------------------------------------------------------------------------
# Test 8: Single TP simulations with MFE
# ---------------------------------------------------------------------------


def test_single_tp_simulations_with_mfe() -> None:
    facts = [
        _make_signal("s1", "2026-07-01 16:00:00", net_r=1.0, primary_tp_hit=True,
                     mfe=1.5, closed_at="2026-07-01 18:00:00",
                     primary_tp_distance=100, sl_distance=100),
        _make_signal("s2", "2026-07-01 17:00:00", net_r=-1.0, real_stop_loss_hit=True,
                     mfe=0.3, mae=1.0, closed_at="2026-07-01 19:00:00",
                     primary_tp_distance=100, sl_distance=100),
    ]
    result = build_f5_t14_tp_policy_simulation(facts=facts)
    digest = result["json"]
    sections = digest.get("sections", {})
    sims = sections.get("C_single_tp_simulations", {})
    simulations = sims.get("simulations", [])
    assert len(simulations) == 5  # 5 multipliers
    # 1.10x should succeed for signal with MFE=1.5
    sim_1_10 = simulations[0]
    assert sim_1_10.get("multiplier") == 1.10
    assert sim_1_10.get("tp1_hit_would_reach_wider_tp") >= 0


# ---------------------------------------------------------------------------
# Test 9: Delayed BE simulations with MFE
# ---------------------------------------------------------------------------


def test_delayed_be_simulations_with_mfe() -> None:
    facts = [
        _make_signal("s1", "2026-07-01 16:00:00", net_r=1.0, primary_tp_hit=True,
                     mfe=1.5, closed_at="2026-07-01 18:00:00",
                     primary_tp_distance=100, sl_distance=100),
        _make_signal("s2", "2026-07-01 17:00:00", net_r=-1.0, real_stop_loss_hit=True,
                     mfe=0.8, mae=1.0, closed_at="2026-07-01 19:00:00",
                     primary_tp_distance=100, sl_distance=100),
    ]
    result = build_f5_t14_tp_policy_simulation(facts=facts)
    digest = result["json"]
    sections = digest.get("sections", {})
    be_sims = sections.get("D_delayed_be_simulations", {})
    simulations = be_sims.get("simulations", [])
    assert len(simulations) == 4  # 4 BE rules

    # MF>=1.10R delayed BE should save signal s2 (MFE=0.8 < 1.10, so NOT saved at 1.10R)
    # but at threshold=None (at_tp1_hit), s2 loses since it never hit TP1
    at_tp1 = simulations[0]  # at_tp1_hit
    assert at_tp1.get("label") == "at_tp1_hit"
    # Should have 1 win (s1 hit TP1) and 1 loss (s2) = 2 total
    assert at_tp1.get("wins") + at_tp1.get("losses") + at_tp1.get("breakeven") == 2


# ---------------------------------------------------------------------------
# Test 10: No MFE data handled gracefully
# ---------------------------------------------------------------------------


def test_no_mfe_data_handled_gracefully() -> None:
    facts = [
        _make_signal("s1", "2026-07-01 16:00:00", net_r=1.0, primary_tp_hit=True,
                     closed_at="2026-07-01 18:00:00"),
        _make_signal("s2", "2026-07-01 17:00:00", net_r=-1.0, real_stop_loss_hit=True,
                     closed_at="2026-07-01 19:00:00"),
    ]
    result = build_f5_t14_tp_policy_simulation(facts=facts)
    digest = result["json"]
    dq = digest.get("data_quality", {})
    assert dq.get("mfe_known_count") == 0
    assert dq.get("approximation_used") is True
    assert len(dq.get("limitations", [])) > 0

    # Simulations should still complete without error
    sections = digest.get("sections", {})
    sims = sections.get("C_single_tp_simulations", {})
    assert sims.get("available") is True
    assert "## F5_T14" in result["markdown"]


# ---------------------------------------------------------------------------
# Test 11: Post-TP1 extension buckets present
# ---------------------------------------------------------------------------


def test_post_tp1_extension_buckets() -> None:
    facts = [
        _make_signal("s1", "2026-07-01 16:00:00", net_r=1.0, primary_tp_hit=True,
                     mfe=0.95, closed_at="2026-07-01 18:00:00",
                     primary_tp_distance=100, sl_distance=100),  # No extension
        _make_signal("s2", "2026-07-01 17:00:00", net_r=1.5, primary_tp_hit=True,
                     mfe=1.5, closed_at="2026-07-01 19:00:00",
                     primary_tp_distance=100, sl_distance=100),  # +0.50R extension
    ]
    result = build_f5_t14_tp_policy_simulation(facts=facts)
    digest = result["json"]
    sections = digest.get("sections", {})
    ext = sections.get("E_post_tp1_extension_distribution", {})
    assert ext.get("available") is True
    buckets = ext.get("buckets", [])
    assert len(buckets) == 6
    # Sum of bucket counts should equal mfe_known_for_extension
    total_in_buckets = sum(b.get("count", 0) for b in buckets)
    assert total_in_buckets == ext.get("mfe_known_for_extension", 0)


# ---------------------------------------------------------------------------
# Test 12: Segment breakdowns present
# ---------------------------------------------------------------------------


def test_segment_breakdowns_present() -> None:
    facts = [
        _make_signal("s1", "2026-07-01 16:00:00", net_r=1.0, symbol="NEAR", side="LONG",
                     primary_tp_hit=True, mfe=1.5, closed_at="2026-07-01 18:00:00",
                     primary_tp_distance=100, sl_distance=100,
                     killzone=True, market_regime="TRENDING"),
        _make_signal("s2", "2026-07-01 17:00:00", net_r=-1.0, symbol="DOGE", side="SHORT",
                     real_stop_loss_hit=True, mfe=0.2, closed_at="2026-07-01 19:00:00",
                     primary_tp_distance=80, sl_distance=80,
                     killzone=False, market_regime="RANGING"),
    ]
    result = build_f5_t14_tp_policy_simulation(facts=facts)
    digest = result["json"]
    sections = digest.get("sections", {})
    seg = sections.get("F_segment_breakdowns", {})

    assert "by_symbol_top10_wider_friendly" in seg
    assert "by_symbol_top10_wider_hostile" in seg
    assert "by_direction" in seg
    assert "by_regime" in seg
    assert "by_killzone" in seg
    assert "by_exit_reason" in seg

    # NEAR is a watched symbol
    watched = seg.get("watched_symbols", [])
    watched_syms = [s.get("symbol") for s in watched]
    assert "NEAR" in watched_syms or "DOGE" in watched_syms

    direction = seg.get("by_direction", {})
    assert "LONG" in direction or "SHORT" in direction


# ---------------------------------------------------------------------------
# Test 13: Digest size within limits
# ---------------------------------------------------------------------------


def test_digest_size_within_limits() -> None:
    facts = []
    for i in range(50):
        facts.append(_make_signal(
            f"s{i}", f"2026-07-01 16:00:{i:02d}",
            net_r=1.0 if i % 2 == 0 else -0.5,
            symbol=f"SYM{i % 15}",
            side="LONG" if i % 2 == 0 else "SHORT",
            mfe=0.3 if i % 3 == 0 else None,
            mae=0.1 if i % 3 == 0 else None,
            closed_at=f"2026-07-01 18:00:{i:02d}",
            primary_tp_hit=(i % 2 == 0),
            real_stop_loss_hit=(i % 3 == 0),
            no_progress_exit=(i % 4 == 0),
            time_stop_exit=(i % 5 == 0),
            killzone=(i % 2 == 0),
            market_regime="TRENDING" if i % 2 == 0 else "RANGING",
            primary_tp_distance=100,
            sl_distance=100,
        ))
    for i in range(100):
        facts.append(_make_candidate(
            f"c{i}", f"2026-07-01 16:00:{i:02d}",
            blocked=(i % 3 == 0),
            blocked_reason="ofa_live_rvol_too_low" if i % 3 == 0 else "",
        ))

    result = build_f5_t14_tp_policy_simulation(facts=facts)
    digest = result["json"]
    json_str = json.dumps(digest, ensure_ascii=False, default=str)
    assert len(json_str) <= MAX_DIGEST_CHARS, f"Digest size {len(json_str)} exceeds {MAX_DIGEST_CHARS}"


# ---------------------------------------------------------------------------
# Test 14: Markdown contains all major sections
# ---------------------------------------------------------------------------


def test_markdown_contains_all_sections() -> None:
    facts = [
        _make_signal("s1", "2026-07-01 16:00:00", net_r=1.0, primary_tp_hit=True,
                     mfe=1.5, closed_at="2026-07-01 18:00:00",
                     primary_tp_distance=100, sl_distance=100,
                     killzone=True, market_regime="TRENDING"),
    ]
    result = build_f5_t14_tp_policy_simulation(facts=facts)
    md = result["markdown"]
    assert "### Data Quality" in md
    assert "### A. Current Policy" in md
    assert "### B. TP1 Hit Analysis" in md
    assert "### C. Single TP Simulations" in md
    assert "### D. Delayed BE Simulations" in md
    assert "### E. Post-TP1 Extension Distribution" in md
    assert "### F. Segment Breakdowns" in md
    assert "### G. Interpretation & Recommendation" in md


# ---------------------------------------------------------------------------
# Test 15: Both outputs generated (JSON + Markdown)
# ---------------------------------------------------------------------------


def test_both_outputs_generated() -> None:
    facts = [_make_signal("s1", "2026-07-01 16:00:00", net_r=1.0)]
    result = build_f5_t14_tp_policy_simulation(facts=facts)
    assert "json" in result
    assert "markdown" in result
    assert isinstance(result["json"], dict)
    assert isinstance(result["markdown"], str)
    assert "F5_T14" in result["markdown"]


# ---------------------------------------------------------------------------
# Test 16: JSON round-trip parseable
# ---------------------------------------------------------------------------


def test_json_round_trip() -> None:
    facts = [
        _make_signal("s1", "2026-07-01 16:00:00", net_r=1.5, symbol="BTC", side="LONG",
                     mfe=2.0, closed_at="2026-07-01 18:00:00",
                     primary_tp_hit=True, primary_tp_distance=100, sl_distance=100),
        _make_signal("s2", "2026-07-01 17:00:00", net_r=-0.8, symbol="ETH", side="SHORT",
                     mfe=0.3, closed_at="2026-07-01 19:00:00",
                     real_stop_loss_hit=True, primary_tp_distance=80, sl_distance=80),
    ]
    result = build_f5_t14_tp_policy_simulation(facts=facts)
    json_str = json.dumps(result["json"], ensure_ascii=False, default=str)
    parsed = json.loads(json_str)
    assert parsed["read_only"] is True
    assert parsed["schema_version"] == F5_T14_SCHEMA_VERSION


# ---------------------------------------------------------------------------
# Test 17: Empty post-change data handled gracefully
# ---------------------------------------------------------------------------


def test_empty_post_change_data() -> None:
    facts = [
        _make_signal("s_pre", "2026-07-01 10:00:00", net_r=1.0),  # Before cutoff
        _make_candidate("c_pre", "2026-07-01 10:00:00", blocked=True, blocked_reason="rvol_low"),
    ]
    result = build_f5_t14_tp_policy_simulation(facts=facts)
    digest = result["json"]
    assert digest.get("post_change_data_available") is False
    assert "No post-change data" in result["markdown"]


# ---------------------------------------------------------------------------
# Test 18: TP1 R estimation from geometry
# ---------------------------------------------------------------------------


def test_estimate_tp1_r_from_geometry() -> None:
    row = {
        "primary_tp_distance": 120,
        "sl_distance": 100,
        "primary_tp_hit": True,
        "net_r": 1.2,
    }
    tp1_r = _estimate_tp1_r(row)
    assert tp1_r == pytest.approx(1.2, 0.01)


def test_estimate_tp1_r_fallback() -> None:
    row = {
        "primary_tp_distance": None,
        "sl_distance": None,
        "primary_tp_hit": True,
        "net_r": 0.8,
    }
    tp1_r = _estimate_tp1_r(row)
    assert tp1_r == pytest.approx(0.8, 0.01)


def test_estimate_tp1_r_insufficient() -> None:
    row = {
        "primary_tp_distance": None,
        "sl_distance": None,
        "primary_tp_hit": False,
        "net_r": -1.0,
    }
    tp1_r = _estimate_tp1_r(row)
    assert tp1_r is None


# ---------------------------------------------------------------------------
# Test 19: MFE/MAE extraction
# ---------------------------------------------------------------------------


def test_estimate_mfe_r() -> None:
    assert _estimate_mfe_r({"mfe": 1.5}) == 1.5
    assert _estimate_mfe_r({"mfe": None}) is None
    assert _estimate_mfe_r({}) is None


def test_estimate_mae_r() -> None:
    assert _estimate_mae_r({"mae": -0.8}) == -0.8
    assert _estimate_mae_r({"mae": None}) is None
    assert _estimate_mae_r({}) is None


# ---------------------------------------------------------------------------
# Test 20: Core metrics calculation
# ---------------------------------------------------------------------------


def test_core_metrics() -> None:
    rows = [
        {"net_r": 1.0},
        {"net_r": 1.5},
        {"net_r": -0.5},
        {"net_r": 0.0},
    ]
    m = _core_metrics(rows)
    assert m["count"] == 4
    assert m["wins"] == 2
    assert m["losses"] == 1
    assert m["breakeven"] == 1
    assert m["gross_profit_r"] == 2.5
    assert m["gross_loss_r"] == 0.5
    assert m["net_r"] == 2.0
    assert m["profit_factor"] == 5.0
    assert m["winrate"] == 0.5


def test_core_metrics_empty() -> None:
    m = _core_metrics([])
    assert m["count"] == 0
    assert m["r_values_count"] == 0
    assert m["wins"] == 0
    assert m["losses"] == 0
    assert m["net_r"] == 0.0
    assert m["profit_factor"] is None
    assert m["winrate"] is None


# ---------------------------------------------------------------------------
# Test 21: _is_true helper
# ---------------------------------------------------------------------------


def test_is_true() -> None:
    assert _is_true(True) is True
    assert _is_true(False) is False
    assert _is_true(None) is False
    assert _is_true("true") is True
    assert _is_true("1") is True
    assert _is_true("yes") is True
    assert _is_true("no") is False