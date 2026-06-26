"""Unit tests for F5_T12 Strategy Change Readiness digest.

Tests the compact digest builder functions:
  - build_f5_t12_strategy_readiness
  - All section builders
  - Size guard (< 95,000 characters)
  - Deterministic ordering
  - Read-only mode enforcement
"""
from __future__ import annotations

import json
import math
import sys
import os
from typing import Any

# Ensure src is on path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from src.ai_reporter.f5_t12_strategy_readiness import (
    build_f5_t12_strategy_readiness,
    F5_T12_READINESS_JSON_FILENAME,
    F5_T12_READINESS_MD_FILENAME,
    F5_T12_READINESS_SCHEMA_VERSION,
    MAX_DIGEST_CHARS,
    _build_denominators,
    _build_pf_core,
    _build_loss_top,
    _build_no_progress_core,
    _build_risk_context_candidates,
    _build_guard_value,
    _build_data_quality,
    _build_human_checklist,
    _safe_get,
    _limit,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_lifecycle(**overrides: Any) -> dict[str, Any]:
    return {
        "signals_total": 100,
        "sent_to_telegram": 85,
        "candidates_total": 250,
        "events_total": 450,
        "facts_total": 500,
        "primary_tp_hit": 30,
        "real_stop_loss_hit": 20,
        "no_progress_exit": 15,
        "near_miss_candidates": 10,
        **overrides,
    }


def _make_t02_diagnostics(**overrides: Any) -> dict[str, Any]:
    return {
        "profit_factor_diagnostics": {
            "sent_signals": {
                "profit_factor_stats": {
                    "count": 85,
                    "r_values_count": 80,
                    "wins": 45,
                    "losses": 35,
                    "gross_win_r": 65.5,
                    "gross_loss_abs_r": 42.3,
                    "net_r": 23.2,
                    "avg_r": 0.29,
                    "profit_factor": 1.55,
                    "decision": "KEEP_OR_EXPAND",
                    "confidence": "MEDIUM",
                }
            },
            "all_signals": {
                "profit_factor_stats": {
                    "count": 100,
                    "r_values_count": 95,
                    "wins": 50,
                    "losses": 45,
                    "gross_win_r": 72.0,
                    "gross_loss_abs_r": 55.0,
                    "net_r": 17.0,
                    "avg_r": 0.18,
                    "profit_factor": 1.31,
                    "decision": "KEEP_OR_EXPAND",
                    "confidence": "MEDIUM",
                }
            },
        },
        **overrides,
    }


