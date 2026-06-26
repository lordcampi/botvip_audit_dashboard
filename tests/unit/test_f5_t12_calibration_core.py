"""
Unit tests for F5_T12 Calibration Core Library.
Tests all pure metric computation functions without DB or UI dependencies.
"""

import json
import math
import sys
import os
from datetime import datetime, timezone

import pytest


# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from src.ai_reporter.f5_t12_calibration_core import (
    safe_float,
    safe_int,
    parse_json_safe,
    normalize_side,
    normalize_session,
    compute_weekend_flag,
    bucket_exit_family,
    is_managed_exit,
    is_directional_exit,
    extract_r_values,
    extract_r_values_with_meta,
    compute_profit_factor_stats,
    classify_decision,
    segment_rows,
    compute_no_progress_stats,
    _classify_no_progress_action,
    compute_managed_exit_stats,
    _classify_managed_exit_decision,
    compute_filter_contribution,
    _classify_filter_decision,
    compute_symbol_calibration,
    _classify_symbol_decision,
    classify_candidate_class,
    compute_candidate_promotion,
    _classify_promotion_decision,
    compact_json_serialize,
    write_compact_json,
)


# ===========================================================================
# safe_float
# ===========================================================================

class TestSafeFloat:
    def test_none(self):
        assert safe_float(None) is None
        assert safe_float(None, 0.0) == 0.0

    def test_int(self):
        assert safe_float(42) == 42.0
        assert safe_float(0) == 0.0
        assert safe_float(-5) == -5.0

    def test_float(self):
        assert safe_float(3.14) == 3.14
        assert safe_float(-0.5) == -0.5

    def test_valid_string(self):
        assert safe_float("3.14") == 3.14
        assert safe_float("-0.5") == -0.5
        assert safe_float("0") == 0.0

    def test_invalid_string(self):
        assert safe_float("abc") is None
        assert safe_float("") is None
        assert safe_float("   ") is None

    def test_nan_inf(self):
        assert safe_float(float("nan")) is None
        assert safe_float(float("inf")) is None
        assert safe_float(float("-inf")) is None


# ===========================================================================
# safe_int
# ===========================================================================

class TestSafeInt:
    def test_none(self):
        assert safe_int(None) is None

    def test_int(self):
        assert safe_int(42) == 42

    def test_float(self):
        assert safe_int(3.14) == 3
        assert safe_int(float("nan")) is None

    def test_string(self):
        assert safe_int("42") == 42
        assert safe_int("abc") is None


# ===========================================================================
# parse_json_safe
# ===========================================================================

class TestParseJsonSafe:
    def test_none(self):
        assert parse_json_safe(None) == {}

    def test_dict(self):
        assert parse_json_safe({"a": 1}) == {"a": 1}

    def test_valid_json(self):
        assert parse_json_safe('{"a": 1}') == {"a": 1}

    def test_invalid_json(self):
        assert parse_json_safe("not json") == {}

    def test_empty_string(self):
        assert parse_json_safe("") == {}


# ===========================================================================
# normalize_side
# ===========================================================================

class TestNormalizeSide:
    def test_long_variants(self):
        assert normalize_side("LONG") == "LONG"
        assert normalize_side("BUY") == "LONG"
        assert normalize_side("CALL") == "LONG"
        assert normalize_side("long") == "LONG"

    def test_short_variants(self):
        assert normalize_side("SHORT") == "SHORT"
        assert normalize_side("SELL") == "SHORT"
        assert normalize_side("PUT") == "SHORT"
        assert normalize_side("short") == "SHORT"

    def test_unknown(self):
        assert normalize_side(None) == "UNKNOWN"
        assert normalize_side("") == "UNKNOWN"
        assert normalize_side("OTHER") == "UNKNOWN"


# ===========================================================================
# normalize_session
# ===========================================================================

class TestNormalizeSession:
    def test_asia(self):
        # 05:00 UTC = 00:00 Bogota (UTC-5)
        dt = datetime(2026, 6, 25, 5, 0, 0, tzinfo=timezone.utc)
        assert normalize_session(dt) == "asia"


    def test_london_open(self):
        dt = datetime(2026, 6, 25, 12, 0, 0, tzinfo=timezone.utc)  # 07:00 Bogota
        assert normalize_session(dt) == "london_open"

    def test_ny_open(self):
        dt = datetime(2026, 6, 25, 18, 0, 0, tzinfo=timezone.utc)  # 13:00 Bogota
        assert normalize_session(dt) == "ny_open"

    def test_off_hours(self):
        dt = datetime(2026, 6, 25, 4, 0, 0, tzinfo=timezone.utc)  # 23:00 Bogota (previous day)
        # 04:00 UTC = 23:00 Bogota (UTC-5) previous day
        assert normalize_session(dt) == "off_hours"

    def test_none(self):
        assert normalize_session(None) == "unknown"

    def test_iso_string(self):
        assert normalize_session("2026-06-25T12:00:00Z") == "london_open"


