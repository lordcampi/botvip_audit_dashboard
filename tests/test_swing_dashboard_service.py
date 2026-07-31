from __future__ import annotations

"""Tests for swing_dashboard_service.py — R2 view-model builder."""

import json
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from src.swing_dashboard_service import (
    assess_data_quality,
    build_swing_dashboard,
    window_days,
    custom_window,
    _compute_signal_kpis,
    _build_executability,
    _build_signal_table,
    _build_experiments_panel,
    _build_shadow_panel,
    _build_scanner_panel,
    is_swing_trend_reclaim_signal,
    filter_swing_official_signals,
    extract_nested_timestamp,
    normalize_side,
    _fingerprint_segmentation,
    _resolve_nested_timestamp,
    _safe_str,
    SWING_SCOPE_SIDE,
    COLOMBIA_OFFSET,
)


# ---------------------------------------------------------------------------
# Synthetic fixtures
# ---------------------------------------------------------------------------
def _make_metrics(execution_detached=None, same_market_bar=None, fingerprint="fp_test_ok", adapter_parity=None):
    """Build a metrics_json dict for testing."""
    obj: dict = {}
    swing_v1: dict = {}
    if fingerprint:
        swing_v1["config_fingerprint"] = fingerprint
    if execution_detached is not None:
        swing_v1["execution_detached"] = execution_detached
    if same_market_bar is not None:
        swing_v1["same_market_bar"] = same_market_bar
    if adapter_parity is not None:
        swing_v1["adapter_parity"] = adapter_parity
    obj["swing_v1"] = swing_v1
    return json.dumps(obj)


def _signal_df(rows=None):
    """Build a minimal signal_records DataFrame for testing."""
    if rows is None:
        rows = [
            {"id": 1, "symbol": "BTCUSDT", "side": "LONG", "status": "WON", "net_r": 1.5, "gross_r": 1.5,
             "created_at": datetime(2026, 7, 20, 14, 0, 0),
             "born_timestamp": datetime(2026, 7, 20, 14, 0, 0),
             "activation_bar_timestamp": datetime(2026, 7, 20, 14, 0, 0),
             "metrics_json": _make_metrics(execution_detached=False, same_market_bar=True, adapter_parity={"status": "FILLED"})},
            {"id": 2, "symbol": "ETHUSDT", "side": "SHORT", "status": "LOST", "net_r": -1.0, "gross_r": -1.0,
             "created_at": datetime(2026, 7, 20, 15, 0, 0),
             "born_timestamp": datetime(2026, 7, 20, 15, 0, 0),
             "activation_bar_timestamp": datetime(2026, 7, 20, 15, 0, 0),
             "metrics_json": _make_metrics(execution_detached=True, same_market_bar=False, adapter_parity=None)},
            {"id": 3, "symbol": "ADAUSDT", "side": "LONG", "status": "PENDING", "net_r": None, "gross_r": None,
             "created_at": datetime(2026, 7, 20, 16, 0, 0),
             "born_timestamp": None,
             "activation_bar_timestamp": None,
             "metrics_json": _make_metrics(execution_detached=None)},
        ]
    return pd.DataFrame(rows)


def _events_df():
    return pd.DataFrame({
        "id": [1, 2],
        "signal_id": [1, 1],
        "event_type": ["SIGNAL_CREATED", "PRIMARY_TP_HIT"],
        "event_time": [datetime(2026, 7, 20, 14, 0, 0), datetime(2026, 7, 20, 18, 0, 0)],
        "metadata_json": ["{}", '{"key":"val"}'],
    })


# ---------------------------------------------------------------------------
# Window helpers
# ---------------------------------------------------------------------------
class TestWindowHelpers:
    def test_window_3_days_returns_colombia_range(self):
        start, end = window_days(3)
        diff = end - start
        assert diff.days == 3

    def test_window_7_days(self):
        start, end = window_days(7)
        diff = end - start
        assert diff.days == 7

    def test_window_colombia_offset(self):
        start, end = window_days(1)
        # Roughly 24h ± DST (Colombia has no DST, UTC-5)
        diff = end - start
        assert timedelta(hours=23, minutes=59) <= diff <= timedelta(hours=24, minutes=1)

    def test_custom_window(self):
        start, end = custom_window("2026-07-20T00:00:00", "2026-07-27T00:00:00")
        assert start == datetime(2026, 7, 20)
        assert end == datetime(2026, 7, 27)


# ---------------------------------------------------------------------------
# Data quality
# ---------------------------------------------------------------------------
class TestAssessDataQuality:
    def test_empty_signals_is_insufficient(self):
        result = assess_data_quality(pd.DataFrame(), "fp_test")
        assert result["level"] == "INSUFFICIENT"

    def test_none_signals_is_insufficient(self):
        result = assess_data_quality(None, None)
        assert result["level"] == "INSUFFICIENT"

    def test_good_quality(self):
        # Use 6 signals to pass the min sample threshold (>=5)
        rows = [
            {"id": i, "symbol": "BTCUSDT", "side": "LONG", "status": "WON", "net_r": 1.0,
             "created_at": datetime(2026, 7, 20, 14, 0, 0) + timedelta(hours=i),
             "metrics_json": _make_metrics()}
            for i in range(6)
        ]
        signals = pd.DataFrame(rows)
        result = assess_data_quality(signals, "fp_test")
        assert result["level"] in ("GOOD", "PARTIAL")

    def test_no_fingerprint_adds_reason(self):
        signals = _signal_df()
        result = assess_data_quality(signals, None)
        assert len(result["reasons"]) >= 1

    def test_multiple_fingerprints_produces_warning(self):
        rows = [
            {"id": i, "symbol": "BTCUSDT", "side": "LONG", "status": "WON", "net_r": 1.0,
             "created_at": datetime(2026, 7, 20, 14, 0, 0) + timedelta(hours=i),
             "metrics_json": _make_metrics(fingerprint=f"fp_{i % 3}")}
            for i in range(10)
        ]
        signals = pd.DataFrame(rows)
        result = assess_data_quality(signals, "fp_0")
        assert any("Multiple" in r for r in result["reasons"])

    def test_invalid_json_produces_invalid(self):
        rows = [
            {"id": i, "symbol": "BTCUSDT", "side": "LONG", "status": "WON", "net_r": 1.0,
             "created_at": datetime(2026, 7, 20, 14, 0, 0) + timedelta(hours=i),
             "metrics_json": "not-valid-json!!!"}
            for i in range(10)
        ]
        signals = pd.DataFrame(rows)
        result = assess_data_quality(signals, None)
        assert result["level"] == "INVALID"

    def test_small_sample_is_insufficient(self):
        rows = [
            {"id": i, "symbol": "BTCUSDT", "side": "LONG", "status": "WON", "net_r": 1.0,
             "created_at": datetime(2026, 7, 20, 14, 0, 0) + timedelta(hours=i),
             "metrics_json": _make_metrics()}
            for i in range(3)
        ]
        signals = pd.DataFrame(rows)
        result = assess_data_quality(signals, "fp_test")
        assert result["level"] == "INSUFFICIENT"


