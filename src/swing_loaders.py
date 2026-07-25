from __future__ import annotations

"""
swing_loaders.py
----------------
PostgreSQL-specific loaders for the Swing Strategy Review Center.

Every loader:
  - Uses parameterised queries only (no string interpolation of values)
  - Returns DataFrames with columns matching expected schema
  - Marks missing data as None (never invents zeros)
  - Attaches source/authority/confidence metadata
  - Handles UTC/Colombia timezone windows correctly
  - Uses half-open intervals: start_utc <= timestamp < end_utc
"""

import json as _json
from datetime import datetime, timedelta
from typing import Any, Optional

import pandas as pd

from .postgres_readonly import read_sql_df_pg, read_rows_pg, table_exists_pg, table_columns_pg
from .swing_source_map import get_all_metadata, get_authority, get_confidence

# Colombia = UTC-5 (no DST)
COLOMBIA_OFFSET = timedelta(hours=-5)


def _utc_now() -> datetime:
    return datetime.utcnow()


def _colombia_now() -> datetime:
    return _utc_now() + COLOMBIA_OFFSET


def _attach_metadata(df: pd.DataFrame, key: str) -> pd.DataFrame:
    """Attach source/authority/confidence as DataFrame attributes."""
    if df is None or df.empty:
        return df
    df.attrs["metric_key"] = key
    df.attrs["source"] = get_authority(key) or "unknown"
    df.attrs["authority"] = get_authority(key) or "unknown"
    df.attrs["confidence"] = get_confidence(key) or "unknown"
    return df


# ---------------------------------------------------------------------------
# JSON helpers
# ---------------------------------------------------------------------------
def _safe_json_load(val: Any) -> Optional[dict]:
    """Parse a JSON value without crashing. Returns None on failure."""
    if val is None:
        return None
    if isinstance(val, dict):
        return val
    try:
        if isinstance(val, str):
            return _json.loads(val)
    except (_json.JSONDecodeError, TypeError):
        pass
    return None


def _nested_get(obj: Optional[dict], path: str) -> Any:
    """Traverse a dotted path into a dict. Returns None if any key is missing."""
    if obj is None:
        return None
    parts = path.split(".")
    current = obj
    for part in parts:
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