# ===========================================================================
# compute_weekend_flag
# ===========================================================================

class TestComputeWeekendFlag:
    def test_explicit_true(self):
        assert compute_weekend_flag({"weekend": True}) is True
        assert compute_weekend_flag({"weekend": 1}) is True
        assert compute_weekend_flag({"weekend": "true"}) is True

    def test_explicit_false(self):
        assert compute_weekend_flag({"weekend": False}) is False
        assert compute_weekend_flag({"weekend": 0}) is False
        assert compute_weekend_flag({"weekend": "false"}) is False

    def test_saturday_fallback(self):
        # Saturday 2026-06-27
        dt = datetime(2026, 6, 27, 12, 0, 0, tzinfo=timezone.utc)
        assert compute_weekend_flag({"created_at": dt}) is True

    def test_weekday_fallback(self):
        # Thursday 2026-06-25
        dt = datetime(2026, 6, 25, 12, 0, 0, tzinfo=timezone.utc)
        assert compute_weekend_flag({"created_at": dt}) is False

    def test_no_data(self):
        assert compute_weekend_flag({}) is False


# ===========================================================================
# bucket_exit_family
# ===========================================================================

class TestBucketExitFamily:
    def test_take_profit(self):
        assert bucket_exit_family({"exit_reason": "take_profit"}) == "take_profit"
        assert bucket_exit_family({"exit_reason": "tp"}) == "take_profit"
        assert bucket_exit_family({"exit_reason": "runner_tp_hit"}) == "take_profit"

    def test_stop_loss(self):
        assert bucket_exit_family({"exit_reason": "stop_loss"}) == "stop_loss"
        assert bucket_exit_family({"exit_reason": "sl"}) == "stop_loss"

    def test_breakeven(self):
        assert bucket_exit_family({"exit_reason": "breakeven"}) == "breakeven"
        assert bucket_exit_family({"exit_reason": "be"}) == "breakeven"

    def test_no_progress(self):
        assert bucket_exit_family({"exit_reason": "no_progress"}) == "no_progress"

    def test_mfe_stall(self):
        assert bucket_exit_family({"exit_reason": "mfe_stall"}) == "mfe_stall"

    def test_time_stop(self):
        assert bucket_exit_family({"exit_reason": "time_stop"}) == "time_stop"

    def test_expired(self):
        assert bucket_exit_family({"exit_reason": "expired"}) == "expired"

    def test_unknown(self):
        assert bucket_exit_family({"exit_reason": "something_else"}) == "unknown_or_open"
        assert bucket_exit_family({}) == "unknown_or_open"

    def test_partial_match(self):
        assert bucket_exit_family({"exit_reason": "no_progress_exit"}) == "no_progress"
        assert bucket_exit_family({"exit_reason": "mfe_stall_exit"}) == "mfe_stall"


# ===========================================================================
# is_managed_exit / is_directional_exit
# ===========================================================================

class TestExitClassification:
    def test_managed_exits(self):
        for family in ("no_progress", "mfe_stall", "time_stop", "breakeven", "runner_breakeven", "expired", "unknown_or_open"):
            assert is_managed_exit(family) is True, f"{family} should be managed"

    def test_directional_exits(self):
        for family in ("take_profit", "stop_loss"):
            assert is_directional_exit(family) is True, f"{family} should be directional"

    def test_not_managed(self):
        assert is_managed_exit("take_profit") is False
        assert is_managed_exit("stop_loss") is False

    def test_not_directional(self):
        assert is_directional_exit("no_progress") is False
        assert is_directional_exit("breakeven") is False


# ===========================================================================
# extract_r_values
# ===========================================================================