def _make_loss_contribution(**overrides: Any) -> dict[str, Any]:
    return {
        "schema_version": "f5_t04e_loss_contribution_v1",
        "section": "F5_T04e_loss_contribution",
        "read_only": True,
        "official_signal_denominator": 100,
        "total_loss_abs_r": 42.3,
        "total_net_r": 23.2,
        "by_dimension": {
            "outcome": {
                "segments": [
                    {"segment": "loss", "count": 35, "gross_loss_abs_r": 42.3, "avg_r": -1.21, "loss_contribution_pct": 1.0},
                    {"segment": "win", "count": 45, "gross_loss_abs_r": 0.0, "avg_r": 1.46, "loss_contribution_pct": 0.0},
                ],
                "top_loss_segments": [
                    {"segment": "loss", "count": 35, "gross_loss_abs_r": 42.3, "avg_r": -1.21, "loss_contribution_pct": 1.0},
                ],
            },
            "symbol": {
                "segments": [
                    {"segment": "BTC", "count": 10, "gross_loss_abs_r": 15.0, "avg_r": -1.5, "loss_contribution_pct": 0.35},
                    {"segment": "ETH", "count": 8, "gross_loss_abs_r": 10.0, "avg_r": -1.25, "loss_contribution_pct": 0.24},
                    {"segment": "SOL", "count": 5, "gross_loss_abs_r": 7.0, "avg_r": -1.4, "loss_contribution_pct": 0.17},
                    {"segment": "DOGE", "count": 4, "gross_loss_abs_r": 5.0, "avg_r": -1.25, "loss_contribution_pct": 0.12},
                    {"segment": "ADA", "count": 3, "gross_loss_abs_r": 3.0, "avg_r": -1.0, "loss_contribution_pct": 0.07},
                ],
                "top_loss_segments": [
                    {"segment": "BTC", "count": 10, "gross_loss_abs_r": 15.0, "avg_r": -1.5, "loss_contribution_pct": 0.35},
                    {"segment": "ETH", "count": 8, "gross_loss_abs_r": 10.0, "avg_r": -1.25, "loss_contribution_pct": 0.24},
                    {"segment": "SOL", "count": 5, "gross_loss_abs_r": 7.0, "avg_r": -1.4, "loss_contribution_pct": 0.17},
                    {"segment": "DOGE", "count": 4, "gross_loss_abs_r": 5.0, "avg_r": -1.25, "loss_contribution_pct": 0.12},
                    {"segment": "ADA", "count": 3, "gross_loss_abs_r": 3.0, "avg_r": -1.0, "loss_contribution_pct": 0.07},
                ],
            },
            "side": {
                "segments": [
                    {"segment": "LONG", "count": 20, "gross_loss_abs_r": 25.0, "avg_r": -1.25, "loss_contribution_pct": 0.59},
                    {"segment": "SHORT", "count": 15, "gross_loss_abs_r": 17.3, "avg_r": -1.15, "loss_contribution_pct": 0.41},
                ],
                "top_loss_segments": [
                    {"segment": "LONG", "count": 20, "gross_loss_abs_r": 25.0, "avg_r": -1.25, "loss_contribution_pct": 0.59},
                    {"segment": "SHORT", "count": 15, "gross_loss_abs_r": 17.3, "avg_r": -1.15, "loss_contribution_pct": 0.41},
                ],
            },
            "zone": {
                "segments": [
                    {"segment": "killzone", "count": 18, "gross_loss_abs_r": 22.0, "avg_r": -1.22, "loss_contribution_pct": 0.52},
                    {"segment": "outside_killzone", "count": 12, "gross_loss_abs_r": 15.0, "avg_r": -1.25, "loss_contribution_pct": 0.35},
                    {"segment": "unknown_zone", "count": 5, "gross_loss_abs_r": 5.3, "avg_r": -1.06, "loss_contribution_pct": 0.13},
                ],
                "top_loss_segments": [
                    {"segment": "killzone", "count": 18, "gross_loss_abs_r": 22.0, "avg_r": -1.22, "loss_contribution_pct": 0.52},
                    {"segment": "outside_killzone", "count": 12, "gross_loss_abs_r": 15.0, "avg_r": -1.25, "loss_contribution_pct": 0.35},
                    {"segment": "unknown_zone", "count": 5, "gross_loss_abs_r": 5.3, "avg_r": -1.06, "loss_contribution_pct": 0.13},
                ],
            },
            "data_gap_bucket": {
                "segments": [
                    {"segment": "data_gap_0", "count": 60, "gross_loss_abs_r": 25.0, "avg_r": 0.42, "loss_contribution_pct": 0.59},
                    {"segment": "data_gap_1_to_2", "count": 20, "gross_loss_abs_r": 10.0, "avg_r": 0.50, "loss_contribution_pct": 0.24},
                    {"segment": "data_gap_3_to_5", "count": 10, "gross_loss_abs_r": 5.0, "avg_r": 0.50, "loss_contribution_pct": 0.12},
                    {"segment": "data_gap_gt_5", "count": 5, "gross_loss_abs_r": 2.3, "avg_r": 0.46, "loss_contribution_pct": 0.05},
                ],
                "top_loss_segments": [
                    {"segment": "data_gap_0", "count": 60, "gross_loss_abs_r": 25.0, "avg_r": 0.42, "loss_contribution_pct": 0.59},
                ],
            },
        },
        **overrides,
    }


def _make_no_progress_v3(**overrides: Any) -> dict[str, Any]:
    return {
        "official_signal_denominator": 100,
        "official_no_progress_count": 15,
        "mfe_known_count": 12,
        "mae_known_count": 10,
        "avg_r": -0.85,
        "bucket_counts": {
            "mfe_zero": 5,
            "mfe_low": 3,
            "low_vol": 2,
            "btc_conflict": 2,
            "spread_sensitive": 2,
            "entered_too_late": 1,
        },
        "segments": {
            "by_symbol": {
                "BTC": {"sample_size": 4, "avg_r": -0.9, "net_sum_r": -3.6},
                "ETH": {"sample_size": 3, "avg_r": -0.8, "net_sum_r": -2.4},
                "SOL": {"sample_size": 2, "avg_r": -1.0, "net_sum_r": -2.0},
                "DOGE": {"sample_size": 2, "avg_r": -0.7, "net_sum_r": -1.4},
                "ADA": {"sample_size": 1, "avg_r": -0.5, "net_sum_r": -0.5},
                "LTC": {"sample_size": 1, "avg_r": -1.2, "net_sum_r": -1.2},
                "BCH": {"sample_size": 1, "avg_r": -0.6, "net_sum_r": -0.6},
                "NEAR": {"sample_size": 1, "avg_r": -0.9, "net_sum_r": -0.9},
            }
        },
        **overrides,
    }