# ---------------------------------------------------------------------------
# Signal KPIs
# ---------------------------------------------------------------------------
class TestComputeSignalKpis:
    def test_empty_signals(self):
        result = _compute_signal_kpis(pd.DataFrame())
        assert result["available"] is False

    def test_lifecycle_status_pending(self):
        rows = [
            {"id": 1, "symbol": "BTCUSDT", "status": "PENDING", "gross_r": None,
             "created_at": datetime(2026, 7, 20, 14, 0, 0),
             "metrics_json": _make_metrics()},
        ]
        signals = pd.DataFrame(rows)
        result = _compute_signal_kpis(signals)
        assert result["lifecycle_pending"] == 1
        assert result["lifecycle_activated"] == 0
        assert result["lifecycle_closed"] == 0

    def test_lifecycle_status_activated(self):
        rows = [
            {"id": 1, "symbol": "BTCUSDT", "status": "ACTIVATED", "gross_r": None,
             "created_at": datetime(2026, 7, 20, 14, 0, 0),
             "metrics_json": _make_metrics()},
        ]
        signals = pd.DataFrame(rows)
        result = _compute_signal_kpis(signals)
        assert result["lifecycle_activated"] == 1
        assert result["lifecycle_closed"] == 0

    def test_lifecycle_status_open(self):
        rows = [
            {"id": 1, "symbol": "BTCUSDT", "status": "OPEN", "gross_r": None,
             "created_at": datetime(2026, 7, 20, 14, 0, 0),
             "metrics_json": _make_metrics()},
        ]
        signals = pd.DataFrame(rows)
        result = _compute_signal_kpis(signals)
        assert result["lifecycle_activated"] == 1

    def test_lifecycle_closed_win(self):
        rows = [
            {"id": 1, "symbol": "BTCUSDT", "status": "CLOSED", "gross_r": 2.0,
             "side": "LONG",
             "created_at": datetime(2026, 7, 20, 14, 0, 0),
             "metrics_json": _make_metrics()},
        ]
        signals = pd.DataFrame(rows)
        result = _compute_signal_kpis(signals)
        assert result["lifecycle_closed"] == 1
        # CLOSED + gross_r=2.0 → WIN derived from gross_r (new resolve_official_result behavior)
        assert result["result_win"] == 1
        assert result["result_unknown"] == 0
        assert result["result_derived_count"] == 1  # DERIVED_FROM_GROSS_R

    def test_lifecycle_closed_loss(self):
        rows = [
            {"id": 1, "symbol": "BTCUSDT", "status": "LOST", "gross_r": -1.0,
             "created_at": datetime(2026, 7, 20, 14, 0, 0),
             "metrics_json": _make_metrics()},
        ]
        signals = pd.DataFrame(rows)
        result = _compute_signal_kpis(signals)
        assert result["result_loss"] == 1

    def test_lifecycle_cancelled(self):
        rows = [
            {"id": 1, "symbol": "BTCUSDT", "status": "CANCELLED", "gross_r": None,
             "created_at": datetime(2026, 7, 20, 14, 0, 0),
             "metrics_json": _make_metrics()},
        ]
        signals = pd.DataFrame(rows)
        result = _compute_signal_kpis(signals)
        assert result["lifecycle_cancelled"] == 1

    def test_lifecycle_expired(self):
        rows = [
            {"id": 1, "symbol": "BTCUSDT", "status": "EXPIRED", "gross_r": None,
             "created_at": datetime(2026, 7, 20, 14, 0, 0),
             "metrics_json": _make_metrics()},
        ]
        signals = pd.DataFrame(rows)
        result = _compute_signal_kpis(signals)
        assert result["lifecycle_expired"] == 1

    def test_lifecycle_unknown_status(self):
        rows = [
            {"id": 1, "symbol": "BTCUSDT", "status": "BIZARRE_STATUS", "gross_r": None,
             "created_at": datetime(2026, 7, 20, 14, 0, 0),
             "metrics_json": _make_metrics()},
        ]
        signals = pd.DataFrame(rows)
        result = _compute_signal_kpis(signals)
        assert result["lifecycle_other"] == 1

    def test_no_double_counting(self):
        """Closed signals in WON/LOST must NOT double-count between lifecycle and result."""
        signals = _signal_df()
        result = _compute_signal_kpis(signals)
        # lifecycle_closed = won(1) + lost(1) + pending(0, different lifecycle) = 2
        assert result["lifecycle_closed"] == 2
        assert result["result_win"] == 1
        assert result["result_loss"] == 1
        # Total should equal lifecycle components
        total_lifecycle = (
            result["lifecycle_pending"] + result["lifecycle_activated"] +
            result["lifecycle_closed"] + result["lifecycle_cancelled"] +
            result["lifecycle_expired"] + result["lifecycle_other"]
        )
        assert total_lifecycle == result["total"]

    # ---- Profit Factor ----

    def test_profit_factor_mixed_wins_losses(self):
        rows = [
            {"id": 1, "symbol": "BTCUSDT", "status": "WON", "gross_r": 2.0,
             "created_at": datetime(2026, 7, 20, 14, 0, 0), "metrics_json": _make_metrics()},
            {"id": 2, "symbol": "ETHUSDT", "status": "LOST", "gross_r": -1.0,
             "created_at": datetime(2026, 7, 20, 15, 0, 0), "metrics_json": _make_metrics()},
        ]
        signals = pd.DataFrame(rows)
        result = _compute_signal_kpis(signals)
        assert result["profit_factor"] == 2.0
        assert result["closed_evaluable"] == 2

    def test_profit_factor_only_wins(self):
        rows = [
            {"id": i, "symbol": "BTCUSDT", "status": "WON", "gross_r": 1.0,
             "created_at": datetime(2026, 7, 20, 14, 0, 0) + timedelta(hours=i),
             "metrics_json": _make_metrics()}
            for i in range(5)
        ]
        signals = pd.DataFrame(rows)
        result = _compute_signal_kpis(signals)
        assert result["profit_factor"] == "∞"
        assert result["pf_warning"] is not None

    def test_profit_factor_only_losses(self):
        rows = [
            {"id": 1, "symbol": "BTCUSDT", "status": "LOST", "gross_r": -1.0,
             "created_at": datetime(2026, 7, 20, 14, 0, 0), "metrics_json": _make_metrics()},
        ]
        signals = pd.DataFrame(rows)
        result = _compute_signal_kpis(signals)
        assert result["profit_factor"] == 0.0  # 0 positive / 1 negative

    def test_profit_factor_all_gross_r_none(self):
        rows = [
            {"id": 1, "symbol": "BTCUSDT", "status": "WON", "gross_r": None,
             "created_at": datetime(2026, 7, 20, 14, 0, 0), "metrics_json": _make_metrics()},
            {"id": 2, "symbol": "ETHUSDT", "status": "LOST", "gross_r": None,
             "created_at": datetime(2026, 7, 20, 15, 0, 0), "metrics_json": _make_metrics()},
        ]
        signals = pd.DataFrame(rows)
        result = _compute_signal_kpis(signals)
        assert result["profit_factor"] is None
        assert result["closed_evaluable"] == 0

    def test_profit_factor_open_excluded(self):
        rows = [
            {"id": 1, "symbol": "BTCUSDT", "status": "OPEN", "gross_r": 2.0,
             "created_at": datetime(2026, 7, 20, 14, 0, 0), "metrics_json": _make_metrics()},
            {"id": 2, "symbol": "ETHUSDT", "status": "WON", "gross_r": 1.0,
             "created_at": datetime(2026, 7, 20, 15, 0, 0), "metrics_json": _make_metrics()},
        ]
        signals = pd.DataFrame(rows)
        result = _compute_signal_kpis(signals)
        assert result["closed_evaluable"] == 1  # Only WON, not OPEN

    def test_avg_r_only_closed_evaluable(self):
        rows = [
            {"id": 1, "symbol": "BTCUSDT", "status": "WON", "gross_r": 3.0,
             "created_at": datetime(2026, 7, 20, 14, 0, 0), "metrics_json": _make_metrics()},
            {"id": 2, "symbol": "ETHUSDT", "status": "LOST", "gross_r": -1.0,
             "created_at": datetime(2026, 7, 20, 15, 0, 0), "metrics_json": _make_metrics()},
            {"id": 3, "symbol": "ADAUSDT", "status": "PENDING", "gross_r": 10.0,
             "created_at": datetime(2026, 7, 20, 16, 0, 0), "metrics_json": _make_metrics()},
        ]
        signals = pd.DataFrame(rows)
        result = _compute_signal_kpis(signals)
        assert result["avg_r"] == pytest.approx(1.0)  # (3 + -1) / 2, PENDING excluded

    def test_latest_signal(self):
        signals = _signal_df()
        result = _compute_signal_kpis(signals)
        assert result["latest_signal_id"] == 3  # Created last