class TestExtractRValues:
    def test_basic(self):
        rows = [
            {"net_r": 1.5},
            {"net_r": -0.5},
            {"net_r": 2.0},
        ]
        assert extract_r_values(rows) == [1.5, -0.5, 2.0]

    def test_skip_none(self):
        rows = [
            {"net_r": 1.0},
            {"net_r": None},
            {"net_r": "abc"},
        ]
        assert extract_r_values(rows) == [1.0]

    def test_empty(self):
        assert extract_r_values([]) == []

    def test_with_meta(self):
        rows = [
            {"net_r": 1.5},
            {"net_r": -0.5},
            {"net_r": 2.0},
            {"net_r": -1.0},
        ]
        values, wins, losses = extract_r_values_with_meta(rows)
        assert values == [1.5, -0.5, 2.0, -1.0]
        assert wins == 2
        assert losses == 2


# ===========================================================================
# compute_profit_factor_stats
# ===========================================================================

class TestComputeProfitFactorStats:
    def test_winners_and_losers(self):
        rows = [
            {"net_r": 2.0},
            {"net_r": -1.0},
            {"net_r": 1.5},
            {"net_r": -0.5},
        ]
        stats = compute_profit_factor_stats(rows, min_count=10)
        assert stats["r_values_count"] == 4
        assert stats["wins"] == 2
        assert stats["losses"] == 2
        assert stats["gross_win_r"] == 3.5
        assert stats["gross_loss_abs_r"] == 1.5
        assert stats["net_r"] == 2.0
        assert stats["profit_factor"] == round(3.5 / 1.5, 4)
        assert stats["decision"] == "INSUFFICIENT_SAMPLE"  # min_count=10



    def test_no_losses(self):
        rows = [{"net_r": 1.0}, {"net_r": 2.0}]
        stats = compute_profit_factor_stats(rows, min_count=1)
        assert stats["profit_factor"] is None  # no losses
        assert stats["decision"] == "KEEP_WATCH_NO_LOSSES"

    def test_no_r_values(self):
        rows = [{"net_r": None}, {"net_r": "abc"}]
        stats = compute_profit_factor_stats(rows)
        assert stats["r_values_count"] == 0
        assert stats["profit_factor"] is None
        assert stats["decision"] == "NO_R_VALUES"

    def test_empty(self):
        stats = compute_profit_factor_stats([])
        assert stats["count"] == 0
        assert stats["r_values_count"] == 0

    def test_high_pf(self):
        rows = [{"net_r": 3.0}, {"net_r": -1.0}, {"net_r": 2.0}]
        stats = compute_profit_factor_stats(rows, min_count=1)
        assert stats["profit_factor"] == 5.0
        assert stats["decision"] == "KEEP_OR_EXPAND"

    def test_low_pf(self):
        rows = [{"net_r": 0.5}, {"net_r": -2.0}, {"net_r": -1.0}]
        stats = compute_profit_factor_stats(rows, min_count=1)
        assert stats["profit_factor"] == round(0.5 / 3.0, 4)
        assert stats["decision"] == "RESTRICT"



# ===========================================================================
# classify_decision
# ===========================================================================

class TestClassifyDecision:
    def test_insufficient_sample(self):
        assert classify_decision(5, 1.5, 2.0, 3.0, 2.0, min_count=10) == "INSUFFICIENT_SAMPLE"

    def test_keep_watch_no_losses(self):
        assert classify_decision(10, None, 5.0, 5.0, 0.0) == "KEEP_WATCH_NO_LOSSES"

    def test_keep_or_expand(self):
        assert classify_decision(10, 1.5, 3.0, 6.0, 4.0) == "KEEP_OR_EXPAND"

    def test_watch(self):
        assert classify_decision(10, 1.0, 0.5, 5.0, 5.0) == "WATCH"

    def test_restrict(self):
        assert classify_decision(10, 0.5, -2.0, 3.0, 6.0) == "RESTRICT"

    def test_review(self):
        assert classify_decision(10, 0.5, 1.0, 3.0, 6.0) == "REVIEW"


# ===========================================================================
# segment_rows
# ===========================================================================

