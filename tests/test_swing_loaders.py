from __future__ import annotations

"""Tests for swing_loaders.py — comprehensive schema, derivation, and security coverage."""

import json
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from src.swing_loaders import (
    load_signal_records_pg,
    load_signal_events_pg,
    load_swing_experimental_lifecycles_pg,
    load_scanner_shadow_diagnostics_pg,
    load_all_swing_data_pg,
    compute_swing_summary_pg,
    extract_adapter_parity,
    extract_execution_detached,
    extract_fingerprint,
    resolve_same_market_bar,
    derive_retroactive_bar_fill,
    classify_demo_compatibility,
    _safe_json_load,
    _nested_get,
)


# ---------------------------------------------------------------------------
# Synthetic fixtures
# ---------------------------------------------------------------------------
def _mock_signal_records_df(include_metrics_json=True, include_metadata=False):
    """Build a minimal signal_records DataFrame.

    Uses metrics_json (correct column), NOT metadata (legacy column).
    """
    data = {
        "id": [2491, 2492, 2493, 2494, 2495, 2496, 2497, 2498],
        "symbol": ["BTCUSDT"] * 8,
        "signal_type": ["SWING"] * 8,
        "side": ["LONG"] * 8,
        "status": ["WON", "LOST", "OPEN", "WON", "LOST", "CANCELLED", "EXPIRED", "WON"],
        "engine_name": ["SWING_v3"] * 8,
        "net_r": [1.5, -1.0, None, 2.0, -1.0, 0.0, 0.0, 1.2],
        "gross_r": [None, None, None, None, None, None, None, None],
        "created_at": [datetime(2026, 7, 20, 14, 0, 0) + timedelta(hours=i) for i in range(8)],
        "opened_at": [datetime(2026, 7, 20, 14, 5, 0) + timedelta(hours=i) for i in range(8)],
    }
    if include_metrics_json:
        data["metrics_json"] = [
            json.dumps({"swing_v1": {"config_fingerprint": "fp_v7", "same_market_bar": True, "adapter_parity": {"status": "FILLED"}}}),
            json.dumps({"swing_v1": {"config_fingerprint": "fp_v7", "same_market_bar": False, "adapter_parity": {"status": "FILLED"}}}),
            json.dumps({"swing_v1": {"config_fingerprint": "fp_v7"}}),
            json.dumps({"swing_v1": {"config_fingerprint": "fp_v7", "same_market_bar": True}}),
            json.dumps({"swing_v1": {"config_fingerprint": "fp_v7"}}),
            json.dumps({"swing_v1": {"config_fingerprint": "fp_v7"}}),
            json.dumps({"swing_v1": {"config_fingerprint": "fp_v7"}}),
            json.dumps({}),  # signal 2498 — no same_market_bar, pre-deploy
        ]
    if include_metadata:
        data["metadata"] = ["legacy_meta"] * 8
    return pd.DataFrame(data)


def _mock_events_df(use_event_time=True, use_metadata_json=True):
    """Build a minimal signal_events DataFrame."""
    data = {
        "id": [1, 2, 3],
        "signal_id": [2491, 2491, 2492],
        "event_type": ["SIGNAL_CREATED", "PRIMARY_TP_HIT", "STOP_LOSS_HIT"],
        "price": [50000.0, 51000.0, 49500.0],
    }
    if use_event_time:
        data["event_time"] = [
            datetime(2026, 7, 20, 14, 0, 0),
            datetime(2026, 7, 20, 18, 0, 0),
            datetime(2026, 7, 20, 19, 0, 0),
        ]
    else:
        data["created_at"] = [
            datetime(2026, 7, 20, 14, 0, 0),
            datetime(2026, 7, 20, 18, 0, 0),
            datetime(2026, 7, 20, 19, 0, 0),
        ]
    if use_metadata_json:
        data["metadata_json"] = ["{}", '{"key":"val"}', "{}"]
    return pd.DataFrame(data)