def _make_guard_matrix(**overrides: Any) -> dict[str, Any]:
    return {
        "candidate_shadow_denominator": 250,
        "matched_guard_rows": 45,
        "matrix_by_guard": {
            "adx_filter": {
                "rows": 15,
                "avoided_losses_r": 12.5,
                "missed_winners_r": 3.2,
                "net_guard_value_r": 9.3,
            },
            "rvol_filter": {
                "rows": 10,
                "avoided_losses_r": 8.0,
                "missed_winners_r": 4.0,
                "net_guard_value_r": 4.0,
            },
            "spread_filter": {
                "rows": 8,
                "avoided_losses_r": 6.0,
                "missed_winners_r": 5.0,
                "net_guard_value_r": 1.0,
            },
            "btc_trend_filter": {
                "rows": 7,
                "avoided_losses_r": 3.0,
                "missed_winners_r": 6.0,
                "net_guard_value_r": -3.0,
            },
            "weekend_filter": {
                "rows": 5,
                "avoided_losses_r": 2.0,
                "missed_winners_r": 4.5,
                "net_guard_value_r": -2.5,
            },
        },
        **overrides,
    }


def _make_facts() -> list[dict[str, Any]]:
    return [{"record_type": "signal", "signal_id": i} for i in range(100)]


# ---------------------------------------------------------------------------
# Tests: Section builders
# ---------------------------------------------------------------------------


class TestBuildDenominators:
    def test_basic(self):
        lifecycle = _make_lifecycle()
        result = _build_denominators(lifecycle)
        assert result["official_signals"] == 100
        assert result["sent_to_telegram"] == 85
        assert result["candidates"] == 250
        assert result["events"] == 450
        assert result["facts"] == 500

    def test_with_facts(self):
        lifecycle = _make_lifecycle()
        facts = _make_facts()
        result = _build_denominators(lifecycle, facts)
        assert result["facts"] == 100  # len(facts)

    def test_empty_lifecycle(self):
        result = _build_denominators({})
        assert result["official_signals"] == 0
        assert result["sent_to_telegram"] == 0


class TestBuildPfCore:
    def test_basic(self):
        t02 = _make_t02_diagnostics()
        result = _build_pf_core(t02)
        assert result["sent_only"]["count"] == 85
        assert result["sent_only"]["gross_profit_r"] == 65.5
        assert result["sent_only"]["gross_loss_r"] == 42.3
        assert result["sent_only"]["profit_factor"] == 1.55
        assert result["all_signals"]["count"] == 100

    def test_empty(self):
        result = _build_pf_core({})
        assert result["sent_only"]["count"] == 0
        assert result["sent_only"]["profit_factor"] is None

    def test_direct_t02(self):
        """Handle case where t02_diagnostics IS the pf data."""
        t02 = {
            "sent_signals": {
                "profit_factor_stats": {
                    "count": 50,
                    "gross_win_r": 30.0,
                    "gross_loss_abs_r": 20.0,
                    "avg_r": 0.2,
                    "profit_factor": 1.5,
                    "confidence": "LOW",
                }
            },
            "all_signals": {
                "profit_factor_stats": {
                    "count": 60,
                    "gross_win_r": 35.0,
                    "gross_loss_abs_r": 25.0,
                    "avg_r": 0.17,
                    "profit_factor": 1.4,
                    "confidence": "LOW",
                }
            },
        }
        result = _build_pf_core(t02)
        assert result["sent_only"]["count"] == 50
        assert result["sent_only"]["profit_factor"] == 1.5