class TestSegmentRows:
    def test_all_dimension(self):
        rows = [{"symbol": "BTC"}, {"symbol": "ETH"}]
        segments = segment_rows(rows, "ALL")
        assert "ALL" in segments
        assert len(segments["ALL"]) == 2

    def test_symbol_dimension(self):
        rows = [
            {"symbol": "BTC"},
            {"symbol": "ETH"},
            {"symbol": "BTC"},
        ]
        segments = segment_rows(rows, "symbol")
        assert len(segments["BTC"]) == 2
        assert len(segments["ETH"]) == 1

    def test_signal_type_dimension(self):
        rows = [
            {"signal_type": "LONG"},
            {"signal_type": "SHORT"},
            {"signal_type": "LONG"},
        ]
        segments = segment_rows(rows, "signal_type")
        assert len(segments["LONG"]) == 2
        assert len(segments["SHORT"]) == 1

    def test_weekend_dimension(self):
        rows = [
            {"weekend": True},
            {"weekend": False},
        ]
        segments = segment_rows(rows, "weekend")
        assert "WEEKEND" in segments
        assert "WEEKDAY" in segments

    def test_exit_family_dimension(self):
        rows = [
            {"exit_reason": "take_profit"},
            {"exit_reason": "stop_loss"},
            {"exit_reason": "no_progress"},
        ]
        segments = segment_rows(rows, "exit_family")
        assert "take_profit" in segments
        assert "stop_loss" in segments
        assert "no_progress" in segments


# ===========================================================================
# compute_no_progress_stats
# ===========================================================================

class TestComputeNoProgressStats:
    def test_empty(self):
        stats = compute_no_progress_stats([])
        assert stats["count"] == 0
        assert stats["action"] == "INSUFFICIENT_SAMPLE"

    def test_basic(self):
        rows = [
            {"net_r": -0.1, "metrics_json": '{"mfe": 0.0}'},
            {"net_r": -0.2, "metrics_json": '{"mfe": 0.05}'},
            {"net_r": 0.0, "metrics_json": '{"mfe": 0.1}'},
        ]
        stats = compute_no_progress_stats(rows)
        assert stats["count"] == 3
        assert stats["mfe_zero_count"] >= 1

    def test_btc_conflict(self):
        rows = [
            {"net_r": -0.1, "btc_trend": "bearish", "signal_type": "LONG"},
            {"net_r": -0.2, "btc_trend": "bullish", "signal_type": "SHORT"},
            {"net_r": 0.0, "btc_trend": "bullish", "signal_type": "LONG"},
        ]
        stats = compute_no_progress_stats(rows)
        assert stats["btc_conflict_count"] == 2

    def test_spread_sensitive(self):
        rows = [
            {"net_r": -0.1, "spread_pct": 0.1},
            {"net_r": -0.2, "spread_pct": 0.01},
        ]
        stats = compute_no_progress_stats(rows)
        assert stats["spread_sensitive_count"] == 1


# ===========================================================================
# _classify_no_progress_action
# ===========================================================================

class TestClassifyNoProgressAction:
    def test_insufficient_sample(self):
        action = _classify_no_progress_action(3, 0, 0, 0, 0, 0, 0)
        assert action == "INSUFFICIENT_SAMPLE"

    def test_mfe_zero_high(self):
        action = _classify_no_progress_action(10, 6, 0, 0, 0, 0, 0)
        assert "BLOCK_PRE_ENTRY" in action

    def test_low_vol(self):
        action = _classify_no_progress_action(10, 0, 0, 5, 0, 0, 0)
        assert "REQUIRE_EXPANSION_CONFIRMATION" in action

    def test_btc_conflict(self):
        action = _classify_no_progress_action(10, 0, 0, 0, 4, 0, 0)
        assert "APPLY_BTC_CONFLICT_PENALTY" in action

    def test_watch_only(self):
        action = _classify_no_progress_action(10, 0, 0, 0, 0, 0, 0)
        assert action == "WATCH_ONLY"


# ===========================================================================
# compute_managed_exit_stats
# ===========================================================================

class TestComputeManagedExitStats:
    def test_empty(self):
        stats = compute_managed_exit_stats([])
        assert stats["count"] == 0
        assert stats["decision"] == "INSUFFICIENT_SAMPLE"

    def test_basic(self):
        rows = [
            {"net_r": 0.5, "exit_reason": "breakeven"},
            {"net_r": -0.3, "exit_reason": "no_progress"},
            {"net_r": 0.2, "exit_reason": "time_stop"},
        ]
        stats = compute_managed_exit_stats(rows)
        assert stats["count"] == 3
        assert stats["r_values_count"] == 3


# ===========================================================================
# _classify_managed_exit_decision
# ===========================================================================