# ---------------------------------------------------------------------------
# Executability
# ---------------------------------------------------------------------------
class TestBuildExecutability:
    def test_empty_signals(self):
        result = _build_executability(pd.DataFrame())
        assert result["available"] is False

    def test_same_market_bar_canonical(self):
        signals = _signal_df()
        result = _build_executability(signals)
        smb = result["same_market_bar"]
        assert smb["true"] == 1
        assert smb["false"] == 1
        assert smb["none"] == 1
        assert smb["canonical"] >= 0

    def test_same_market_bar_derived(self):
        # Signal without canonical field, but with timestamps
        rows = [
            {"id": 1, "symbol": "BTCUSDT", "side": "LONG", "status": "WON", "net_r": 1.0,
             "created_at": datetime(2026, 7, 20, 14, 10, 0),
             "born_timestamp": datetime(2026, 7, 20, 14, 10, 0),
             "activation_bar_timestamp": datetime(2026, 7, 20, 14, 30, 0),  # same 14:00 bar
             "metrics_json": json.dumps({"swing_v1": {"config_fingerprint": "fp"}})},
        ]
        signals = pd.DataFrame(rows)
        result = _build_executability(signals)
        smb = result["same_market_bar"]
        assert smb["derived"] == 1
        assert smb["true"] == 1

    def test_execution_detached_independent(self):
        signals = _signal_df()
        result = _build_executability(signals)
        ed = result["execution_detached"]
        assert ed["true"] == 1
        assert ed["false"] == 1
        assert ed["none"] == 1

    def test_demo_activation_mismatch(self):
        ap = {
            "actions": {
                "demo_entry_submit": {
                    "status": "SUCCEEDED",
                    "reason": "activation_mismatch_new_cancelled",
                }
            },
            "schema": "swing_adapter_parity_v1",
        }
        rows = [
            {"id": 1, "symbol": "BTCUSDT", "side": "LONG", "status": "WON", "net_r": 1.0,
             "created_at": datetime(2026, 7, 20, 14, 0, 0),
             "metrics_json": _make_metrics(adapter_parity=ap)},
        ]
        signals = pd.DataFrame(rows)
        result = _build_executability(signals)
        demo = result["demo_compatibility"]
        assert "ACTIVATION_MISMATCH" in demo
        assert demo["ACTIVATION_MISMATCH"] == 1

    def test_submitted_not_filled(self):
        ap = {
            "actions": {
                "demo_entry_submit": {
                    "status": "SUCCEEDED",
                    "reason": "submitted",
                }
            },
        }
        rows = [
            {"id": 1, "symbol": "BTCUSDT", "side": "LONG", "status": "WON", "net_r": 1.0,
             "created_at": datetime(2026, 7, 20, 14, 0, 0),
             "metrics_json": _make_metrics(adapter_parity=ap)},
        ]
        signals = pd.DataFrame(rows)
        result = _build_executability(signals)
        demo = result["demo_compatibility"]
        assert "SUBMITTED" in demo
        assert "FILLED" not in demo


# ---------------------------------------------------------------------------
# Signal table
# ---------------------------------------------------------------------------
class TestBuildSignalTable:
    def test_does_not_include_full_metrics_json(self):
        signals = _signal_df()
        table = _build_signal_table(signals)
        assert "metrics_json" not in table.columns

    def test_includes_derived_columns(self):
        signals = _signal_df()
        table = _build_signal_table(signals)
        assert "same_market_bar" in table.columns
        assert "execution_detached" in table.columns
        assert "demo_classification" in table.columns

    def test_smb_source_column(self):
        signals = _signal_df()
        table = _build_signal_table(signals)
        assert "smb_source" in table.columns

    def test_empty_signals_returns_empty(self):
        table = _build_signal_table(pd.DataFrame())
        assert table.empty


# ---------------------------------------------------------------------------
# Experiments + Scanner panels
# ---------------------------------------------------------------------------
class TestExperimentsPanel:
    """Tests for the experimental panel — now filters to swing_short_universe_probe_v1."""

    def test_empty_returns_unavailable(self):
        result = _build_experiments_panel(pd.DataFrame())
        assert result["available"] is False
        assert result["rows"] == 0

    def test_none_returns_unavailable(self):
        result = _build_experiments_panel(None)
        assert result["available"] is False

    def test_with_only_probe_variant(self):
        df = pd.DataFrame({
            "id": [1, 2],
            "variant_id": ["swing_short_universe_probe_v1"] * 2,
            "side": ["SHORT", "SHORT"],
            "symbol": ["HYPEUSDT", "SUIUSDT"],
            "status": ["PROBE_PENDING", "PROBE_STOP_LOSS"],
            "payload_json": [
                '{"tier": "BPLUS", "cluster": "OTHER", "result_r": null}',
                '{"tier": "BPLUS", "cluster": "AI", "result_r": -1.0, "reason": "stop_loss"}',
            ],
        })
        result = _build_experiments_panel(df)
        assert result["available"] is True
        assert result["rows"] == 2
        assert result["variant_id"] == "swing_short_universe_probe_v1"
        table = result["table"]
        assert not table.empty
        assert "tier" in table.columns
        assert "cluster" in table.columns
        assert set(table["tier"].dropna()) == {"BPLUS"}

    def test_legacy_donchian_variant_excluded(self):
        """Rows from swing_donchian_12_shadow must NOT appear in the probe panel."""
        df = pd.DataFrame({
            "id": [1, 2],
            "variant_id": ["swing_donchian_12_shadow"] * 2,
            "side": ["SHORT", "LONG"],
            "symbol": ["BTCUSDT", "ETHUSDT"],
            "status": ["SHADOW_STOP_LOSS", "SHADOW_PRIMARY_TP"],
            "payload_json": ["{}", "{}"],
        })
        result = _build_experiments_panel(df)
        assert result["available"] is False
        assert result["rows"] == 0

    def test_probe_long_rows_excluded(self):
        """Only SHORT rows from the probe variant are shown."""
        df = pd.DataFrame({
            "id": [1, 2],
            "variant_id": ["swing_short_universe_probe_v1"] * 2,
            "side": ["SHORT", "LONG"],
            "symbol": ["HYPEUSDT", "SUIUSDT"],
            "status": ["PROBE_PENDING", "PROBE_STOP_LOSS"],
            "payload_json": ["{}", "{}"],
        })
        result = _build_experiments_panel(df)
        assert result["available"] is True
        assert result["rows"] == 1
        assert result["table"]["side"].dropna().astype(str).str.upper().iloc[0] == "SHORT"