class TestBuildLossTop:
    def test_basic(self):
        lc = _make_loss_contribution()
        result = _build_loss_top(lc)
        assert result["total_loss_abs_r"] == 42.3
        assert result["total_net_r"] == 23.2
        assert "outcome" in result["top_by_dimension"]
        assert "symbol" in result["top_by_dimension"]
        assert "side" in result["top_by_dimension"]
        assert "zone" in result["top_by_dimension"]

    def test_top_losses_limited(self):
        lc = _make_loss_contribution()
        result = _build_loss_top(lc)
        for dim in ("outcome", "symbol", "side", "zone"):
            assert len(result["top_by_dimension"][dim]) <= 5

    def test_empty(self):
        result = _build_loss_top({})
        assert result["total_loss_abs_r"] is None
        # When no data, each dimension returns an empty list
        for dim in ("outcome", "symbol", "side", "zone"):
            assert result["top_by_dimension"][dim] == []


class TestBuildNoProgressCore:
    def test_basic(self):
        np = _make_no_progress_v3()
        result = _build_no_progress_core(np)
        assert result["official_no_progress_count"] == 15
        assert result["official_signal_denominator"] == 100
        assert result["avg_r"] == -0.85
        assert len(result["top_symbols"]) <= 10

    def test_top_symbols_ordered(self):
        np = _make_no_progress_v3()
        result = _build_no_progress_core(np)
        symbols = result["top_symbols"]
        if len(symbols) >= 2:
            assert symbols[0]["count"] >= symbols[1]["count"]

    def test_empty(self):
        result = _build_no_progress_core({})
        assert result["official_no_progress_count"] is None
        assert result["top_symbols"] == []


class TestBuildRiskContextCandidates:
    def test_basic(self):
        gm = _make_guard_matrix()
        result = _build_risk_context_candidates(gm)
        assert result["candidate_shadow_denominator"] == 250
        assert result["matched_guard_rows"] == 45
        assert len(result["guard_summaries"]) == 5

    def test_guard_summaries_limited(self):
        gm = _make_guard_matrix()
        # Add many guards
        for i in range(20):
            gm["matrix_by_guard"][f"guard_{i}"] = {
                "rows": 1,
                "avoided_losses_r": 0.5,
                "missed_winners_r": 0.2,
                "net_guard_value_r": 0.3,
            }
        result = _build_risk_context_candidates(gm)
        assert len(result["guard_summaries"]) <= 10

    def test_empty(self):
        result = _build_risk_context_candidates({})
        assert result["candidate_shadow_denominator"] == 0
        assert result["guard_summaries"] == []


class TestBuildGuardValue:
    def test_basic(self):
        gm = _make_guard_matrix()
        result = _build_guard_value(gm)
        assert len(result["positive_net_value"]) > 0
        assert len(result["negative_net_value"]) > 0

    def test_positive_first(self):
        gm = _make_guard_matrix()
        result = _build_guard_value(gm)
        for g in result["positive_net_value"]:
            assert g["net_guard_value_r"] > 0
        for g in result["negative_net_value"]:
            assert g["net_guard_value_r"] < 0

    def test_empty(self):
        result = _build_guard_value({})
        assert result["positive_net_value"] == []
        assert result["negative_net_value"] == []


class TestBuildDataQuality:
    def test_basic(self):
        t02 = _make_t02_diagnostics()
        np = _make_no_progress_v3()
        lc = _make_loss_contribution()
        result = _build_data_quality(t02, np, lc)
        assert result["mfe_known"] == 12
        assert result["mae_known"] == 10
        assert result["official_signal_denominator"] == 100

    def test_warnings_low_mfe(self):
        t02 = _make_t02_diagnostics()
        np = _make_no_progress_v3(mfe_known_count=10, official_signal_denominator=100)
        lc = _make_loss_contribution()
        result = _build_data_quality(t02, np, lc)
        # 10/100 = 10% < 50% -> warning
        assert len(result["confidence_warnings"]) >= 1

    def test_no_warnings_good_quality(self):
        t02 = _make_t02_diagnostics()
        np = _make_no_progress_v3(mfe_known_count=80, mae_known_count=75, official_signal_denominator=100)
        lc = _make_loss_contribution()
        result = _build_data_quality(t02, np, lc)
        assert len(result["confidence_warnings"]) == 0

    def test_empty(self):
        result = _build_data_quality({}, {}, {})
        assert result["mfe_known"] is None
        assert result["confidence_warnings"] == []