class TestClassifyManagedExitDecision:
    def test_insufficient_sample(self):
        decision = _classify_managed_exit_decision({"r_values_count": 3, "profit_factor": 1.0, "net_r": 1.0})
        assert decision == "INSUFFICIENT_SAMPLE"

    def test_keep(self):
        decision = _classify_managed_exit_decision({"r_values_count": 10, "profit_factor": 1.5, "net_r": 5.0})
        assert decision == "KEEP"

    def test_keep_contextual(self):
        decision = _classify_managed_exit_decision({"r_values_count": 10, "profit_factor": 0.7, "net_r": 1.0})
        assert decision == "KEEP_CONTEXTUAL"

    def test_review_capture_rule(self):
        decision = _classify_managed_exit_decision({"r_values_count": 10, "profit_factor": 0.3, "net_r": -2.0})
        assert decision == "REVIEW_CAPTURE_RULE"


# ===========================================================================
# compute_filter_contribution
# ===========================================================================

class TestComputeFilterContribution:
    def test_empty(self):
        stats = compute_filter_contribution([], [])
        assert stats["blocked_count"] == 0
        assert stats["evaluable_count"] == 0

    def test_evaluable_only(self):
        evaluable = [
            {"net_r": 1.0},
            {"net_r": -0.5},
            {"net_r": 2.0},
        ]
        stats = compute_filter_contribution([], evaluable)
        assert stats["evaluable_count"] == 3
        assert stats["hypothetical_wins"] == 2
        assert stats["hypothetical_losses"] == 1

    def test_blocked_with_hypothetical(self):
        blocked = [
            {"metadata_json": '{"hypothetical_result": "loss", "net_rr": -1.5}'},
            {"metadata_json": '{"hypothetical_result": "win", "net_rr": 2.0}'},
        ]
        stats = compute_filter_contribution(blocked, [])
        assert stats["blocked_count"] == 2
        assert stats["avoided_loss_r"] == 1.5
        assert stats["missed_win_r"] == 2.0


# ===========================================================================
# _classify_filter_decision
# ===========================================================================

class TestClassifyFilterDecision:
    def test_insufficient_sample(self):
        assert _classify_filter_decision(1.0, 0.5, 3, 0.5) == "INSUFFICIENT_SAMPLE"

    def test_needs_geometry(self):
        assert _classify_filter_decision(1.0, 0.5, 10, 0.2) == "NEEDS_GEOMETRY"

    def test_keep(self):
        assert _classify_filter_decision(2.0, 0.5, 10, 0.5) == "KEEP"

    def test_relax(self):
        assert _classify_filter_decision(-1.0, 1.5, 10, 0.5) == "RELAX"

    def test_remove_candidate(self):
        assert _classify_filter_decision(-3.0, 0.5, 10, 0.5) == "REMOVE_CANDIDATE"

    def test_contextual(self):
        assert _classify_filter_decision(0.0, 1.0, 10, 0.5) == "CONTEXTUAL"


# ===========================================================================
# compute_symbol_calibration
# ===========================================================================

class TestComputeSymbolCalibration:
    def test_empty(self):
        stats = compute_symbol_calibration([], "BTC")
        assert stats["symbol"] == "BTC"
        assert stats["count"] == 0
        assert stats["decision"] == "INSUFFICIENT_SAMPLE"

    def test_basic(self):
        rows = [
            {"symbol": "BTC", "net_r": 2.0, "exit_reason": "take_profit"},
            {"symbol": "BTC", "net_r": -1.0, "exit_reason": "stop_loss"},
            {"symbol": "BTC", "net_r": 1.5, "exit_reason": "take_profit"},
        ]
        stats = compute_symbol_calibration(rows, "BTC")
        assert stats["count"] == 3
        assert stats["directional_count"] == 3
        assert stats["managed_count"] == 0
        assert stats["tp_count"] == 2
        assert stats["sl_count"] == 1


# ===========================================================================
# _classify_symbol_decision
# ===========================================================================

class TestClassifySymbolDecision:
    def test_insufficient_sample(self):
        assert _classify_symbol_decision(None, 0, 3, 0, 0, 3) == "INSUFFICIENT_SAMPLE"

    def test_allow(self):
        assert _classify_symbol_decision(1.5, 5.0, 10, 0, 0, 10) == "ALLOW"

    def test_allow_with_context(self):
        assert _classify_symbol_decision(1.0, 2.0, 10, 0, 0, 10) == "ALLOW_WITH_CONTEXT"

    def test_block_temporary(self):
        assert _classify_symbol_decision(0.3, -5.0, 10, 5, 3, 10) == "BLOCK_TEMPORARY"

    def test_restrict(self):
        assert _classify_symbol_decision(0.5, -3.0, 10, 4, 3, 10) == "RESTRICT"

    def test_watch_positive(self):
        assert _classify_symbol_decision(None, 2.0, 10, 0, 0, 10) == "WATCH_POSITIVE"