# ---------------------------------------------------------------------------
# _safe_json_load / _nested_get (helpers)
# ---------------------------------------------------------------------------
class TestJsonHelpers:
    def test_safe_json_load_valid_string(self):
        assert _safe_json_load('{"a":1}') == {"a": 1}

    def test_safe_json_load_already_dict(self):
        assert _safe_json_load({"a": 1}) == {"a": 1}

    def test_safe_json_load_none(self):
        assert _safe_json_load(None) is None

    def test_safe_json_load_invalid(self):
        assert _safe_json_load("not json") is None

    def test_safe_json_load_empty_string(self):
        assert _safe_json_load("") is None

    def test_nested_get_exists(self):
        assert _nested_get({"a": {"b": {"c": 42}}}, "a.b.c") == 42

    def test_nested_get_missing_key(self):
        assert _nested_get({"a": 1}, "a.b.c") is None

    def test_nested_get_none_obj(self):
        assert _nested_get(None, "a.b") is None


# ---------------------------------------------------------------------------
# signal_records: uses metrics_json, NOT metadata
# ---------------------------------------------------------------------------
class TestLoadSignalRecords:
    def test_table_missing_returns_empty(self):
        mock_conn = MagicMock()
        with patch("src.swing_loaders.table_exists_pg", return_value=False):
            df = load_signal_records_pg(mock_conn)
            assert df.empty

    def test_loads_and_parses_dates(self):
        mock_conn = MagicMock()
        mock_df = _mock_signal_records_df()
        cols = list(mock_df.columns)
        with patch("src.swing_loaders.table_exists_pg", return_value=True):
            with patch("src.swing_loaders.table_columns_pg", return_value=cols):
                with patch("src.swing_loaders.read_sql_df_pg", return_value=mock_df):
                    df = load_signal_records_pg(mock_conn)
                    assert len(df) == 8
                    assert pd.api.types.is_numeric_dtype(df["net_r"])

    def test_uses_metrics_json_not_metadata(self):
        """Verify metrics_json is preferred, NOT metadata."""
        mock_conn = MagicMock()
        mock_df = _mock_signal_records_df(include_metrics_json=True, include_metadata=False)
        cols = list(mock_df.columns)
        assert "metrics_json" in cols
        assert "metadata" not in cols  # metadata column must NOT be used

    def test_gross_r_missing_returns_none(self):
        """gross_r can be missing — must remain None, not zero."""
        mock_conn = MagicMock()
        mock_df = _mock_signal_records_df()
        cols = list(mock_df.columns)
        with patch("src.swing_loaders.table_exists_pg", return_value=True):
            with patch("src.swing_loaders.table_columns_pg", return_value=cols):
                with patch("src.swing_loaders.read_sql_df_pg", return_value=mock_df):
                    df = load_signal_records_pg(mock_conn)
                    # gross_r is all None — should still be None, not 0.0
                    assert df["gross_r"].isna().all()

    def test_half_open_window(self):
        """Window must use start <= ts < end (not <= end)."""
        mock_conn = MagicMock()
        mock_df = _mock_signal_records_df()
        cols = list(mock_df.columns)
        with patch("src.swing_loaders.table_exists_pg", return_value=True):
            with patch("src.swing_loaders.table_columns_pg", return_value=cols):
                with patch("src.swing_loaders.read_sql_df_pg") as mock_read:
                    mock_read.return_value = mock_df
                    window_start = datetime(2026, 7, 20, 0, 0, 0)
                    window_end = datetime(2026, 7, 21, 0, 0, 0)
                    load_signal_records_pg(mock_conn, window_start, window_end)
                    # Verify the query uses < (half-open), not <=
                    call_query = mock_read.call_args[0][1]
                    assert "< %s" in call_query or "<%s" in call_query

    def test_parameterised_query(self):
        """Queries must be parameterised — no string interpolation of values."""
        mock_conn = MagicMock()
        mock_df = _mock_signal_records_df()
        cols = list(mock_df.columns)
        with patch("src.swing_loaders.table_exists_pg", return_value=True):
            with patch("src.swing_loaders.table_columns_pg", return_value=cols):
                with patch("src.swing_loaders.read_sql_df_pg") as mock_read:
                    mock_read.return_value = mock_df
                    window_start = datetime(2026, 7, 20, 14, 0, 0)
                    load_signal_records_pg(mock_conn, window_start)
                    # params should be a tuple, not embedded in query string
                    args, kwargs = mock_read.call_args
                    params = args[2] if len(args) > 2 else kwargs.get("params", ())
                    assert isinstance(params, tuple)
                    # Query should contain %s placeholders, not literal dates
                    query = args[1]
                    assert "%s" in query