class TestBuildHumanChecklist:
    def test_basic(self):
        result = _build_human_checklist()
        assert "checklist" in result
        assert len(result["checklist"]) == 10

    def test_all_items_have_ids(self):
        result = _build_human_checklist()
        ids = [item["id"] for item in result["checklist"]]
        assert ids == ["C01", "C02", "C03", "C04", "C05", "C06", "C07", "C08", "C09", "C10"]

    def test_all_items_have_categories(self):
        result = _build_human_checklist()
        categories = {item["category"] for item in result["checklist"]}
        assert "denominators" in categories
        assert "profit_factor" in categories
        assert "loss_contribution" in categories
        assert "no_progress" in categories
        assert "risk_context_gate" in categories
        assert "data_quality" in categories
        assert "deploy_readiness" in categories


# ---------------------------------------------------------------------------
# Tests: Main builder
# ---------------------------------------------------------------------------


class TestBuildF5T12StrategyReadiness:
    def test_basic(self):
        result = build_f5_t12_strategy_readiness(
            lifecycle=_make_lifecycle(),
            facts=_make_facts(),
            t02_diagnostics=_make_t02_diagnostics(),
            loss_contribution=_make_loss_contribution(),
            no_progress_v3=_make_no_progress_v3(),
            guard_matrix=_make_guard_matrix(),
        )
        assert "json" in result
        assert "markdown" in result

        digest = result["json"]
        assert digest["schema_version"] == F5_T12_READINESS_SCHEMA_VERSION
        assert digest["section"] == "f5_t12_strategy_change_readiness"
        assert digest["read_only"] is True
        assert "sections" in digest
        assert "denominators" in digest["sections"]
        assert "pf_core" in digest["sections"]
        assert "loss_top" in digest["sections"]
        assert "no_progress_core" in digest["sections"]
        assert "risk_context_candidates" in digest["sections"]
        assert "guard_value" in digest["sections"]
        assert "data_quality" in digest["sections"]
        assert "human_checklist" in digest["sections"]

    def test_size_guard(self):
        """JSON output must be < 95,000 characters."""
        result = build_f5_t12_strategy_readiness(
            lifecycle=_make_lifecycle(),
            facts=_make_facts(),
            t02_diagnostics=_make_t02_diagnostics(),
            loss_contribution=_make_loss_contribution(),
            no_progress_v3=_make_no_progress_v3(),
            guard_matrix=_make_guard_matrix(),
        )
        json_str = json.dumps(result["json"], ensure_ascii=False, default=str)
        assert len(json_str) < MAX_DIGEST_CHARS, f"JSON size {len(json_str)} exceeds {MAX_DIGEST_CHARS}"

    def test_markdown_size(self):
        """Markdown output must be < 95,000 characters."""
        result = build_f5_t12_strategy_readiness(
            lifecycle=_make_lifecycle(),
            facts=_make_facts(),
            t02_diagnostics=_make_t02_diagnostics(),
            loss_contribution=_make_loss_contribution(),
            no_progress_v3=_make_no_progress_v3(),
            guard_matrix=_make_guard_matrix(),
        )
        assert len(result["markdown"]) < MAX_DIGEST_CHARS, f"MD size {len(result['markdown'])} exceeds {MAX_DIGEST_CHARS}"

    def test_deterministic_ordering(self):
        """Running twice with same inputs must produce identical output."""
        inputs = dict(
            lifecycle=_make_lifecycle(),
            facts=_make_facts(),
            t02_diagnostics=_make_t02_diagnostics(),
            loss_contribution=_make_loss_contribution(),
            no_progress_v3=_make_no_progress_v3(),
            guard_matrix=_make_guard_matrix(),
        )
        result1 = build_f5_t12_strategy_readiness(**inputs)
        result2 = build_f5_t12_strategy_readiness(**inputs)
        assert json.dumps(result1["json"], ensure_ascii=False, default=str) == \
               json.dumps(result2["json"], ensure_ascii=False, default=str)
        assert result1["markdown"] == result2["markdown"]

    def test_read_only_flag(self):
        """All sections must have read_only semantics."""
        result = build_f5_t12_strategy_readiness(
            lifecycle=_make_lifecycle(),
            facts=_make_facts(),
            t02_diagnostics=_make_t02_diagnostics(),
            loss_contribution=_make_loss_contribution(),
            no_progress_v3=_make_no_progress_v3(),
            guard_matrix=_make_guard_matrix(),
        )
        digest = result["json"]
        assert digest["read_only"] is True
        assert digest["mode"] == "shadow_observational_only"

    def test_json_parseable(self):
        """JSON output must be valid JSON."""
        result = build_f5_t12_strategy_readiness(
            lifecycle=_make_lifecycle(),
            facts=_make_facts(),
            t02_diagnostics=_make_t02_diagnostics(),
            loss_contribution=_make_loss_contribution(),
            no_progress_v3=_make_no_progress_v3(),
            guard_matrix=_make_guard_matrix(),
        )
        json_str = json.dumps(result["json"], ensure_ascii=False, default=str)
        parsed = json.loads(json_str)
        assert parsed["section"] == "f5_t12_strategy_change_readiness"

    def test_with_empty_inputs(self):
        """Should handle empty/missing inputs gracefully."""
        result = build_f5_t12_strategy_readiness(
            lifecycle={},
            facts=[],
            t02_diagnostics={},
            loss_contribution={},
            no_progress_v3={},
            guard_matrix={},
        )
        digest = result["json"]
        assert digest["read_only"] is True
        json_str = json.dumps(digest, ensure_ascii=False, default=str)
        assert len(json_str) < MAX_DIGEST_CHARS

    def test_guardrails_present(self):
        """Digest must include guardrails."""
        result = build_f5_t12_strategy_readiness(
            lifecycle=_make_lifecycle(),
            facts=_make_facts(),
            t02_diagnostics=_make_t02_diagnostics(),
            loss_contribution=_make_loss_contribution(),
            no_progress_v3=_make_no_progress_v3(),
            guard_matrix=_make_guard_matrix(),
        )
        guardrails = result["json"].get("guardrails", [])
        assert len(guardrails) >= 3
        assert any("real trading" in g for g in guardrails)
        assert any("Single-window" in g for g in guardrails)

    def test_markdown_contains_sections(self):
        """Markdown must contain all major section headers."""
        result = build_f5_t12_strategy_readiness(
            lifecycle=_make_lifecycle(),
            facts=_make_facts(),
            t02_diagnostics=_make_t02_diagnostics(),
            loss_contribution=_make_loss_contribution(),
            no_progress_v3=_make_no_progress_v3(),
            guard_matrix=_make_guard_matrix(),
        )
        md = result["markdown"]
        assert "## Denominators" in md
        assert "## PF Core" in md
        assert "## Loss Top" in md
        assert "## No-Progress Core" in md
        assert "## Risk Context Candidates" in md
        assert "## Guard Value" in md
        assert "## Data Quality" in md
        assert "## Human Checklist" in md
        assert "## Guardrails" in md