# ===========================================================================
# classify_candidate_class
# ===========================================================================

class TestClassifyCandidateClass:
    def test_sweep_only(self):
        assert classify_candidate_class({"reason": "sweep_detected"}) == "sweep_only"

    def test_sweep_plus_reclaim(self):
        assert classify_candidate_class({"reason": "sweep_and_reclaim"}) == "sweep_plus_reclaim"

    def test_absorption(self):
        assert classify_candidate_class({"reason": "absorption_confirmed"}) == "absorption_confirmed"

    def test_unknown(self):
        assert classify_candidate_class({"reason": "something_else"}) == "unknown_or_no_geometry"

    def test_from_metadata(self):
        row = {"reason": "generic", "metadata_json": '{"candidate_class": "delta_confirmed"}'}
        assert classify_candidate_class(row) == "delta_confirmed"


# ===========================================================================
# compute_candidate_promotion
# ===========================================================================

class TestComputeCandidatePromotion:
    def test_empty(self):
        stats = compute_candidate_promotion([])
        assert stats["count"] == 0
        assert stats["promotion_decision"] == "INSUFFICIENT_SAMPLE"

    def test_basic(self):
        rows = [
            {"metadata_json": '{"has_geometry": true, "net_rr": 2.0}'},
            {"metadata_json": '{"has_geometry": true, "net_rr": -1.0}'},
            {"metadata_json": '{"has_geometry": true, "net_rr": 1.5}'},
        ]
        stats = compute_candidate_promotion(rows)
        assert stats["count"] == 3
        assert stats["evaluable_count"] == 3


# ===========================================================================
# _classify_promotion_decision
# ===========================================================================

class TestClassifyPromotionDecision:
    def test_insufficient_sample(self):
        decision, confirmations = _classify_promotion_decision(2, None, 0, 2)
        assert decision == "INSUFFICIENT_SAMPLE"

    def test_promote(self):
        decision, confirmations = _classify_promotion_decision(10, 2.0, 5.0, 10)
        assert decision == "PROMOTE"
        assert confirmations == 2

    def test_watch_positive(self):
        decision, confirmations = _classify_promotion_decision(10, 1.2, 2.0, 10)
        assert decision == "WATCH_POSITIVE"
        assert confirmations == 3

    def test_restrict(self):
        decision, confirmations = _classify_promotion_decision(10, 0.3, -3.0, 10)
        assert decision == "RESTRICT"

    def test_watch_only(self):
        decision, confirmations = _classify_promotion_decision(10, 0.7, 0.5, 10)
        assert decision == "WATCH_ONLY"


# ===========================================================================
# compact_json_serialize
# ===========================================================================

class TestCompactJsonSerialize:
    def test_basic(self):
        data = {"key": "value", "number": 42}
        result = compact_json_serialize(data)
        parsed = json.loads(result)
        assert parsed["key"] == "value"
        assert parsed["number"] == 42

    def test_truncation(self):
        # Create data that exceeds max_chars
        data = {"items": ["x" * 100000]}
        result = compact_json_serialize(data, max_chars=1000)
        assert "//TRUNCATED" in result


# ===========================================================================
# Integration: PF decomposition matches dashboard metrics
# ===========================================================================

class TestIntegrationPFDecomposition:
    def test_dashboard_scenario(self):
        """Replicate the dashboard scenario from the spec:
        83 directional trades, 44 TP wins, 39 SL losses, 160 managed.
        """
        rows = []
        # 44 TP wins with varying R
        for i in range(44):
            rows.append({"net_r": 1.5 + (i % 3) * 0.5, "exit_reason": "take_profit"})
        # 39 SL losses
        for i in range(39):
            rows.append({"net_r": -1.0 - (i % 3) * 0.3, "exit_reason": "stop_loss"})
        # 160 managed exits (mostly no_progress, breakeven, time_stop)
        for i in range(80):
            rows.append({"net_r": -0.1, "exit_reason": "no_progress"})
        for i in range(40):
            rows.append({"net_r": 0.0, "exit_reason": "breakeven"})
        for i in range(40):
            rows.append({"net_r": -0.2, "exit_reason": "time_stop"})

        # Total PF
        total_stats = compute_profit_factor_stats(rows, min_count=1)
        assert total_stats["count"] == 243  # 44 + 39 + 160

        # Directional PF
        directional_rows = [r for r in rows if is_directional_exit(bucket_exit_family(r))]
        directional_stats = compute_profit_factor_stats(directional_rows, min_count=1)
        assert directional_stats["count"] == 83

        # Managed PF
        managed_rows = [r for r in rows if is_managed_exit(bucket_exit_family(r))]
        managed_stats = compute_profit_factor_stats(managed_rows, min_count=1)
        assert managed_stats["count"] == 160

        # Verify managed PF is lower than directional PF (as expected)
        if directional_stats["profit_factor"] is not None and managed_stats["profit_factor"] is not None:
            assert managed_stats["profit_factor"] < directional_stats["profit_factor"]