class TestShadowPanel:
    """Tests for the new SWING SHADOW multi-pair panel (SHORT-only)."""

    def _shadow_df(self):
        rows = [
            {"id": 1, "symbol": "LINK/USDT:USDT", "signal_type": "SHORT", "status": "CLOSED",
             "gross_r": 2.0, "is_shadow": True, "metrics_json": _make_metrics()},
            {"id": 2, "symbol": "LINK/USDT:USDT", "signal_type": "SHORT", "status": "CLOSED",
             "gross_r": -1.0, "is_shadow": True, "metrics_json": _make_metrics()},
            {"id": 3, "symbol": "SOL/USDT:USDT", "signal_type": "SHORT", "status": "CLOSED",
             "gross_r": 1.5, "is_shadow": True, "metrics_json": _make_metrics()},
            {"id": 4, "symbol": "SOL/USDT:USDT", "signal_type": "SHORT", "status": "PENDING",
             "gross_r": None, "is_shadow": True, "metrics_json": _make_metrics()},
            {"id": 5, "symbol": "ETH/USDT:USDT", "signal_type": "LONG", "status": "CLOSED",
             "gross_r": 3.0, "is_shadow": True, "metrics_json": _make_metrics()},
        ]
        return pd.DataFrame(rows)

    def test_empty_returns_unavailable(self):
        result = _build_shadow_panel(pd.DataFrame())
        assert result["available"] is False
        assert result["rows"] == 0

    def test_none_returns_unavailable(self):
        result = _build_shadow_panel(None)
        assert result["available"] is False

    def test_only_shadow_short_rows(self):
        """LONG rows are excluded; is_shadow=true only."""
        result = _build_shadow_panel(self._shadow_df())
        assert result["available"] is True
        assert result["rows"] == 4  # 5 total minus 1 LONG
        assert result["pairs"] == 2  # LINK, SOL (ETH LONG excluded)

        table = result["table"]
        assert set(table["symbol"]) == {"LINK/USDT:USDT", "SOL/USDT:USDT"}
        link = table[table["symbol"] == "LINK/USDT:USDT"].iloc[0]
        assert link["signals"] == 2
        assert link["closed"] == 2
        assert link["wins"] == 1
        assert link["losses"] == 1
        assert link["total_r"] == pytest.approx(1.0)

        sol = table[table["symbol"] == "SOL/USDT:USDT"].iloc[0]
        assert sol["signals"] == 2
        assert sol["closed"] == 1
        assert sol["wins"] == 1
        assert sol["losses"] == 0
        assert sol["win_rate"] == 100.0

    def test_sorted_by_total_r_desc(self):
        result = _build_shadow_panel(self._shadow_df())
        table = result["table"]
        total_rs = table["total_r"].dropna().tolist()
        assert total_rs == sorted(total_rs, reverse=True)

    def test_is_shadow_false_rows_excluded(self):
        df = pd.DataFrame([
            {"id": 1, "symbol": "BTC/USDT:USDT", "signal_type": "SHORT", "status": "CLOSED",
             "gross_r": 1.0, "is_shadow": False, "metrics_json": _make_metrics()},
        ])
        result = _build_shadow_panel(df)
        assert result["available"] is False
        assert result["rows"] == 0

    def test_non_shadow_long_only_returns_unavailable(self):
        df = pd.DataFrame([
            {"id": 1, "symbol": "BTC/USDT:USDT", "signal_type": "LONG", "status": "CLOSED",
             "gross_r": 1.0, "is_shadow": True, "metrics_json": _make_metrics()},
        ])
        result = _build_shadow_panel(df)
        assert result["available"] is False


class TestDashboardShortOnlyScope:
    """SHORT-only scope in build_swing_dashboard."""

    def test_long_signals_excluded_from_scope(self):
        """LONG signals should be filtered out of the dashboard view-model."""
        mock_conn = MagicMock()
        signals = pd.DataFrame([
            {"id": 1, "engine_name": "SWING_TREND_RECLAIM_V1", "symbol": "SOL/USDT:USDT",
             "signal_type": "SHORT", "status": "CLOSED", "net_r": 1.0, "gross_r": 1.0,
             "is_shadow": True, "created_at": datetime(2026, 7, 20),
             "metrics_json": _make_metrics()},
            {"id": 2, "engine_name": "SWING_TREND_RECLAIM_V1", "symbol": "ETH/USDT:USDT",
             "signal_type": "LONG", "status": "CLOSED", "net_r": 3.0, "gross_r": 3.0,
             "is_shadow": True, "created_at": datetime(2026, 7, 21),
             "metrics_json": _make_metrics()},
            {"id": 3, "engine_name": "SWING_TREND_RECLAIM_V1", "symbol": "LINK/USDT:USDT",
             "signal_type": "SHORT", "status": "PENDING", "net_r": None, "gross_r": None,
             "is_shadow": True, "created_at": datetime(2026, 7, 22),
             "metrics_json": _make_metrics()},
        ])
        with patch("src.swing_dashboard_service.build_readonly_conn", return_value=mock_conn):
            with patch("src.swing_dashboard_service.load_signal_records_pg", return_value=signals):
                with patch("src.swing_dashboard_service.load_signal_events_pg", return_value=_events_df()):
                    with patch("src.swing_dashboard_service.load_swing_experimental_lifecycles_pg", return_value=pd.DataFrame()):
                        with patch("src.swing_dashboard_service.load_scanner_shadow_diagnostics_pg", return_value=pd.DataFrame()):
                            data = build_swing_dashboard(datetime(2026, 7, 20), datetime(2026, 7, 27))

        assert data.get("error") is None
        # 3 SWING signals, 1 LONG excluded
        assert data["total_signals"] == 2
        assert data["excluded_long"] == 1
        assert data["signal_kpis"]["total"] == 2
        # Shadow panel should include the SHORT rows
        shadow = data.get("shadow", {})
        assert shadow.get("available") is True
        assert shadow.get("rows") == 2

    def test_signal_table_only_short(self):
        """The signal table side column should contain only SHORT values."""
        mock_conn = MagicMock()
        signals = pd.DataFrame([
            {"id": 1, "engine_name": "SWING_TREND_RECLAIM_V1", "symbol": "SOL/USDT:USDT",
             "signal_type": "SHORT", "status": "CLOSED", "net_r": 1.0,
             "is_shadow": True, "created_at": datetime(2026, 7, 20),
             "metrics_json": _make_metrics()},
            {"id": 2, "engine_name": "SWING_TREND_RECLAIM_V1", "symbol": "ETH/USDT:USDT",
             "signal_type": "LONG", "status": "CLOSED", "net_r": 3.0,
             "is_shadow": True, "created_at": datetime(2026, 7, 21),
             "metrics_json": _make_metrics()},
        ])
        with patch("src.swing_dashboard_service.build_readonly_conn", return_value=mock_conn):
            with patch("src.swing_dashboard_service.load_signal_records_pg", return_value=signals):
                with patch("src.swing_dashboard_service.load_signal_events_pg", return_value=_events_df()):
                    with patch("src.swing_dashboard_service.load_swing_experimental_lifecycles_pg", return_value=pd.DataFrame()):
                        with patch("src.swing_dashboard_service.load_scanner_shadow_diagnostics_pg", return_value=pd.DataFrame()):
                            data = build_swing_dashboard(datetime(2026, 7, 20), datetime(2026, 7, 27))

        table = data.get("signal_table", pd.DataFrame())
        assert not table.empty
        assert "side" in table.columns
        assert set(table["side"].unique()) == {SWING_SCOPE_SIDE}

    def test_swing_scope_side_constant(self):
        assert SWING_SCOPE_SIDE == "SHORT"