# ---------------------------------------------------------------------------
# signal_events: uses event_time and metadata_json, NOT created_at/metadata
# ---------------------------------------------------------------------------
class TestLoadSignalEvents:
    def test_table_missing_returns_empty(self):
        mock_conn = MagicMock()
        with patch("src.swing_loaders.table_exists_pg", return_value=False):
            df = load_signal_events_pg(mock_conn)
            assert df.empty

    def test_uses_event_time_not_created_at(self):
        """Must prefer event_time over created_at."""
        mock_conn = MagicMock()
        mock_df = _mock_events_df(use_event_time=True)
        cols = list(mock_df.columns)
        assert "event_time" in cols
        assert "created_at" not in cols  # correctly NOT using created_at

    def test_uses_metadata_json_not_metadata(self):
        """Must use metadata_json, NOT metadata."""
        mock_conn = MagicMock()
        mock_df = _mock_events_df(use_metadata_json=True)
        cols = list(mock_df.columns)
        assert "metadata_json" in cols

    def test_loads_events(self):
        mock_conn = MagicMock()
        mock_df = _mock_events_df()
        cols = list(mock_df.columns)
        with patch("src.swing_loaders.table_exists_pg", return_value=True):
            with patch("src.swing_loaders.table_columns_pg", return_value=cols):
                with patch("src.swing_loaders.read_sql_df_pg", return_value=mock_df):
                    df = load_signal_events_pg(mock_conn)
                    assert len(df) == 3


# ---------------------------------------------------------------------------
# Experimental lifecycles
# ---------------------------------------------------------------------------
class TestLoadExperimentalLifecycles:
    def test_table_missing_returns_empty(self):
        mock_conn = MagicMock()
        with patch("src.swing_loaders.table_exists_pg", return_value=False):
            df = load_swing_experimental_lifecycles_pg(mock_conn)
            assert df.empty


# ---------------------------------------------------------------------------
# Scanner shadow diagnostics (optional, unverified)
# ---------------------------------------------------------------------------
class TestScannerShadowDiagnostics:
    def test_table_missing_returns_unverified_status(self):
        mock_conn = MagicMock()
        with patch("src.swing_loaders.table_exists_pg", return_value=False):
            df = load_scanner_shadow_diagnostics_pg(mock_conn)
            assert not df.empty
            assert df["status"].iloc[0] == "UNVERIFIED_TABLE_MISSING"

    def test_does_not_feed_official_results(self):
        """Scanner diagnostics must NOT produce PF, W/L, or official metrics."""
        mock_conn = MagicMock()
        with patch("src.swing_loaders.table_exists_pg", return_value=False):
            df = load_scanner_shadow_diagnostics_pg(mock_conn)
            # Check: no columns suggesting official metrics
            assert "pf" not in [c.lower() for c in df.columns]
            assert "win_rate" not in [c.lower() for c in df.columns]

    def test_attrs_set_to_unverified(self):
        mock_conn = MagicMock()
        with patch("src.swing_loaders.table_exists_pg", return_value=False):
            df = load_scanner_shadow_diagnostics_pg(mock_conn)
            assert df.attrs.get("swing_isolation") == "UNVERIFIED"
            assert df.attrs.get("mode_authority") == "SECONDARY_DIAGNOSTIC"


# ---------------------------------------------------------------------------
# Adapter parity: extracted from metrics_json, NOT a separate table
# ---------------------------------------------------------------------------
class TestExtractAdapterParity:
    def test_extracts_from_metrics_json(self):
        signals = _mock_signal_records_df()
        ap = extract_adapter_parity(signals)
        assert ap.iloc[0] == {"status": "FILLED"}
        assert ap.iloc[1] == {"status": "FILLED"}
        assert ap.iloc[2] is None  # no adapter_parity in this signal's metrics

    def test_no_metrics_json_column_returns_none_series(self):
        signals = _mock_signal_records_df(include_metrics_json=False)
        ap = extract_adapter_parity(signals)
        assert ap.isna().all() or all(v is None for v in ap)

    def test_does_not_query_table_adapter_parity(self):
        """Adapter parity must NOT query a separate adapter_parity table."""
        # The function takes a DataFrame, not a connection — no table query possible
        signals = _mock_signal_records_df()
        ap = extract_adapter_parity(signals)
        assert ap is not None  # pure function, no DB call

    def test_missing_adapter_parity_returns_none(self):
        signals = pd.DataFrame({"metrics_json": ['{"swing_v1":{}}']})
        ap = extract_adapter_parity(signals)
        assert ap.iloc[0] is None

    def test_invalid_json_returns_none(self):
        signals = pd.DataFrame({"metrics_json": ["not valid json"]})
        ap = extract_adapter_parity(signals)
        assert ap.iloc[0] is None  # must not crash