# ===========================================================================
# Integration: No-progress analysis
# ===========================================================================

class TestIntegrationNoProgress:
    def test_no_progress_segment(self):
        """Test that no-progress rows are correctly identified and analyzed."""
        rows = []
        for i in range(20):
            rows.append({
                "net_r": -0.1,
                "exit_reason": "no_progress",
                "metrics_json": json.dumps({"mfe": 0.02}),
                "btc_trend": "bearish" if i < 10 else "bullish",
                "signal_type": "LONG" if i < 10 else "SHORT",
                "spread_pct": 0.08 if i < 5 else 0.02,
            })

        stats = compute_no_progress_stats(rows)
        assert stats["count"] == 20
        assert stats["mfe_zero_count"] == 0  # MFE > 0
        # All 20 rows have btc_conflict: first 10 LONG+bearish, next 10 SHORT+bullish
        assert stats["btc_conflict_count"] == 20
        assert stats["spread_sensitive_count"] == 5  # first 5 have high spread



# ===========================================================================
# Integration: Symbol calibration
# ===========================================================================

class TestIntegrationSymbolCalibration:
    def test_symbol_tier_classification(self):
        """Test that symbols are correctly classified into tiers."""
        # Good symbol
        good_rows = [
            {"symbol": "GOOD", "net_r": 2.0, "exit_reason": "take_profit"},
            {"symbol": "GOOD", "net_r": 1.5, "exit_reason": "take_profit"},
            {"symbol": "GOOD", "net_r": -0.5, "exit_reason": "stop_loss"},
            {"symbol": "GOOD", "net_r": 3.0, "exit_reason": "take_profit"},
            {"symbol": "GOOD", "net_r": 1.0, "exit_reason": "take_profit"},
        ]
        good_stats = compute_symbol_calibration(good_rows, "GOOD")
        assert good_stats["decision"] in ("ALLOW", "ALLOW_WITH_CONTEXT")

        # Bad symbol
        bad_rows = [
            {"symbol": "BAD", "net_r": -1.0, "exit_reason": "stop_loss"},
            {"symbol": "BAD", "net_r": -2.0, "exit_reason": "stop_loss"},
            {"symbol": "BAD", "net_r": -0.5, "exit_reason": "no_progress"},
            {"symbol": "BAD", "net_r": -1.5, "exit_reason": "stop_loss"},
            {"symbol": "BAD", "net_r": -0.3, "exit_reason": "no_progress"},
        ]
        bad_stats = compute_symbol_calibration(bad_rows, "BAD")
        assert bad_stats["decision"] in ("RESTRICT", "BLOCK_TEMPORARY")


# ===========================================================================
# Integration: Candidate promotion
# ===========================================================================

class TestIntegrationCandidatePromotion:
    def test_candidate_classification(self):
        """Test candidate classification from scanner_candidate_shadow_snapshots."""
        rows = [
            {"reason": "sweep_detected", "metadata_json": '{"has_geometry": true, "net_rr": 2.0}'},
            {"reason": "absorption_confirmed", "metadata_json": '{"has_geometry": true, "net_rr": 1.5}'},
            {"reason": "reclaim_blocked", "metadata_json": '{"has_geometry": true, "net_rr": -0.5}'},
            {"reason": "unknown_pattern", "metadata_json": '{"has_geometry": false}'},
        ]
        stats = compute_candidate_promotion(rows)
        assert stats["count"] == 4
        assert stats["evaluable_count"] == 3
        assert stats["hypothetical_wins"] == 2
        assert stats["hypothetical_losses"] == 1