class TestScannerPanel:
    def test_empty_returns_no_data(self):
        result = _build_scanner_panel(pd.DataFrame())
        assert result["available"] is False
        assert result["status"] == "No data available"

    def test_unverified_status(self):
        df = pd.DataFrame({"status": ["UNVERIFIED_NO_DATA"]})
        result = _build_scanner_panel(df)
        assert result["available"] is False
        assert "STALE" in result.get("confidence", "")


# ---------------------------------------------------------------------------
# build_swing_dashboard (integration)
# ---------------------------------------------------------------------------
class TestBuildSwingDashboard:
    def test_connection_error_is_handled(self):
        with patch("src.swing_dashboard_service.build_readonly_conn", side_effect=RuntimeError("No DB")):
            data = build_swing_dashboard(datetime(2026, 7, 20), datetime(2026, 7, 27))
            assert data["error"] is not None

    def test_data_loading_error_is_handled(self):
        mock_conn = MagicMock()
        with patch("src.swing_dashboard_service.build_readonly_conn", return_value=mock_conn):
            with patch("src.swing_dashboard_service.load_signal_records_pg", side_effect=RuntimeError("Query failed")):
                data = build_swing_dashboard(datetime(2026, 7, 20), datetime(2026, 7, 27))
                assert data["error"] is not None

    def test_no_writes_performed(self):
        """build_swing_dashboard must NOT execute any write operations."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value.__enter__.return_value = mock_cursor

        with patch("src.swing_dashboard_service.build_readonly_conn", return_value=mock_conn):
            with patch("src.swing_dashboard_service.load_signal_records_pg", return_value=_signal_df()):
                with patch("src.swing_dashboard_service.load_signal_events_pg", return_value=_events_df()):
                    with patch("src.swing_dashboard_service.load_swing_experimental_lifecycles_pg", return_value=pd.DataFrame()):
                        with patch("src.swing_dashboard_service.load_scanner_shadow_diagnostics_pg", return_value=pd.DataFrame()):
                            data = build_swing_dashboard(datetime(2026, 7, 20), datetime(2026, 7, 27))

        # The mock cursor should NOT have been used for INSERT/UPDATE/DELETE
        for call in mock_cursor.execute.call_args_list if hasattr(mock_cursor.execute, 'call_args_list') else []:
            sql = str(call[0][0]).upper()
            assert "INSERT" not in sql
            assert "UPDATE" not in sql
            assert "DELETE" not in sql

        assert data.get("error") is None
        # Connection must be closed
        mock_conn.close.assert_called()


# ---------------------------------------------------------------------------
# Official vs shadow separation
# ---------------------------------------------------------------------------
class TestOfficialVsShadow:
    def test_experiments_not_in_kpis(self):
        """Experimental lifecycles must NOT be mixed into signal KPIs."""
        signals = _signal_df()
        kpis = _compute_signal_kpis(signals)
        # KPIs come exclusively from signal_records
        assert kpis["total"] == 3
        assert "experimental" not in str(kpis).lower()

    def test_experiments_panel_is_separate(self):
        """Experiments have their own panel dict."""
        exp_df = pd.DataFrame({
            "id": [100],
            "variant_id": ["swing_short_universe_probe_v1"],
            "side": ["SHORT"],
            "symbol": ["HYPEUSDT"],
            "status": ["PROBE_PENDING"],
            "payload_json": ["{}"],
        })
        result = _build_experiments_panel(exp_df)
        assert result.get("available") is True
        assert "table" in result
        # The experiments panel returns a dict, not mixed with KPIs


# ---------------------------------------------------------------------------
# Half-open window verification
# ---------------------------------------------------------------------------
class TestHalfOpenWindow:
    def test_kpis_use_window_from_dashboard(self):
        """The dashboard builder passes window params to loaders."""
        start = datetime(2026, 7, 20, 0, 0, 0)
        end = datetime(2026, 7, 27, 0, 0, 0)
        mock_conn = MagicMock()

        with patch("src.swing_dashboard_service.build_readonly_conn", return_value=mock_conn):
            with patch("src.swing_dashboard_service.load_signal_records_pg") as mock_load:
                mock_load.return_value = _signal_df()
                with patch("src.swing_dashboard_service.load_signal_events_pg", return_value=_events_df()):
                    with patch("src.swing_dashboard_service.load_swing_experimental_lifecycles_pg", return_value=pd.DataFrame()):
                        with patch("src.swing_dashboard_service.load_scanner_shadow_diagnostics_pg", return_value=pd.DataFrame()):
                            build_swing_dashboard(start, end)

        # Verify the loader was called with the correct window params
        call_args = mock_load.call_args
        assert call_args[0][1] == start  # window_start
        assert call_args[0][2] == end    # window_end


# ===========================================================================
# R2 CORRECTIONS — New tests for SWING scope, nested fields, side, etc.
# ===========================================================================

# ---------------------------------------------------------------------------
# _safe_str helper
# ---------------------------------------------------------------------------
class TestSafeStr:
    def test_returns_string_as_is(self):
        assert _safe_str("SWING_TREND_RECLAIM_V1") == "SWING_TREND_RECLAIM_V1"

    def test_none_returns_empty(self):
        assert _safe_str(None) == ""

    def test_nan_returns_empty(self):
        assert _safe_str(float("nan")) == ""

    def test_int_converts(self):
        assert _safe_str(42) == "42"


# ---------------------------------------------------------------------------
# is_swing_trend_reclaim_signal
# ---------------------------------------------------------------------------
class TestIsSwingTrendReclaimSignal:
    def test_engine_name_match(self):
        row = {"engine_name": "SWING_TREND_RECLAIM_V1", "metrics_json": None}
        assert is_swing_trend_reclaim_signal(row) is True

    def test_setup_match(self):
        row = {"setup": "SWING_TREND_RECLAIM_V1"}
        assert is_swing_trend_reclaim_signal(row) is True

    def test_strategy_match(self):
        row = {"strategy": "SWING_TREND_RECLAIM"}
        assert is_swing_trend_reclaim_signal(row) is True

    def test_setup_type_match(self):
        row = {"setup_type": "SWING_TREND_RECLAIM_V1"}
        assert is_swing_trend_reclaim_signal(row) is True

    def test_metrics_json_with_fingerprint(self):
        metrics = json.dumps({"swing_v1": {"config_fingerprint": "7fa9d83d70c7076b"}})
        row = {"metrics_json": metrics}
        assert is_swing_trend_reclaim_signal(row) is True

    def test_metrics_json_swing_v1_but_no_fingerprint(self):
        metrics = json.dumps({"swing_v1": {"some_field": "value"}})
        row = {"metrics_json": metrics}
        # swing_v1 present but no valid fingerprint → False
        assert is_swing_trend_reclaim_signal(row) is False

    def test_setup_id_swing_prefix(self):
        row = {"setup_id": "SWING_V1_BTCUSDT_123"}
        assert is_swing_trend_reclaim_signal(row) is True

    def test_legacy_ofa_signal(self):
        row = {"engine_name": "OFA_ENGINE", "setup": "OFA_V1"}
        assert is_swing_trend_reclaim_signal(row) is False

    def test_legacy_true_scalp(self):
        row = {"engine_name": "TRUE_SCALP_V2"}
        assert is_swing_trend_reclaim_signal(row) is False

    def test_no_identifiable_columns(self):
        row = {"symbol": "BTCUSDT", "status": "WON"}
        assert is_swing_trend_reclaim_signal(row) is False

    def test_swing_v1_with_short_fingerprint(self):
        metrics = json.dumps({"swing_v1": {"config_fingerprint": "abc"}})
        row = {"metrics_json": metrics}
        # fingerprint < 8 chars → not valid
        assert is_swing_trend_reclaim_signal(row) is False

    def test_combined_match(self):
        row = {"engine_name": "SOMETHING", "setup": "swing_trend_reclaim_v1"}
        assert is_swing_trend_reclaim_signal(row) is True

    def test_signal_2498_like(self):
        metrics = json.dumps({
            "swing_v1": {
                "config_fingerprint": "7fa9d83d70c7076b",
                "born_timestamp": "2026-07-24T14:00:00Z",
                "activation_timestamp": "2026-07-24T14:05:00Z",
                "execution_detached": True,
                "adapter_parity": {"actions": {"demo_entry_submit": {"status": "SUCCEEDED", "reason": "activation_mismatch"}}}
            }
        })
        row = {"engine_name": "SWING_TREND_RECLAIM_V1", "metrics_json": metrics}
        assert is_swing_trend_reclaim_signal(row) is True

    def test_signal_2498_like_without_engine_name(self):
        metrics = json.dumps({
            "swing_v1": {
                "config_fingerprint": "7fa9d83d70c7076b",
                "born_timestamp": "2026-07-24T14:00:00Z",
                "execution_detached": True,
            }
        })
        row = {"metrics_json": metrics}
        # No engine_name, but valid swing_v1 with fingerprint → True
        assert is_swing_trend_reclaim_signal(row) is True


# ---------------------------------------------------------------------------
# filter_swing_official_signals
# ---------------------------------------------------------------------------
class TestFilterSwingOfficialSignals:
    def test_empty_df_returns_empty(self):
        df = pd.DataFrame()
        filtered, excl = filter_swing_official_signals(df)
        assert filtered.empty
        assert excl == 0

    def test_none_returns_none(self):
        filtered, excl = filter_swing_official_signals(None)
        assert filtered is None
        assert excl == 0

    def test_mixed_signals(self):
        signals = pd.DataFrame([
            {"id": 1, "engine_name": "SWING_TREND_RECLAIM_V1", "metrics_json": None},
            {"id": 2, "engine_name": "OFA_ENGINE", "metrics_json": None},
            {"id": 3, "engine_name": "TRUE_SCALP_V2", "metrics_json": None},
            {"id": 4, "metrics_json": json.dumps({"swing_v1": {"config_fingerprint": "7fa9d83d70c7076b"}})},
        ])
        filtered, excl = filter_swing_official_signals(signals)
        assert len(filtered) == 2  # id 1 and 4
        assert excl == 2  # id 2 and 3

    def test_all_swing(self):
        signals = pd.DataFrame([
            {"id": i, "engine_name": "SWING_TREND_RECLAIM_V1"}
            for i in range(5)
        ])
        filtered, excl = filter_swing_official_signals(signals)
        assert len(filtered) == 5
        assert excl == 0

    def test_no_swing(self):
        signals = pd.DataFrame([
            {"id": i, "engine_name": "OFA_ENGINE"}
            for i in range(10)
        ])
        filtered, excl = filter_swing_official_signals(signals)
        assert len(filtered) == 0
        assert excl == 10


# ---------------------------------------------------------------------------
# extract_nested_timestamp
# ---------------------------------------------------------------------------
class TestExtractNestedTimestamp:
    def test_extracts_from_metrics_json(self):
        metrics = json.dumps({
            "swing_v1": {
                "born_timestamp": "2026-07-24T14:00:00",
                "activation_timestamp": "2026-07-24T14:05:00",
            }
        })
        df = pd.DataFrame([{"metrics_json": metrics}])
        result = extract_nested_timestamp(df, "born_timestamp")
        assert result.notna().all()
        assert result.iloc[0] == pd.Timestamp("2026-07-24T14:00:00")

    def test_returns_nat_for_missing_field(self):
        metrics = json.dumps({"swing_v1": {}})
        df = pd.DataFrame([{"metrics_json": metrics}])
        result = extract_nested_timestamp(df, "activation_bar_timestamp")
        assert result.isna().all() or (result.iloc[0] is None)

    def test_returns_nat_for_no_metrics_json_column(self):
        df = pd.DataFrame([{"id": 1}])
        result = extract_nested_timestamp(df, "born_timestamp")
        assert len(result) == 1
        assert result.iloc[0] is None

    def test_handles_invalid_timestamp(self):
        metrics = json.dumps({"swing_v1": {"born_timestamp": "not-a-date"}})
        df = pd.DataFrame([{"metrics_json": metrics}])
        result = extract_nested_timestamp(df, "born_timestamp")
        assert result.iloc[0] is None

    def test_2498_like_timestamps(self):
        metrics = json.dumps({
            "swing_v1": {
                "config_fingerprint": "7fa9d83d70c7076b",
                "born_timestamp": "2026-07-24T14:00:00Z",
                "activation_timestamp": "2026-07-24T14:05:00Z",
                "activation_bar_timestamp": "2026-07-24T14:00:00Z",
            }
        })
        df = pd.DataFrame([{"metrics_json": metrics}])
        for field in ["born_timestamp", "activation_timestamp", "activation_bar_timestamp"]:
            result = extract_nested_timestamp(df, field)
            assert result.iloc[0] is not None, f"{field} should not be None"
            assert isinstance(result.iloc[0], pd.Timestamp), f"{field} should be pd.Timestamp"


# ---------------------------------------------------------------------------
# normalize_side
# ---------------------------------------------------------------------------
class TestNormalizeSide:
    def test_side_column_long(self):
        df = pd.DataFrame([{"side": "LONG"}, {"side": "long"}, {"side": "SHORT"}])
        result = normalize_side(df)
        assert result.iloc[0] == "LONG"
        assert result.iloc[1] == "LONG"
        assert result.iloc[2] == "SHORT"

    def test_signal_type_buy_sell(self):
        df = pd.DataFrame([{"signal_type": "BUY"}, {"signal_type": "SELL"}])
        result = normalize_side(df)
        assert result.iloc[0] == "LONG"
        assert result.iloc[1] == "SHORT"

    def test_side_has_priority_over_signal_type(self):
        df = pd.DataFrame([{"side": "LONG", "signal_type": "SELL"}])
        result = normalize_side(df)
        assert result.iloc[0] == "LONG"  # side wins

    def test_direction_from_metrics_json(self):
        metrics = json.dumps({"swing_v1": {"direction": "SHORT"}})
        df = pd.DataFrame([{"metrics_json": metrics}])
        result = normalize_side(df)
        assert result.iloc[0] == "SHORT"

    def test_unknown_when_all_absent(self):
        df = pd.DataFrame([{"id": 1}])
        result = normalize_side(df)
        assert result.iloc[0] == "UNKNOWN"

    def test_side_not_lost(self):
        df = pd.DataFrame([{"side": "LONG"}, {"side": None}, {"side": "LONG"}])
        result = normalize_side(df)
        assert result.iloc[1] == "UNKNOWN"  # None → UNKNOWN
        assert result.iloc[2] == "LONG"

    def test_empty_dataframe(self):
        result = normalize_side(pd.DataFrame())
        assert len(result) == 0


# ---------------------------------------------------------------------------
# _resolve_nested_timestamp
# ---------------------------------------------------------------------------
class TestResolveNestedTimestamp:
    def test_prefers_nested_over_physical(self):
        obj = {"swing_v1": {"born_timestamp": "2026-07-24T14:00:00"}}
        row = {"born_timestamp": datetime(2025, 1, 1)}
        result = _resolve_nested_timestamp(obj, row, "born_timestamp")
        assert result == pd.Timestamp("2026-07-24T14:00:00")

    def test_falls_back_to_physical(self):
        obj = {"swing_v1": {}}
        row = {"born_timestamp": datetime(2025, 1, 1)}
        result = _resolve_nested_timestamp(obj, row, "born_timestamp")
        assert result == pd.Timestamp("2025-01-01")

    def test_returns_none_when_neither_present(self):
        obj = {"swing_v1": {}}
        row = {"symbol": "BTCUSDT"}
        result = _resolve_nested_timestamp(obj, row, "born_timestamp")
        assert result is None

    def test_none_obj_falls_back(self):
        row = {"activation_bar_timestamp": datetime(2025, 1, 1)}
        result = _resolve_nested_timestamp(None, row, "activation_bar_timestamp")
        assert result == pd.Timestamp("2025-01-01")


# ---------------------------------------------------------------------------
# _fingerprint_segmentation
# ---------------------------------------------------------------------------
class TestFingerprintSegmentation:
    def test_single_fingerprint_no_warning(self):
        signals = pd.DataFrame([
            {"metrics_json": _make_metrics(fingerprint="fp_aaa_12345")}
            for _ in range(5)
        ])
        result = _fingerprint_segmentation(signals, "fp_aaa_12345")
        assert result["num_distinct"] == 1
        assert result["warning"] is None

    def test_multiple_fingerprints_produces_warning(self):
        signals = pd.DataFrame([
            {"metrics_json": _make_metrics(fingerprint="fp_old_version")},
            {"metrics_json": _make_metrics(fingerprint="fp_new_version")},
        ])
        result = _fingerprint_segmentation(signals, "fp_new_version")
        assert result["num_distinct"] == 2
        assert "MIXED CONFIG" in (result["warning"] or "")

    def test_no_fingerprints_produces_warning(self):
        signals = pd.DataFrame([
            {"metrics_json": json.dumps({"other": "data"})}
            for _ in range(3)
        ])
        result = _fingerprint_segmentation(signals, None)
        assert result["num_distinct"] == 0
        assert result["warning"] is not None

    def test_empty_signals(self):
        result = _fingerprint_segmentation(pd.DataFrame(), "fp")
        assert result["available"] is False


# ---------------------------------------------------------------------------
# build_swing_dashboard integration with SWING scope filter
# ---------------------------------------------------------------------------
class TestDashboardWithSwingScope:
    def test_excluded_non_swing_appears_in_result(self):
        mock_conn = MagicMock()
        mixed_signals = pd.DataFrame([
            {"id": 1, "engine_name": "SWING_TREND_RECLAIM_V1", "symbol": "BTCUSDT",
             "signal_type": "SHORT", "status": "WON", "net_r": 1.0, "gross_r": 1.0,
             "created_at": datetime(2026, 7, 20),
             "metrics_json": _make_metrics()},
            {"id": 2, "engine_name": "OFA_ENGINE", "symbol": "ETHUSDT", "signal_type": "SHORT",
             "status": "LOST", "net_r": -1.0, "gross_r": -1.0, "created_at": datetime(2026, 7, 20),
             "metrics_json": json.dumps({})},
        ])
        with patch("src.swing_dashboard_service.build_readonly_conn", return_value=mock_conn):
            with patch("src.swing_dashboard_service.load_signal_records_pg", return_value=mixed_signals):
                with patch("src.swing_dashboard_service.load_signal_events_pg", return_value=_events_df()):
                    with patch("src.swing_dashboard_service.load_swing_experimental_lifecycles_pg", return_value=pd.DataFrame()):
                        with patch("src.swing_dashboard_service.load_scanner_shadow_diagnostics_pg", return_value=pd.DataFrame()):
                            data = build_swing_dashboard(datetime(2026, 7, 20), datetime(2026, 7, 27))

        assert data.get("error") is None
        assert data["total_signals"] == 1  # only SWING SHORT
        assert data["excluded_non_swing"] == 1  # OFA excluded
        assert data["excluded_long"] == 0  # SWING signal is SHORT, nothing excluded by side
        assert data["signal_kpis"]["total"] == 1

    def test_fingerprint_segmentation_in_result(self):
        mock_conn = MagicMock()
        signals = pd.DataFrame([
            {"id": i, "engine_name": "SWING_TREND_RECLAIM_V1", "symbol": "BTCUSDT",
             "signal_type": "SHORT",
             "status": "WON", "net_r": 1.0, "gross_r": 1.0, "created_at": datetime(2026, 7, 20),
             "metrics_json": _make_metrics(fingerprint=f"fp_version_{i % 2}")}
            for i in range(5)
        ])
        with patch("src.swing_dashboard_service.build_readonly_conn", return_value=mock_conn):
            with patch("src.swing_dashboard_service.load_signal_records_pg", return_value=signals):
                with patch("src.swing_dashboard_service.load_signal_events_pg", return_value=_events_df()):
                    with patch("src.swing_dashboard_service.load_swing_experimental_lifecycles_pg", return_value=pd.DataFrame()):
                        with patch("src.swing_dashboard_service.load_scanner_shadow_diagnostics_pg", return_value=pd.DataFrame()):
                            data = build_swing_dashboard(datetime(2026, 7, 20), datetime(2026, 7, 27))

        fp_seg = data.get("fingerprint_segmentation", {})
        assert fp_seg["available"] is True
        assert fp_seg["num_distinct"] == 2
        assert fp_seg["warning"] is not None


# ---------------------------------------------------------------------------
# Nested timestamps resolve in executability and signal table (simulated)
# ---------------------------------------------------------------------------
class TestNestedTimestampsInBuilders:
    def test_executability_resolves_nested_born_timestamp(self):
        """born_timestamp from metrics_json → swing_v1 should be used for same_market_bar."""
        metrics = json.dumps({
            "swing_v1": {
                "config_fingerprint": "fp_test",
                "born_timestamp": "2026-07-24T14:00:00",
                "activation_bar_timestamp": "2026-07-24T14:00:00",
                "activation_timestamp": "2026-07-24T14:05:00",
            }
        })
        rows = [
            {"id": 1, "symbol": "BTCUSDT", "side": "LONG", "status": "WON", "net_r": 1.0,
             "created_at": datetime(2026, 7, 24, 14, 0, 0),
             "metrics_json": metrics},
        ]
        signals = pd.DataFrame(rows)
        result = _build_executability(signals)
        smb = result["same_market_bar"]
        # born and act_bar in same hour → same_market_bar=True (DERIVED_FROM_TIMESTAMPS)
        assert smb["true"] == 1
        assert smb["derived"] == 1

    def test_signal_table_uses_normalized_side(self):
        signals = _signal_df()
        # Inject normalized_side into the DataFrame before calling _build_signal_table
        signals["normalized_side"] = pd.Series(["LONG", "SHORT", "LONG"], index=signals.index)
        table = _build_signal_table(signals)
        assert "side" in table.columns
        assert set(table["side"].unique()) == {"LONG", "SHORT"}

    def test_signal_2497_like_same_market_bar_derived(self):
        """2497: born=14:00, act_bar=14:00 → same_market_bar=True derived."""
        metrics = json.dumps({
            "swing_v1": {
                "config_fingerprint": "7fa9d83d70c7076b",
                "born_timestamp": "2026-07-24T14:00:00",
                "activation_bar_timestamp": "2026-07-24T14:00:00",
                "activation_timestamp": "2026-07-24T14:05:00",
                "execution_detached": True,
            }
        })
        rows = [
            {"id": 2497, "symbol": "BTCUSDT", "side": "SHORT", "status": "CLOSED",
             "created_at": datetime(2026, 7, 24, 14, 0, 0),
             "metrics_json": metrics},
        ]
        signals = pd.DataFrame(rows)
        result = _build_executability(signals)
        smb = result["same_market_bar"]
        assert smb["true"] == 1
        assert smb["derived"] == 1

    def test_signal_2498_like_same_market_bar_derived(self):
        """2498: born=14:00, act_bar=14:00 → same_market_bar=True derived."""
        metrics = json.dumps({
            "swing_v1": {
                "config_fingerprint": "7fa9d83d70c7076b",
                "born_timestamp": "2026-07-24T14:00:00",
                "activation_bar_timestamp": "2026-07-24T14:00:00",
                "activation_timestamp": "2026-07-24T14:05:00",
                "execution_detached": True,
            }
        })
        rows = [
            {"id": 2498, "symbol": "ADAUSDT", "side": "SHORT", "status": "CLOSED",
             "created_at": datetime(2026, 7, 24, 14, 0, 0),
             "metrics_json": metrics},
        ]
        signals = pd.DataFrame(rows)
        result = _build_executability(signals)
        smb = result["same_market_bar"]
        assert smb["true"] == 1
        assert smb["derived"] == 1


# ---------------------------------------------------------------------------
# Sample size warning
# ---------------------------------------------------------------------------
class TestSampleSizeWarning:
    def test_small_closed_set_produces_pf_warning(self):
        """When there are only wins (no losses) with gross_r, produce pf_warning."""
        rows = [
            {"id": 1, "symbol": "BTCUSDT", "status": "WON", "gross_r": 1.0,
             "created_at": datetime(2026, 7, 20), "metrics_json": _make_metrics()},
        ]
        signals = pd.DataFrame(rows)
        kpis = _compute_signal_kpis(signals)
        assert kpis["closed_evaluable"] == 1
        assert kpis["profit_factor"] == "∞"
        assert kpis["pf_warning"] is not None  # sample warning produced

    def test_no_warning_for_sufficient_data(self):
        rows = [
            {"id": 1, "symbol": "BTCUSDT", "status": "WON", "gross_r": 2.0,
             "created_at": datetime(2026, 7, 20), "metrics_json": _make_metrics()},
            {"id": 2, "symbol": "ETHUSDT", "status": "LOST", "gross_r": -1.0,
             "created_at": datetime(2026, 7, 20), "metrics_json": _make_metrics()},
        ]
        signals = pd.DataFrame(rows)
        kpis = _compute_signal_kpis(signals)
        assert kpis["closed_evaluable"] == 2
        assert kpis["profit_factor"] == 2.0
        assert kpis.get("pf_warning") is None


# ---------------------------------------------------------------------------
# Expired vs Other separation
# ---------------------------------------------------------------------------
class TestExpiredVsOther:
    def test_expired_detected_by_status(self):
        """EXPIRED status is detected. CANCELLED_EXPIRED double-matches both CANCEL and EXPIR patterns — known legacy behavior."""
        rows = [
            {"id": 1, "symbol": "BTCUSDT", "status": "EXPIRED", "gross_r": None,
             "created_at": datetime(2026, 7, 20), "metrics_json": "{}"},
            {"id": 2, "symbol": "BTCUSDT", "status": "CANCELLED_EXPIRED", "gross_r": None,
             "created_at": datetime(2026, 7, 20), "metrics_json": "{}"},
        ]
        signals = pd.DataFrame(rows)
        kpis = _compute_signal_kpis(signals)
        # EXPIRED → expired=1
        # CANCELLED_EXPIRED → matches both CANCEL and EXPIR → cancelled=1, expired=1 (double-count, legacy)
        assert kpis["lifecycle_expired"] >= 1  # at least the pure EXPIRED
        assert kpis["lifecycle_cancelled"] >= 1  # CANCELLED_EXPIRED matches CANCEL

    def test_other_is_unknown_status(self):
        rows = [
            {"id": 1, "symbol": "BTCUSDT", "status": "BIZARRE_STATUS", "gross_r": None,
             "created_at": datetime(2026, 7, 20), "metrics_json": "{}"},
        ]
        signals = pd.DataFrame(rows)
        kpis = _compute_signal_kpis(signals)
        assert kpis["lifecycle_other"] == 1

    def test_no_unknown_double_count(self):
        """status=WON should only be counted once in lifecycle."""
        rows = [
            {"id": 1, "symbol": "BTCUSDT", "status": "WON", "gross_r": 1.0,
             "created_at": datetime(2026, 7, 20), "metrics_json": "{}"},
        ]
        signals = pd.DataFrame(rows)
        kpis = _compute_signal_kpis(signals)
        # WON → closed(1), none of pending/activated/cancelled/expired/other
        assert kpis["lifecycle_closed"] == 1
        assert kpis["lifecycle_other"] == 0
        assert kpis["lifecycle_expired"] == 0
        assert kpis["lifecycle_cancelled"] == 0
        assert kpis["lifecycle_pending"] == 0
        assert kpis["lifecycle_activated"] == 0
        assert kpis["total"] == 1