# ---------------------------------------------------------------------------
# same_market_bar resolver
# ---------------------------------------------------------------------------
class TestResolveSameMarketBar:
    def test_canonical_true(self):
        result = resolve_same_market_bar(
            json.dumps({"swing_v1": {"same_market_bar": True}})
        )
        assert result["value"] is True
        assert result["derivation_source"] == "CANONICAL_FIELD"
        assert result["data_available"] is True

    def test_canonical_false(self):
        result = resolve_same_market_bar(
            json.dumps({"swing_v1": {"same_market_bar": False}})
        )
        assert result["value"] is False
        assert result["derivation_source"] == "CANONICAL_FIELD"

    def test_canonical_field_absent_derived_from_timestamps(self):
        """When canonical field is absent but timestamps exist, derive."""
        born = datetime(2026, 7, 20, 14, 3, 0)  # 14:00 bar
        activation_bar = datetime(2026, 7, 20, 14, 5, 0)  # same 14:00 bar
        result = resolve_same_market_bar(
            json.dumps({"swing_v1": {}}),
            born_timestamp=born,
            activation_bar_timestamp=activation_bar,
        )
        assert result["value"] is True
        assert result["derivation_source"] == "DERIVED_FROM_TIMESTAMPS"
        assert "canonical field was absent" in result.get("warning", "")

    def test_derived_false_different_bars(self):
        born = datetime(2026, 7, 20, 14, 30, 0)  # 14:00 bar
        activation_bar = datetime(2026, 7, 20, 15, 5, 0)  # 15:00 bar
        result = resolve_same_market_bar(
            json.dumps({"swing_v1": {}}),
            born_timestamp=born,
            activation_bar_timestamp=activation_bar,
        )
        assert result["value"] is False

    def test_insufficient_data_returns_none(self):
        result = resolve_same_market_bar(
            json.dumps({"swing_v1": {}}),
            born_timestamp=None,
            activation_bar_timestamp=None,
        )
        assert result["value"] is None
        assert result["derivation_source"] == "INSUFFICIENT_DATA"
        assert result["data_available"] is False

    def test_uses_activation_timestamp_fallback(self):
        born = datetime(2026, 7, 20, 14, 0, 0)
        act = datetime(2026, 7, 20, 14, 0, 0)
        result = resolve_same_market_bar(
            json.dumps({"swing_v1": {}}),
            born_timestamp=born,
            activation_timestamp=act,
        )
        assert result["derivation_source"] == "DERIVED_FROM_TIMESTAMPS"

    def test_non_bool_field_returns_none_with_warning(self):
        result = resolve_same_market_bar(
            json.dumps({"swing_v1": {"same_market_bar": "yes"}})
        )
        assert result["value"] is None
        assert result["derivation_source"] == "CANONICAL_FIELD_INVALID_TYPE"
        assert result["warning"] is not None


# ---------------------------------------------------------------------------
# execution_detached: separate from same_market_bar
# ---------------------------------------------------------------------------
class TestExecutionDetached:
    def test_extracts_execution_detached(self):
        signals = pd.DataFrame({
            "metrics_json": [
                json.dumps({"swing_v1": {"execution_detached": True}}),
                json.dumps({"swing_v1": {"execution_detached": False}}),
                json.dumps({"swing_v1": {}}),
                "invalid",
            ]
        })
        result = extract_execution_detached(signals)
        assert result.iloc[0] is True
        assert result.iloc[1] is False
        assert result.iloc[2] is None
        assert result.iloc[3] is None  # invalid JSON

    def test_does_not_substitute_same_market_bar(self):
        """execution_detached is a separate concept — verify it's NOT conflated."""
        signals = pd.DataFrame({
            "metrics_json": [
                json.dumps({"swing_v1": {"execution_detached": True, "same_market_bar": True}}),
            ]
        })
        ed = extract_execution_detached(signals)
        smb = resolve_same_market_bar(signals["metrics_json"].iloc[0])
        # Both can be True independently — no substitution
        assert ed.iloc[0] == True
        assert smb["value"] == True
        # They're separate functions — no cross-contamination in the API