# ---------------------------------------------------------------------------
# Tests: Helpers
# ---------------------------------------------------------------------------


class TestSafeGet:
    def test_basic(self):
        data = {"a": {"b": {"c": 42}}}
        assert _safe_get(data, ["a", "b", "c"]) == 42

    def test_missing(self):
        data = {"a": 1}
        assert _safe_get(data, ["b", "c"]) is None

    def test_default(self):
        data = {"a": 1}
        assert _safe_get(data, ["b"], default="fallback") == "fallback"

    def test_list_index(self):
        data = {"items": [10, 20, 30]}
        assert _safe_get(data, ["items", 1]) == 20

    def test_list_out_of_range(self):
        data = {"items": [10, 20]}
        assert _safe_get(data, ["items", 5]) is None


class TestLimit:
    def test_basic(self):
        data = {"a": 1, "b": 2, "c": 3, "d": 4, "e": 5}
        result = _limit(data, max_items=3, depth=2)
        assert len(result) == 4  # 3 items + _omitted_keys
        assert result["_omitted_keys"] == 2

    def test_list_limit(self):
        data = list(range(20))
        result = _limit(data, max_items=5, depth=2)
        assert len(result) == 5

    def test_depth_limit(self):
        data = {"a": {"b": {"c": {"d": 1}}}}
        result = _limit(data, max_items=10, depth=2)
        assert "_truncated" in result["a"]["b"]

    def test_scalar(self):
        assert _limit(42) == 42
        assert _limit("hello") == "hello"
        assert _limit(True) is True
        assert _limit(None) is None