# ---------------------------------------------------------------------------
# Signal Records
# ---------------------------------------------------------------------------
def load_signal_records_pg(
    conn,
    window_start: Optional[datetime] = None,
    window_end: Optional[datetime] = None,
    limit: int = 10000,
) -> pd.DataFrame:
    """Load signal_records from PostgreSQL within a time window.

    Uses metrics_json (NOT metadata).  Half-open interval on created_at.
    """
    if not table_exists_pg(conn, "signal_records"):
        return pd.DataFrame()

    cols = table_columns_pg(conn, "signal_records")
    date_col = "created_at" if "created_at" in cols else None

    params: list = []
    where_clauses: list[str] = []

    if window_start and date_col:
        utc_start = window_start - COLOMBIA_OFFSET
        where_clauses.append(f"{date_col} >= %s")
        params.append(utc_start)
    if window_end and date_col:
        utc_end = window_end - COLOMBIA_OFFSET
        where_clauses.append(f"{date_col} < %s")  # half-open
        params.append(utc_end)

    where = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""
    order_col = "id" if "id" in cols else (date_col or cols[0])
    selected = ", ".join(cols)

    query = f"SELECT {selected} FROM signal_records {where} ORDER BY {order_col} DESC LIMIT %s"
    params.append(limit)

    df = read_sql_df_pg(conn, query, tuple(params))
    if "created_at" in df.columns:
        df["created_at"] = pd.to_datetime(df["created_at"], errors="coerce", utc=True)
    if "opened_at" in df.columns:
        df["opened_at"] = pd.to_datetime(df["opened_at"], errors="coerce", utc=True)
    if "closed_at" in df.columns:
        df["closed_at"] = pd.to_datetime(df["closed_at"], errors="coerce", utc=True)

    # Numeric columns
    for col in ["entry_price", "tp_price", "sl_price", "pnl_r", "gross_r", "net_r", "estimated_cost"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    return _attach_metadata(df, "signal_records")


# ---------------------------------------------------------------------------
# Signal Events
# ---------------------------------------------------------------------------
def load_signal_events_pg(
    conn,
    window_start: Optional[datetime] = None,
    window_end: Optional[datetime] = None,
    limit: int = 50000,
) -> pd.DataFrame:
    """Load signal_events from PostgreSQL.

    Uses event_time (NOT created_at) and metadata_json (NOT metadata).
    Half-open interval.
    """
    if not table_exists_pg(conn, "signal_events"):
        return pd.DataFrame()

    cols = table_columns_pg(conn, "signal_events")
    # Require event_time and metadata_json — no silent fallback
    if "event_time" not in cols:
        raise RuntimeError(
            "Schema mismatch: signal_events missing 'event_time' column. "
            "R1 requires PostgreSQL schema with event_time as the canonical timestamp column."
        )
    if "metadata_json" not in cols:
        raise RuntimeError(
            "Schema mismatch: signal_events missing 'metadata_json' column. "
            "R1 requires PostgreSQL schema with metadata_json for event-level metadata."
        )
    date_col = "event_time"

    params: list = []
    where_clauses: list[str] = []

    if window_start and date_col:
        utc_start = window_start - COLOMBIA_OFFSET
        where_clauses.append(f"{date_col} >= %s")
        params.append(utc_start)
    if window_end and date_col:
        utc_end = window_end - COLOMBIA_OFFSET
        where_clauses.append(f"{date_col} < %s")
        params.append(utc_end)

    where = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""
    order_col = "id" if "id" in cols else (date_col or cols[0])
    selected = ", ".join(cols)

    query = f"SELECT {selected} FROM signal_events {where} ORDER BY {order_col} DESC LIMIT %s"
    params.append(limit)

    df = read_sql_df_pg(conn, query, tuple(params))
    # Parse timestamps
    for col_name in ["event_time", "created_at", "occurred_at"]:
        if col_name in df.columns:
            df[col_name] = pd.to_datetime(df[col_name], errors="coerce", utc=True)
    if "price" in df.columns:
        df["price"] = pd.to_numeric(df["price"], errors="coerce")

    return _attach_metadata(df, "signal_events")


# ---------------------------------------------------------------------------
# Swing Experimental Lifecycles (shadow)
# ---------------------------------------------------------------------------
def load_swing_experimental_lifecycles_pg(
    conn,
    window_start: Optional[datetime] = None,
    window_end: Optional[datetime] = None,
    limit: int = 10000,
) -> pd.DataFrame:
    """Load swing_experimental_lifecycles (shadow guard outcomes)."""
    table = "swing_experimental_lifecycles"
    if not table_exists_pg(conn, table):
        return pd.DataFrame()

    cols = table_columns_pg(conn, table)
    date_col = next((c for c in ["created_at", "started_at", "signal_opened_at"] if c in cols), None)

    params: list = []
    where_clauses: list[str] = []

    if window_start and date_col:
        utc_start = window_start - COLOMBIA_OFFSET
        where_clauses.append(f"{date_col} >= %s")
        params.append(utc_start)
    if window_end and date_col:
        utc_end = window_end - COLOMBIA_OFFSET
        where_clauses.append(f"{date_col} < %s")
        params.append(utc_end)

    where = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""
    order_col = "id" if "id" in cols else (date_col or cols[0])
    selected = ", ".join(cols)

    query = f"SELECT {selected} FROM {table} {where} ORDER BY {order_col} DESC LIMIT %s"
    params.append(limit)

    df = read_sql_df_pg(conn, query, tuple(params))
    return _attach_metadata(df, "swing_experimental_lifecycles")


# ---------------------------------------------------------------------------
# Scanner Shadow Diagnostics (optional, unverified)
# ---------------------------------------------------------------------------
def load_scanner_shadow_diagnostics_pg(
    conn,
    window_start: Optional[datetime] = None,
    window_end: Optional[datetime] = None,
    limit: int = 5000,
) -> pd.DataFrame:
    """Load scanner_shadow_diagnostics if available.

    Returns empty DataFrame with UNVERIFIED status if table is absent
    or if SWING isolation cannot be confirmed.
    """
    table = "scanner_shadow_diagnostics"
    if not table_exists_pg(conn, table):
        df = pd.DataFrame({"status": ["UNVERIFIED_TABLE_MISSING"]})
        df.attrs["swing_isolation"] = "UNVERIFIED"
        df.attrs["mode_authority"] = "SECONDARY_DIAGNOSTIC"
        return _attach_metadata(df, "scanner_diagnostics")

    cols = table_columns_pg(conn, table)
    date_col = next((c for c in ["event_time", "created_at", "scanned_at"] if c in cols), None)

    params: list = []
    where_clauses: list[str] = []

    if window_start and date_col:
        utc_start = window_start - COLOMBIA_OFFSET
        where_clauses.append(f"{date_col} >= %s")
        params.append(utc_start)
    if window_end and date_col:
        utc_end = window_end - COLOMBIA_OFFSET
        where_clauses.append(f"{date_col} < %s")
        params.append(utc_end)

    where = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""
    order_col = "id" if "id" in cols else (date_col or cols[0])
    selected = ", ".join(cols)

    query = f"SELECT {selected} FROM {table} {where} ORDER BY {order_col} DESC LIMIT %s"
    params.append(limit)

    df = read_sql_df_pg(conn, query, tuple(params))
    # Do NOT mix modes — mark as unverified if SWING isolation unclear
    if df.empty:
        df = pd.DataFrame({"status": ["UNVERIFIED_NO_DATA"]})
    df.attrs["swing_isolation"] = "UNVERIFIED"
    df.attrs["mode_authority"] = "SECONDARY_DIAGNOSTIC"
    return _attach_metadata(df, "scanner_diagnostics")


# ---------------------------------------------------------------------------
# Adapter Parity (extracted from signal_records.metrics_json, NOT a separate table)
# ---------------------------------------------------------------------------
def extract_adapter_parity(signals: pd.DataFrame) -> pd.Series:
    """Extract adapter_parity from signal_records.metrics_json → swing_v1.adapter_parity.

    Returns a Series aligned with signals index.  Values are dicts or None.
    """
    if signals is None or signals.empty:
        return pd.Series(dtype=object)

    if "metrics_json" not in signals.columns:
        return pd.Series([None] * len(signals), index=signals.index)

    def _extract(val: Any) -> Optional[dict]:
        obj = _safe_json_load(val)
        return _nested_get(obj, "swing_v1.adapter_parity")

    return signals["metrics_json"].apply(_extract)


# ---------------------------------------------------------------------------
# same_market_bar resolver
# ---------------------------------------------------------------------------
def resolve_same_market_bar(
    metrics_json_val: Any,
    born_timestamp: Optional[datetime] = None,
    activation_bar_timestamp: Optional[datetime] = None,
    activation_timestamp: Optional[datetime] = None,
    timeframe_hours: int = 1,
) -> dict:
    """Resolve same_market_bar with priority:
    1. Canonical field from metrics_json.swing_v1.same_market_bar (bool)
    2. Derived from timestamps: compare canonical bar open
    3. Insufficient data → None

    Returns:
        {
            "value": True | False | None,
            "derivation_source": "CANONICAL_FIELD" | "DERIVED_FROM_TIMESTAMPS" | "INSUFFICIENT_DATA",
            "data_available": bool,
            "warning": str or None,
        }
    """
    # Priority 1: Canonical field
    obj = _safe_json_load(metrics_json_val)
    canonical = _nested_get(obj, "swing_v1.same_market_bar")
    if canonical is not None:
        if isinstance(canonical, bool):
            return {
                "value": canonical,
                "derivation_source": "CANONICAL_FIELD",
                "data_available": True,
                "warning": None,
            }
        # Non-bool value in field — treat as can't use
        return {
            "value": None,
            "derivation_source": "CANONICAL_FIELD_INVALID_TYPE",
            "data_available": True,
            "warning": f"same_market_bar field exists but is not boolean: {type(canonical).__name__}",
        }

    # Priority 2: Derive from timestamps
    activation_ts = activation_bar_timestamp or activation_timestamp
    if born_timestamp is not None and activation_ts is not None:
        try:
            # Normalize to UTC
            born_utc = pd.Timestamp(born_timestamp).tz_localize(None) if pd.notna(born_timestamp) else None
            act_utc = pd.Timestamp(activation_ts).tz_localize(None) if pd.notna(activation_ts) else None

            if born_utc is not None and act_utc is not None:
                # Canonical bar opening: floor to timeframe
                # pd.Timestamp.value is nanoseconds — convert to ms
                born_ms = born_utc.value // 1_000_000
                act_ms = act_utc.value // 1_000_000
                bar_ms = timeframe_hours * 3600 * 1000
                born_bar_open = born_ms // bar_ms * bar_ms
                act_bar_open = act_ms // bar_ms * bar_ms
                derived = (born_bar_open == act_bar_open)
                return {
                    "value": derived,
                    "derivation_source": "DERIVED_FROM_TIMESTAMPS",
                    "data_available": True,
                    "warning": "same_market_bar derived from timestamps — canonical field was absent",
                }
        except Exception:
            pass

    # Priority 3: Insufficient data
    return {
        "value": None,
        "derivation_source": "INSUFFICIENT_DATA",
        "data_available": False,
        "warning": "Cannot determine same_market_bar: canonical field absent and timestamp data missing",
    }


# ---------------------------------------------------------------------------
# Execution detached (separate from same_market_bar)
# ---------------------------------------------------------------------------
def extract_execution_detached(signals: pd.DataFrame) -> pd.Series:
    """Extract execution_detached from signal_records.metrics_json → swing_v1.execution_detached.

    This is a SEPARATE field from same_market_bar. Do NOT use as substitute.
    """
    if signals is None or signals.empty:
        return pd.Series(dtype=object)

    if "metrics_json" not in signals.columns:
        return pd.Series([None] * len(signals), index=signals.index)

    def _extract(val: Any) -> Optional[bool]:
        obj = _safe_json_load(val)
        result = _nested_get(obj, "swing_v1.execution_detached")
        if isinstance(result, bool):
            return result
        return None

    return signals["metrics_json"].apply(_extract)


# ---------------------------------------------------------------------------
# Retroactive bar fill (derived, NOT persisted)
# ---------------------------------------------------------------------------
def derive_retroactive_bar_fill(
    created_at: Optional[datetime],
    pending_persisted_at: Optional[datetime],
    activation_bar_timestamp: Optional[datetime],
    timeframe_hours: int = 1,
) -> Optional[bool]:
    """Derive whether a retroactive bar fill was detected.

    Compares pending_persisted_at vs activation bar close.
    Returns None if data is insufficient.
    """
    if pending_persisted_at is None or activation_bar_timestamp is None:
        return None

    try:
        pending_utc = pd.Timestamp(pending_persisted_at).tz_localize(None)
        act_utc = pd.Timestamp(activation_bar_timestamp).tz_localize(None)

        if pd.isna(pending_utc) or pd.isna(act_utc):
            return None

        # pd.Timestamp.value is nanoseconds — convert to ms
        pending_ms = pending_utc.value // 1_000_000
        act_ms = act_utc.value // 1_000_000
        bar_ms = timeframe_hours * 3600 * 1000
        bar_open = act_ms // bar_ms * bar_ms
        bar_close = bar_open + bar_ms

        # Retroactive if persisted after bar close
        return pending_ms > bar_close
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Demo compatibility (derived from adapter_parity)
# ---------------------------------------------------------------------------
def classify_demo_compatibility(
    adapter_parity_val: Any,
    execution_detached_val: Optional[bool] = None,
) -> str:
    """Classify demo compatibility from adapter_parity embedded data.

    Handles the real swing_adapter_parity_v1 schema:
      adapter_parity → actions → demo_entry_submit → status / reason

    Returns one of:
      ACTIVATION_MISMATCH, FILLED, CANCELLED, SUBMITTED, REQUESTED, UNAVAILABLE, UNKNOWN.
    reason=submitted does NOT equal fill.
    execution_detached is reported SEPARATELY — NOT auto-converted to ACTIVATION_MISMATCH.
    """
    if adapter_parity_val is None:
        return "UNAVAILABLE"

    obj = adapter_parity_val if isinstance(adapter_parity_val, dict) else _safe_json_load(adapter_parity_val)
    if obj is None or not isinstance(obj, dict):
        return "UNKNOWN"

    # Priority 1: canonical swing_adapter_parity_v1 schema with actions.<action>.status/reason
    actions = obj.get("actions")
    if isinstance(actions, dict):
        # Look for demo_entry_submit — the canonical action for demo execution
        demo_action = actions.get("demo_entry_submit")
        if isinstance(demo_action, dict):
            return _classify_from_action(demo_action)
        # Fallback: any demo_* action
        for key, action in actions.items():
            if isinstance(action, dict) and key.startswith("demo_"):
                return _classify_from_action(action)
        return "UNKNOWN"

    # Priority 2: simple flat dict format (legacy/defensive, low priority)
    status = obj.get("status")
    reason = obj.get("reason")
    if status is not None:
        return _classify_from_flat(status, reason)

    return "UNKNOWN"


def _classify_from_action(action: dict) -> str:
    """Classify from a canonical action dict with status and reason fields."""
    status = action.get("status")
    reason = action.get("reason")
    reason_str = str(reason).lower() if reason else ""
    status_str = str(status).upper() if status else ""

    # Reason-based semantic classification (highest priority)
    if reason:
        if "activation_mismatch" in reason_str:
            return "ACTIVATION_MISMATCH"
        if "cancelled" in reason_str or "canceled" in reason_str:
            return "CANCELLED"
        if "filled" in reason_str or reason_str == "fill":
            return "FILLED"
        if "submitted" in reason_str:
            return "SUBMITTED"
        if "requested" in reason_str or "pending" in reason_str:
            return "REQUESTED"

    # Status-based fallback (reason absent or unrecognized)
    if status_str in ("FILLED",):
        return "FILLED"
    if status_str in ("CANCELLED", "CANCELED"):
        return "CANCELLED"
    if status_str in ("SUBMITTED", "SUCCEEDED"):
        # SUCCEEDED without explicit fill/cancel reason → submitted only
        return "SUBMITTED"
    if status_str in ("REQUESTED", "PENDING"):
        return "REQUESTED"
    if status_str == "ACTIVATION_MISMATCH":
        return "ACTIVATION_MISMATCH"

    return "UNKNOWN"


def _classify_from_flat(status: Any, reason: Any) -> str:
    """Classify from a flat {status, reason} dict (legacy format)."""
    reason_str = str(reason).lower() if reason else ""
    status_str = str(status).upper() if status else ""

    # Reason-based semantic classification
    if reason:
        if "activation_mismatch" in reason_str:
            return "ACTIVATION_MISMATCH"
        if "cancelled" in reason_str or "canceled" in reason_str:
            return "CANCELLED"
        if "filled" in reason_str or reason_str == "fill":
            return "FILLED"
        if "submitted" in reason_str:
            return "SUBMITTED"
        if "requested" in reason_str or "pending" in reason_str:
            return "REQUESTED"

    # Status-based fallback
    if status_str == "SUBMITTED" and reason_str != "fill":
        return "SUBMITTED"

    mapping = {
        "REQUESTED": "REQUESTED",
        "SUBMITTED": "SUBMITTED",
        "FILLED": "FILLED",
        "CANCELLED": "CANCELLED",
        "CANCELED": "CANCELLED",
        "SUCCEEDED": "SUBMITTED",
        "ACTIVATION_MISMATCH": "ACTIVATION_MISMATCH",
        "UNAVAILABLE": "UNAVAILABLE",
    }
    return mapping.get(status_str, "UNKNOWN")


# ---------------------------------------------------------------------------
# Fingerprint extraction from metrics_json
# ---------------------------------------------------------------------------
def extract_fingerprint(signals: pd.DataFrame) -> Optional[str]:
    """Extract config fingerprint from signal_records.metrics_json → swing_v1.config_fingerprint."""
    if signals is None or signals.empty:
        return None

    col = "metrics_json"
    if col not in signals.columns:
        return None

    samples = signals[col].dropna().head(20)
    for val in samples:
        obj = _safe_json_load(val)
        fingerprint = _nested_get(obj, "swing_v1.config_fingerprint")
        if fingerprint is not None and isinstance(fingerprint, str):
            return fingerprint
    return None


# ---------------------------------------------------------------------------
# Bulk loader: load all SWING-relevant data for a window
# ---------------------------------------------------------------------------
def load_all_swing_data_pg(
    conn,
    window_start: Optional[datetime] = None,
    window_end: Optional[datetime] = None,
) -> dict[str, pd.DataFrame]:
    """Load all PostgreSQL tables relevant to SWING review.

    Returns a dict keyed by table/domain name.
    Note: adapter_parity is NOT a separate table — it is extracted from metrics_json.
    """
    return {
        "signal_records": load_signal_records_pg(conn, window_start, window_end),
        "signal_events": load_signal_events_pg(conn, window_start, window_end),
        "swing_experimental_lifecycles": load_swing_experimental_lifecycles_pg(conn, window_start, window_end),
        "scanner_shadow_diagnostics": load_scanner_shadow_diagnostics_pg(conn, window_start, window_end),
    }


# ---------------------------------------------------------------------------
# Signal summary for dashboard
# ---------------------------------------------------------------------------
def compute_swing_summary_pg(
    conn,
    window_start: Optional[datetime] = None,
    window_end: Optional[datetime] = None,
) -> dict:
    """Return a compact summary dict for the SWING review dashboard.

    Keys:
      - total_signals
      - open_signals
      - closed_signals
      - won / lost / cancelled / expired / breakeven
      - total_r, win_rate
      - latest_signal_id, latest_signal_time
      - fingerprint
      - data_quality_flag
      - adapter_parity_availability
      - same_market_bar_availability
      - scanner_diagnostics_status
    """
    signals = load_signal_records_pg(conn, window_start, window_end)
    events = load_signal_events_pg(conn, window_start, window_end)
    scanner_df = load_scanner_shadow_diagnostics_pg(conn, window_start, window_end)

    total_signals = 0
    open_signals = 0
    closed_signals = 0
    won = 0
    lost = 0
    cancelled = 0
    expired = 0
    breakeven = 0
    total_r = 0.0
    latest_signal_id = None
    latest_signal_time = None
    fingerprint = None
    data_quality_flag = "ok"
    adapter_parity_availability = "unavailable"
    same_market_bar_availability = "unavailable"
    scanner_diagnostics_status = "unverified"

    if signals is not None and not signals.empty:
        total_signals = len(signals)

        # Status
        if "status" in signals.columns:
            status = signals["status"].astype(str).str.lower()
            open_signals = int(status.isin(["open", "pending", "active"]).sum())
            closed_signals = int(status.isin(["closed", "won", "lost"]).sum())
            won = int(status.isin(["won", "win"]).sum())
            lost = int(status.isin(["lost", "loss"]).sum())
            cancelled = int(status.str.contains("cancel", na=False).sum())
            expired = int(status.str.contains("expir", na=False).sum())

        # R — prefer net_r, then pnl_r, then gross_r
        r_col = next((c for c in ["net_r", "pnl_r", "gross_r"] if c in signals.columns), None)
        if r_col:
            r_vals = pd.to_numeric(signals[r_col], errors="coerce").dropna()
            total_r = float(r_vals.sum()) if not r_vals.empty else 0.0

        # Latest signal
        if "id" in signals.columns:
            id_col = "id"
            date_col = next((c for c in ["created_at", "opened_at"] if c in signals.columns), None)
            if date_col:
                sorted_df = signals.sort_values(date_col, ascending=False)
                latest_signal_id = sorted_df[id_col].iloc[0] if not sorted_df.empty else None
                latest_signal_time = str(sorted_df[date_col].iloc[0]) if not sorted_df.empty else None

        # Fingerprint from metrics_json (NOT metadata)
        fingerprint = extract_fingerprint(signals)

        # Adapter parity availability
        ap_series = extract_adapter_parity(signals)
        if ap_series is not None and not ap_series.isna().all():
            adapter_parity_availability = "available"

        # same_market_bar availability
        if "metrics_json" in signals.columns:
            smb_vals = signals["metrics_json"].dropna().head(10)
            for val in smb_vals:
                obj = _safe_json_load(val)
                if _nested_get(obj, "swing_v1.same_market_bar") is not None:
                    same_market_bar_availability = "canonical_available"
                    break
            if same_market_bar_availability == "unavailable":
                # Check if timestamps allow derivation
                has_born = "born_timestamp" in signals.columns or "created_at" in signals.columns
                has_activation = any(c in signals.columns for c in ["activation_bar_timestamp", "activation_timestamp", "opened_at"])
                if has_born and has_activation:
                    same_market_bar_availability = "derivable_only"

    # Scanner diagnostics status
    if scanner_df is not None and not scanner_df.empty:
        if "status" in scanner_df.columns:
            scanner_diagnostics_status = str(scanner_df["status"].iloc[0]).lower()
        elif len(scanner_df) > 0:
            scanner_diagnostics_status = "available_unverified"
    else:
        scanner_diagnostics_status = "unavailable"

    # Data quality checks
    if signals is not None and not signals.empty and (events is None or events.empty):
        data_quality_flag = "degraded_no_events"
    elif signals is None or signals.empty:
        data_quality_flag = "no_data"

    return {
        "total_signals": total_signals,
        "open_signals": open_signals,
        "closed_signals": closed_signals,
        "won": won,
        "lost": lost,
        "cancelled": cancelled,
        "expired": expired,
        "breakeven": breakeven,
        "total_r": total_r,
        "win_rate": round(won / max(1, won + lost) * 100, 1) if (won + lost) > 0 else None,
        "latest_signal_id": latest_signal_id,
        "latest_signal_time": latest_signal_time,
        "fingerprint": fingerprint,
        "data_quality_flag": data_quality_flag,
        "adapter_parity_availability": adapter_parity_availability,
        "same_market_bar_availability": same_market_bar_availability,
        "scanner_diagnostics_status": scanner_diagnostics_status,
        "source": "postgresql",
        "window_start": str(window_start) if window_start else None,
        "window_end": str(window_end) if window_end else None,
    }