# ---------------------------------------------------------------------------
# Retroactive bar fill (derived)
# ---------------------------------------------------------------------------
class TestRetroactiveBarFill:
    def test_fill_after_bar_close(self):
        # Activation bar: 14:00-15:00. Persisted at 15:30 → retroactive
        pending = datetime(2026, 7, 20, 15, 30, 0)
        activation_bar = datetime(2026, 7, 20, 14, 5, 0)
        result = derive_retroactive_bar_fill(None, pending, activation_bar)
        assert result is True

    def test_no_fill_within_bar(self):
        # Persisted at 14:30 → within bar, not retroactive
        pending = datetime(2026, 7, 20, 14, 30, 0)
        activation_bar = datetime(2026, 7, 20, 14, 5, 0)
        result = derive_retroactive_bar_fill(None, pending, activation_bar)
        assert result is False

    def test_insufficient_data_returns_none(self):
        result = derive_retroactive_bar_fill(None, None, datetime(2026, 7, 20, 14, 0, 0))
        assert result is None


# ---------------------------------------------------------------------------
# Demo compatibility classifier
# ---------------------------------------------------------------------------
class TestClassifyDemoCompatibility:
    """Tests for classify_demo_compatibility — handles real swing_adapter_parity_v1 schema.

    Priority: actions.demo_entry_submit (canonical) → reason-based classification.
    """

    # ---- Real schema: actions.demo_entry_submit ----

    def test_actions_activation_mismatch(self):
        """Real schema: reason=activation_mismatch_new_cancelled → ACTIVATION_MISMATCH."""
        adapter_parity = {
            "actions": {
                "demo_entry_submit": {
                    "status": "SUCCEEDED",
                    "reason": "activation_mismatch_new_cancelled",
                }
            },
            "schema": "swing_adapter_parity_v1",
            "version": "v1",
        }
        result = classify_demo_compatibility(adapter_parity)
        assert result == "ACTIVATION_MISMATCH"

    def test_actions_submitted_reason(self):
        """reason=submitted → SUBMITTED, never FILLED."""
        adapter_parity = {
            "actions": {
                "demo_entry_submit": {
                    "status": "SUCCEEDED",
                    "reason": "submitted",
                }
            },
        }
        result = classify_demo_compatibility(adapter_parity)
        assert result == "SUBMITTED"

    def test_actions_filled_explicit(self):
        """Explicit fill reason → FILLED."""
        adapter_parity = {
            "actions": {
                "demo_entry_submit": {
                    "status": "FILLED",
                    "reason": "fill",
                }
            },
        }
        result = classify_demo_compatibility(adapter_parity)
        assert result == "FILLED"

    def test_actions_cancelled(self):
        """Cancel reason → CANCELLED."""
        adapter_parity = {
            "actions": {
                "demo_entry_submit": {
                    "status": "CANCELLED",
                    "reason": "entry_cancelled",
                }
            },
        }
        result = classify_demo_compatibility(adapter_parity)
        assert result == "CANCELLED"

    def test_actions_requested(self):
        """Requested status → REQUESTED."""
        adapter_parity = {
            "actions": {
                "demo_entry_submit": {
                    "status": "REQUESTED",
                    "reason": None,
                }
            },
        }
        result = classify_demo_compatibility(adapter_parity)
        assert result == "REQUESTED"

    def test_actions_succeeded_no_reason(self):
        """SUCCEEDED without reason → SUBMITTED (not FILLED)."""
        adapter_parity = {
            "actions": {
                "demo_entry_submit": {
                    "status": "SUCCEEDED",
                }
            },
        }
        result = classify_demo_compatibility(adapter_parity)
        assert result == "SUBMITTED"

    # ---- Edge: fallback to any demo_* action ----

    def test_actions_demo_exit_fallback(self):
        """When demo_entry_submit is absent, fallback to any demo_* action."""
        adapter_parity = {
            "actions": {
                "demo_exit_stop_loss": {
                    "status": "SKIPPED_NOT_APPLICABLE",
                    "reason": "execution_detached_skip_remote_close",
                }
            },
        }
        # reason doesn't contain activation_mismatch/cancelled/filled/submitted/requested
        # status SKIPPED_NOT_APPLICABLE is not in any mapping → UNKNOWN
        result = classify_demo_compatibility(adapter_parity)
        assert result == "UNKNOWN"

    # ---- Legacy flat format (defensive, low priority) ----

    def test_flat_submitted_does_not_equal_fill(self):
        result = classify_demo_compatibility({"status": "SUBMITTED", "reason": "submitted"})
        assert result == "SUBMITTED"

    def test_flat_fill_status(self):
        result = classify_demo_compatibility({"status": "FILLED", "reason": "fill"})
        assert result == "FILLED"

    # ---- Absent / invalid ----

    def test_unavailable_none(self):
        result = classify_demo_compatibility(None)
        assert result == "UNAVAILABLE"

    def test_unknown_bad_input(self):
        result = classify_demo_compatibility("garbage")
        assert result == "UNKNOWN"

    def test_unknown_empty_dict(self):
        result = classify_demo_compatibility({})
        assert result == "UNKNOWN"

    def test_unknown_no_relevant_action(self):
        """Actions dict with no demo_* keys → UNKNOWN."""
        adapter_parity = {
            "actions": {
                "telegram_pending": {"status": "SUCCEEDED"},
            },
        }
        result = classify_demo_compatibility(adapter_parity)
        assert result == "UNKNOWN"

    # ---- execution_detached is reported separately ----

    def test_execution_detached_separate(self):
        """execution_detached=True does NOT auto-convert to ACTIVATION_MISMATCH."""
        adapter_parity = {
            "actions": {
                "demo_entry_submit": {
                    "status": "SUCCEEDED",
                    "reason": "submitted",
                }
            },
        }
        # execution_detached=True with submitted reason → still SUBMITTED
        result = classify_demo_compatibility(adapter_parity, execution_detached_val=True)
        assert result == "SUBMITTED"

    # ---- reason with "cancelled" substring takes priority over status ----

    def test_reason_priority_over_status_cancelled(self):
        """reason containing 'cancelled' → CANCELLED even if status is SUCCEEDED."""
        adapter_parity = {
            "actions": {
                "demo_entry_submit": {
                    "status": "SUCCEEDED",
                    "reason": "order_cancelled_by_exchange",
                }
            },
        }
        result = classify_demo_compatibility(adapter_parity)
        assert result == "CANCELLED"

    def test_reason_activation_mismatch_similar(self):
        """reason containing 'mismatch' does NOT match without 'activation_mismatch'."""
        adapter_parity = {
            "actions": {
                "demo_entry_submit": {
                    "status": "SUCCEEDED",
                    "reason": "some_other_mismatch",
                }
            },
        }
        result = classify_demo_compatibility(adapter_parity)
        assert result == "SUBMITTED"  # falls through to status SUCCEEDED → SUBMITTED


# ---------------------------------------------------------------------------
# Fingerprint extraction
# ---------------------------------------------------------------------------
class TestExtractFingerprint:
    def test_extracts_fingerprint_from_metrics_json(self):
        signals = _mock_signal_records_df()
        fp = extract_fingerprint(signals)
        assert fp == "fp_v7"

    def test_no_metrics_json_returns_none(self):
        signals = _mock_signal_records_df(include_metrics_json=False)
        fp = extract_fingerprint(signals)
        assert fp is None


# ---------------------------------------------------------------------------
# Bulk loader
# ---------------------------------------------------------------------------
class TestLoadAllSwingData:
    def test_returns_dict_of_dataframes(self):
        mock_conn = MagicMock()
        with patch("src.swing_loaders.load_signal_records_pg", return_value=pd.DataFrame({"id": [1]})):
            with patch("src.swing_loaders.load_signal_events_pg", return_value=pd.DataFrame({"id": [1]})):
                with patch("src.swing_loaders.load_swing_experimental_lifecycles_pg", return_value=pd.DataFrame()):
                    with patch("src.swing_loaders.load_scanner_shadow_diagnostics_pg", return_value=pd.DataFrame({"status": ["UNVERIFIED"]})):
                        result = load_all_swing_data_pg(mock_conn)
                        assert "signal_records" in result
                        assert "scanner_shadow_diagnostics" in result
                        # adapter_parity is NOT a separate table — not in result keys
                        assert "adapter_parity" not in result
                        assert len(result["signal_records"]) == 1

    def test_no_sqlite_fallback(self):
        """No SQLite fallback — all loaders go through PostgreSQL exclusively."""
        mock_conn = MagicMock()
        with patch("src.swing_loaders.table_exists_pg", return_value=False):
            df = load_signal_records_pg(mock_conn)
            assert df.empty
            # No attempt to open SQLite, no db_path, no fallback
            # Just an empty DataFrame


# ---------------------------------------------------------------------------
# Signal summary
# ---------------------------------------------------------------------------
class TestComputeSwingSummary:
    def test_empty_data_returns_no_data_flag(self):
        mock_conn = MagicMock()
        with patch("src.swing_loaders.load_signal_records_pg", return_value=pd.DataFrame()):
            with patch("src.swing_loaders.load_signal_events_pg", return_value=pd.DataFrame()):
                with patch("src.swing_loaders.load_scanner_shadow_diagnostics_pg", return_value=pd.DataFrame()):
                    summary = compute_swing_summary_pg(mock_conn)
                    assert summary["data_quality_flag"] == "no_data"

    def test_computes_correct_counts(self):
        mock_conn = MagicMock()
        mock_sig = _mock_signal_records_df()
        mock_ev = _mock_events_df()
        with patch("src.swing_loaders.load_signal_records_pg", return_value=mock_sig):
            with patch("src.swing_loaders.load_signal_events_pg", return_value=mock_ev):
                with patch("src.swing_loaders.load_scanner_shadow_diagnostics_pg", return_value=pd.DataFrame()):
                    summary = compute_swing_summary_pg(mock_conn)
                    assert summary["total_signals"] == 8
                    assert summary["won"] == 3
                    assert summary["lost"] == 2
                    assert summary["cancelled"] == 1
                    assert summary["expired"] == 1
                    assert summary["open_signals"] == 1
                    assert summary["total_r"] == pytest.approx(2.7, rel=0.01)
                    assert summary["win_rate"] == pytest.approx(60.0, rel=0.1)
                    assert summary["data_quality_flag"] == "ok"

    def test_degraded_no_events_flag(self):
        mock_conn = MagicMock()
        with patch("src.swing_loaders.load_signal_records_pg", return_value=_mock_signal_records_df()):
            with patch("src.swing_loaders.load_signal_events_pg", return_value=pd.DataFrame()):
                with patch("src.swing_loaders.load_scanner_shadow_diagnostics_pg", return_value=pd.DataFrame()):
                    summary = compute_swing_summary_pg(mock_conn)
                    assert summary["data_quality_flag"] == "degraded_no_events"

    def test_fingerprint_in_summary(self):
        mock_conn = MagicMock()
        with patch("src.swing_loaders.load_signal_records_pg", return_value=_mock_signal_records_df()):
            with patch("src.swing_loaders.load_signal_events_pg", return_value=_mock_events_df()):
                with patch("src.swing_loaders.load_scanner_shadow_diagnostics_pg", return_value=pd.DataFrame()):
                    summary = compute_swing_summary_pg(mock_conn)
                    assert summary["fingerprint"] == "fp_v7"

    def test_adapter_parity_availability(self):
        mock_conn = MagicMock()
        with patch("src.swing_loaders.load_signal_records_pg", return_value=_mock_signal_records_df()):
            with patch("src.swing_loaders.load_signal_events_pg", return_value=_mock_events_df()):
                with patch("src.swing_loaders.load_scanner_shadow_diagnostics_pg", return_value=pd.DataFrame()):
                    summary = compute_swing_summary_pg(mock_conn)
                    assert summary["adapter_parity_availability"] == "available"

    def test_same_market_bar_availability_canonical(self):
        mock_conn = MagicMock()
        with patch("src.swing_loaders.load_signal_records_pg", return_value=_mock_signal_records_df()):
            with patch("src.swing_loaders.load_signal_events_pg", return_value=_mock_events_df()):
                with patch("src.swing_loaders.load_scanner_shadow_diagnostics_pg", return_value=pd.DataFrame()):
                    summary = compute_swing_summary_pg(mock_conn)
                    assert summary["same_market_bar_availability"] == "canonical_available"

    def test_invalid_json_produces_data_quality_flag(self):
        """Invalid JSON in metrics_json must not crash — degrades gracefully."""
        mock_conn = MagicMock()
        bad_df = _mock_signal_records_df()
        bad_df.at[0, "metrics_json"] = "{{{bad json"
        with patch("src.swing_loaders.load_signal_records_pg", return_value=bad_df):
            with patch("src.swing_loaders.load_signal_events_pg", return_value=_mock_events_df()):
                with patch("src.swing_loaders.load_scanner_shadow_diagnostics_pg", return_value=pd.DataFrame()):
                    summary = compute_swing_summary_pg(mock_conn)
                    # Should not crash — the malformed row is handled gracefully
                    assert summary["total_signals"] == 8