from __future__ import annotations

"""
swing_dashboard_service.py
---------------------------
View-model builder for the Swing Strategy Review Center dashboard.

Consumes ONLY src/swing_loaders.py (R1 PostgreSQL read-only layer).
Contains NO Streamlit code, NO file I/O, NO Telegram, NO PostgreSQL writes.
"""

from datetime import datetime, timedelta
from typing import Any, Optional

import pandas as pd

from .postgres_readonly import build_readonly_conn
from .swing_loaders import (
    _nested_get,
    _safe_json_load,
    classify_demo_compatibility,
    derive_retroactive_bar_fill,
    extract_adapter_parity,
    extract_execution_detached,
    extract_fingerprint,
    load_signal_events_pg,
    load_signal_records_pg,
    load_swing_experimental_lifecycles_pg,
    load_scanner_shadow_diagnostics_pg,
    resolve_same_market_bar,
)

COLOMBIA_OFFSET = timedelta(hours=-5)

# Canonical SWING identifiers
SWING_ENGINE = "SWING_TREND_RECLAIM"
SWING_SETUP = "SWING_TREND_RECLAIM_V1"

# BE tolerance for gross_r fallback
GROSS_R_BE_TOLERANCE = 0.0001


# ---------------------------------------------------------------------------
# Official result resolver
# ---------------------------------------------------------------------------
def resolve_official_result(row, gross_r_tolerance: float = GROSS_R_BE_TOLERANCE) -> dict:
    """Resolve the official result (WIN/LOSS/BE/UNKNOWN) for a single signal row.

    Priority order:
    1. metrics_json → swing_v1.official_result (canonical)
    2. Physical column: official_result / result / outcome
    3. Derived from gross_r (fallback, marked DERIVED_FROM_GROSS_R)
    4. INSUFFICIENT_DATA → UNKNOWN

    Returns:
        {
            "value": "WIN" | "LOSS" | "BE" | "UNKNOWN",
            "source": "CANONICAL_FIELD" | "PHYSICAL_COLUMN" | "DERIVED_FROM_GROSS_R" | "INSUFFICIENT_DATA",
            "data_available": bool,
            "warning": str or None,
        }
    """
    row_dict = row if isinstance(row, dict) else row.to_dict() if hasattr(row, 'to_dict') else {}

    # Priority 1: canonical field in metrics_json → swing_v1.official_result
    metrics_raw = row_dict.get("metrics_json") if isinstance(row_dict, dict) else getattr(row, "metrics_json", None)
    obj = _safe_json_load(metrics_raw)
    if obj is not None:
        canonical = _nested_get(obj, "swing_v1.official_result")
        if canonical is not None and isinstance(canonical, str):
            canonical_upper = canonical.upper().strip()
            if canonical_upper in ("WIN", "LOSS", "BE", "BREAKEVEN"):
                normalized = "BE" if canonical_upper in ("BE", "BREAKEVEN") else canonical_upper
                return {
                    "value": normalized,
                    "source": "CANONICAL_FIELD",
                    "data_available": True,
                    "warning": None,
                }
            # Non-standard canonical value — return as-is but note
            return {
                "value": canonical_upper,
                "source": "CANONICAL_FIELD",
                "data_available": True,
                "warning": f"Non-standard official_result value: {canonical}",
            }

    # Priority 2: Physical column
    for col in ["official_result", "result", "outcome"]:
        val = row_dict.get(col) if isinstance(row_dict, dict) else getattr(row, col, None)
        if val is not None:
            val_str = str(val).upper().strip()
            if val_str in ("WIN", "LOSS"):
                return {
                    "value": val_str,
                    "source": "PHYSICAL_COLUMN",
                    "data_available": True,
                    "warning": None,
                }
            if val_str in ("BE", "BREAKEVEN"):
                return {
                    "value": "BE",
                    "source": "PHYSICAL_COLUMN",
                    "data_available": True,
                    "warning": None,
                }

    # Priority 3: Derived from gross_r (only for closed signals)
    status_val = row_dict.get("status") if isinstance(row_dict, dict) else getattr(row, "status", None)
    status_str = str(status_val).upper().strip() if status_val is not None else ""
    is_closed = status_str in ("CLOSED", "WON", "LOST")

    if is_closed:
        gr_val = None
        for col in ["gross_r", "net_r", "pnl_r"]:
            gr_val = row_dict.get(col) if isinstance(row_dict, dict) else getattr(row, col, None)
            if gr_val is not None:
                break

        if gr_val is not None:
            try:
                gr = float(gr_val)
                if gr > gross_r_tolerance:
                    return {
                        "value": "WIN",
                        "source": "DERIVED_FROM_GROSS_R",
                        "data_available": True,
                        "warning": "Official result derived from gross_r — canonical field absent",
                    }
                elif gr < -gross_r_tolerance:
                    return {
                        "value": "LOSS",
                        "source": "DERIVED_FROM_GROSS_R",
                        "data_available": True,
                        "warning": "Official result derived from gross_r — canonical field absent",
                    }
                else:
                    return {
                        "value": "BE",
                        "source": "DERIVED_FROM_GROSS_R",
                        "data_available": True,
                        "warning": "Official result derived from gross_r (within BE tolerance) — canonical field absent",
                    }
            except (ValueError, TypeError):
                pass

    # Priority 4: Insufficient data
    return {
        "value": "UNKNOWN",
        "source": "INSUFFICIENT_DATA",
        "data_available": False,
        "warning": "Cannot determine official result: no canonical field, no physical column, no gross_r",
    }


def apply_official_results(signals: pd.DataFrame) -> pd.DataFrame:
    """Apply resolve_official_result to every row, adding derived columns.

    Adds columns: official_result_value, official_result_source
    """
    if signals is None or signals.empty:
        return signals

    results = signals.apply(resolve_official_result, axis=1)
    signals["official_result_value"] = results.apply(lambda r: r["value"])
    signals["official_result_source"] = results.apply(lambda r: r["source"])
    return signals


# ---------------------------------------------------------------------------
# Fingerprint selector helpers
# ---------------------------------------------------------------------------
def _determine_latest_fingerprint(signals: pd.DataFrame) -> Optional[str]:
    """Find the fingerprint of the most recent SWING signal by created_at."""
    if signals is None or signals.empty or "metrics_json" not in signals.columns:
        return None

    if "created_at" not in signals.columns:
        return None

    sorted_signals = signals.sort_values("created_at", ascending=False)
    for _, row in sorted_signals.iterrows():
        obj = _safe_json_load(row.get("metrics_json"))
        fp = _nested_get(obj, "swing_v1.config_fingerprint")
        if fp and isinstance(fp, str) and len(fp) >= 8:
            return fp
    return None


def filter_signals_by_fingerprint(signals: pd.DataFrame, selected_fp: Optional[str]) -> tuple[pd.DataFrame, int, int]:
    """Filter signals to a specific fingerprint (or keep all if selected_fp is None).

    Returns (filtered_df, included_count, excluded_count).
    Signals without a valid fingerprint (>=8 chars) are classified as UNKNOWN_CONFIG
    and excluded from any specific fingerprint selection, included only in "All".
    """
    if signals is None or signals.empty:
        return signals, 0, 0

    if selected_fp is None or selected_fp == "ALL":
        return signals, len(signals), 0

    total_before = len(signals)
    mask = pd.Series(False, index=signals.index)

    if "metrics_json" in signals.columns:
        for idx in signals.index:
            obj = _safe_json_load(signals.loc[idx, "metrics_json"])
            fp = _nested_get(obj, "swing_v1.config_fingerprint")
            if fp and isinstance(fp, str) and len(fp) >= 8:
                mask.loc[idx] = (fp == selected_fp)

    filtered = signals[mask].copy()
    included = len(filtered)
    excluded = total_before - included
    return filtered, included, excluded


def is_swing_trend_reclaim_signal(row: dict) -> bool:
    """Determine whether a signal_records row belongs to SWING_TREND_RECLAIM_V1.

    Uses canonical evidence in priority order:
    1. engine_name or setup containing SWING_TREND_RECLAIM
    2. metrics_json → swing_v1 presence with valid config_fingerprint
    3. setup_type/setup_id matching canonical SWING format (auxiliary only)

    Returns True only for official SWING_TREND_RECLAIM_V1 signals.
    """
    # Priority 1: engine_name / setup explicit
    engine = _safe_str(row.get("engine_name") if isinstance(row, dict) else getattr(row, "engine_name", None))
    setup = _safe_str(row.get("setup") if isinstance(row, dict) else getattr(row, "setup", None))
    strategy = _safe_str(row.get("strategy") if isinstance(row, dict) else getattr(row, "strategy", None))
    setup_type = _safe_str(row.get("setup_type") if isinstance(row, dict) else getattr(row, "setup_type", None))

    combined = f"{engine or ''} {setup or ''} {strategy or ''} {setup_type or ''}".upper()
    if "SWING_TREND_RECLAIM" in combined:
        return True

    # Priority 2: metrics_json → swing_v1 valid presence
    metrics_raw = row.get("metrics_json") if isinstance(row, dict) else getattr(row, "metrics_json", None)
    obj = _safe_json_load(metrics_raw)
    if obj is not None:
        swing_v1 = _nested_get(obj, "swing_v1")
        if isinstance(swing_v1, dict):
            fp = swing_v1.get("config_fingerprint")
            if fp and isinstance(fp, str) and len(fp) >= 8:
                return True

    # Priority 3: setup_type / setup_id contains SWING canonical format
    setup_id = _safe_str(row.get("setup_id") if isinstance(row, dict) else getattr(row, "setup_id", None))
    if setup_id and "SWING" in setup_id.upper():
        return True

    return False


def _safe_str(val) -> str:
    """Coerce to str, return empty string for None/nan."""
    if val is None:
        return ""
    try:
        if isinstance(val, float) and pd.isna(val):
            return ""
    except Exception:
        pass
    return str(val)


def filter_swing_official_signals(signals: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """Filter DataFrame to only official SWING_TREND_RECLAIM_V1 signals.

    Returns (filtered_df, excluded_count).
    """
    if signals is None or signals.empty:
        return signals, 0

    total_before = len(signals)
    mask = signals.apply(is_swing_trend_reclaim_signal, axis=1)
    filtered = signals[mask].copy()
    excluded = total_before - len(filtered)
    return filtered, excluded


def extract_nested_timestamp(signals: pd.DataFrame, json_path: str) -> pd.Series:
    """Extract a timestamp from metrics_json → swing_v1.{field} for each row.

    Returns a Series of pd.Timestamp or NaT, aligned with signals index.
    """
    if signals is None or signals.empty:
        return pd.Series(dtype="datetime64[ns]")

    def _extract(val) -> Optional[Any]:
        obj = _safe_json_load(val)
        ts = _nested_get(obj, f"swing_v1.{json_path}")
        if ts is None:
            return None
        try:
            return pd.Timestamp(ts)
        except Exception:
            return None

    if "metrics_json" in signals.columns:
        return signals["metrics_json"].apply(_extract)
    return pd.Series([None] * len(signals), index=signals.index)


def normalize_side(signals: pd.DataFrame) -> pd.Series:
    """Extract and normalize side (LONG/SHORT/UNKNOWN) from signal_records.

    Checks in priority order:
    1. side column (physical)
    2. signal_type column (physical)
    3. metrics_json → swing_v1.direction
    """
    if signals is None or signals.empty:
        return pd.Series(dtype=str)

    result = pd.Series("UNKNOWN", index=signals.index)

    # Priority 1: physical side column
    if "side" in signals.columns:
        side_vals = signals["side"].astype(str).str.upper().str.strip()
        result = side_vals.apply(lambda x: x if x in ("LONG", "SHORT") else "UNKNOWN")

    # Priority 2: signal_type column (fills UNKNOWN only)
    if "signal_type" in signals.columns:
        st_vals = signals["signal_type"].astype(str).str.upper().str.strip()
        for idx in result[result == "UNKNOWN"].index:
            v = st_vals.loc[idx] if idx in st_vals.index else ""
            if v in ("LONG", "SHORT", "BUY", "SELL"):
                result.loc[idx] = "LONG" if v in ("LONG", "BUY") else "SHORT"

    # Priority 3: metrics_json → swing_v1.direction (fills UNKNOWN only)
    if "metrics_json" in signals.columns:
        for idx in result[result == "UNKNOWN"].index:
            val = signals["metrics_json"].loc[idx] if idx in signals.index else None
            obj = _safe_json_load(val)
            direction = _nested_get(obj, "swing_v1.direction")
            if direction is not None:
                d_str = str(direction).upper().strip()
                if d_str in ("LONG", "SHORT", "BUY", "SELL"):
                    result.loc[idx] = "LONG" if d_str in ("LONG", "BUY") else "SHORT"

    return result


# ---------------------------------------------------------------------------
# Window helpers
# ---------------------------------------------------------------------------
def window_days(days: int) -> tuple[datetime, datetime]:
    """Return (start, end) Colombia time for the last N days."""
    end = datetime.utcnow() + COLOMBIA_OFFSET
    start = end - timedelta(days=days)
    return start, end


def custom_window(start_str: str, end_str: str) -> tuple[datetime, datetime]:
    """Parse a custom Colombia window. Expects ISO-like strings."""
    start = datetime.fromisoformat(start_str)
    end = datetime.fromisoformat(end_str)
    return start, end


# ---------------------------------------------------------------------------
# Data quality traffic light
# ---------------------------------------------------------------------------
def assess_data_quality(signals: pd.DataFrame, fingerprint: Optional[str]) -> dict:
    """Return a dict with quality assessment.

    Returns:
        {
            "level": "GOOD" | "PARTIAL" | "INSUFFICIENT" | "INVALID",
            "reasons": list[str],
        }
    """
    reasons: list[str] = []

    if signals is None or signals.empty:
        return {"level": "INSUFFICIENT", "reasons": ["No signal records in window"]}

    total = len(signals)

    # Fingerprint
    if fingerprint is None:
        reasons.append("No config fingerprint found")
    else:
        # Check for multiple fingerprints
        fps = set()
        if "metrics_json" in signals.columns:
            for val in signals["metrics_json"].dropna().head(200):
                obj = _safe_json_load(val)
                fp = _nested_get(obj, "swing_v1.config_fingerprint")
                if fp and isinstance(fp, str):
                    fps.add(fp)
        if len(fps) > 1:
            reasons.append(f"Multiple config fingerprints detected: {len(fps)}")

    # metrics_json parseable
    if "metrics_json" in signals.columns:
        parseable = signals["metrics_json"].dropna().apply(
            lambda v: _safe_json_load(v) is not None
        )
        parse_pct = parseable.mean()
        if parse_pct < 0.5:
            reasons.append(f"Low metrics_json parse rate: {parse_pct:.0%}")
            return {"level": "INVALID", "reasons": reasons}
        elif parse_pct < 0.9:
            reasons.append(f"Partial metrics_json parse rate: {parse_pct:.0%}")

    # gross_r availability in closed signals
    if "status" in signals.columns and "gross_r" in signals.columns:
        closed = signals[signals["status"].astype(str).str.upper().isin(["CLOSED", "WON", "LOST"])]
        if len(closed) > 0:
            gr_avail = closed["gross_r"].notna().mean()
            if gr_avail < 0.8:
                reasons.append(f"Low gross_r availability in closed: {gr_avail:.0%}")

    # Adapter parity
    if "metrics_json" in signals.columns:
        ap_series = extract_adapter_parity(signals)
        ap_avail = ap_series.notna().mean() if ap_series is not None else 0
        if ap_avail == 0:
            reasons.append("No adapter_parity data available")
        elif ap_avail < 0.3:
            reasons.append(f"Low adapter_parity coverage: {ap_avail:.0%}")

    if total < 5:
        reasons.append(f"Very small sample ({total} signals)")
        return {"level": "INSUFFICIENT", "reasons": reasons}

    if len(reasons) == 0:
        return {"level": "GOOD", "reasons": []}
    elif len(reasons) <= 2:
        return {"level": "PARTIAL", "reasons": reasons}
    else:
        return {"level": "INSUFFICIENT", "reasons": reasons}


# ---------------------------------------------------------------------------
# Main view-model builder
# ---------------------------------------------------------------------------
def build_swing_dashboard(
    window_start: datetime,
    window_end: datetime,
) -> dict:
    """Build the complete view-model for the SWING dashboard.

    Opens and closes its own PostgreSQL read-only connection.
    Returns a dict ready for Streamlit rendering.
    """
    result: dict[str, Any] = {
        "window_start_co": window_start,
        "window_end_co": window_end,
        "window_start_utc": window_start - COLOMBIA_OFFSET,
        "window_end_utc": window_end - COLOMBIA_OFFSET,
        "loaded_at": datetime.utcnow(),
        "error": None,
    }

    try:
        conn = build_readonly_conn()
    except Exception as e:
        result["error"] = f"PostgreSQL connection failed: {e}"
        return result

    try:
        # Load data — pre-filter SQL if possible
        signals_raw = load_signal_records_pg(conn, window_start, window_end)
        events = load_signal_events_pg(conn, window_start, window_end)
        experiments = load_swing_experimental_lifecycles_pg(conn, window_start, window_end)
        scanner = load_scanner_shadow_diagnostics_pg(conn, window_start, window_end)

        # --- Apply SWING scope filter ---
        signals, excluded_non_swing = filter_swing_official_signals(signals_raw)
        result["excluded_non_swing"] = excluded_non_swing

        # Enrich with nested timestamps from metrics_json
        if signals is not None and not signals.empty:
            # Extract born_timestamp from metrics_json → swing_v1
            nested_born = extract_nested_timestamp(signals, "born_timestamp")
            if nested_born.notna().any():
                signals["born_timestamp"] = nested_born.combine_first(
                    signals.get("born_timestamp", pd.Series(dtype="datetime64[ns]"))
                )

            # Extract activation_timestamp from metrics_json → swing_v1
            nested_act = extract_nested_timestamp(signals, "activation_timestamp")
            if nested_act.notna().any():
                signals["activation_timestamp"] = nested_act.combine_first(
                    signals.get("activation_timestamp", pd.Series(dtype="datetime64[ns]"))
                )

            # Extract activation_bar_timestamp from metrics_json → swing_v1
            nested_act_bar = extract_nested_timestamp(signals, "activation_bar_timestamp")
            if nested_act_bar.notna().any():
                signals["activation_bar_timestamp"] = nested_act_bar.combine_first(
                    signals.get("activation_bar_timestamp", pd.Series(dtype="datetime64[ns]"))
                )

            # Normalize side
            signals["normalized_side"] = normalize_side(signals)

        fingerprint = extract_fingerprint(signals)
        result["fingerprint"] = fingerprint

        # Fingerprint segmentation
        result["fingerprint_segmentation"] = _fingerprint_segmentation(signals, fingerprint)

        # Data quality
        result["data_quality"] = assess_data_quality(signals, fingerprint)

        # --- Signal KPIs ---
        result["total_signals"] = len(signals) if signals is not None else 0
        result["signal_kpis"] = _compute_signal_kpis(signals)

        # --- Executability ---
        result["executability"] = _build_executability(signals)

        # --- Signal detail table ---
        result["signal_table"] = _build_signal_table(signals)

        # --- Experiments ---
        result["experiments"] = _build_experiments_panel(experiments)

        # --- Scanner diagnostics ---
        result["scanner"] = _build_scanner_panel(scanner)

        # --- Events info ---
        result["total_events"] = len(events) if events is not None else 0

        # --- Store raw signals DataFrame for fingerprint filtering in UI ---
        result["_signals_df"] = signals

    except Exception as e:
        result["error"] = f"Data loading failed: {e}"
    finally:
        try:
            conn.close()
        except Exception:
            pass

    return result


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------
def _compute_signal_kpis(signals: pd.DataFrame) -> dict:
    if signals is None or signals.empty:
        return {"available": False}

    status_col = "status" if "status" in signals.columns else None
    status = signals[status_col].astype(str).str.upper() if status_col else pd.Series(dtype=str)

    total = len(signals)

    # ---- A. Lifecycle status ----
    pending = int(status.isin(["PENDING"]).sum()) if status_col else 0
    activated = int(status.isin(["ACTIVATED", "OPEN"]).sum()) if status_col else 0
    closed = int(status.isin(["CLOSED", "WON", "LOST"]).sum()) if status_col else 0
    cancelled = int(status.str.contains("CANCEL", na=False).sum()) if status_col else 0
    expired = int(status.str.contains("EXPIR", na=False).sum()) if status_col else 0
    other = total - closed - pending - activated - cancelled - expired

    # ---- B. Official result using resolve_official_result ----
    win = 0
    loss = 0
    be = 0
    unknown = 0
    canonical_count = 0
    derived_count = 0

    if not signals.empty:
        results = signals.apply(resolve_official_result, axis=1)
        # Only count closed signals
        closed_mask = status.isin(["CLOSED", "WON", "LOST"]) if status_col else pd.Series(False, index=signals.index)
        for idx in signals.index:
            if closed_mask.loc[idx] if idx in closed_mask.index else False:
                r = results.loc[idx]
                val = r["value"]
                src = r["source"]
                if val == "WIN":
                    win += 1
                    if src == "CANONICAL_FIELD":
                        canonical_count += 1
                    elif src == "DERIVED_FROM_GROSS_R":
                        derived_count += 1
                elif val == "LOSS":
                    loss += 1
                    if src == "CANONICAL_FIELD":
                        canonical_count += 1
                    elif src == "DERIVED_FROM_GROSS_R":
                        derived_count += 1
                elif val == "BE":
                    be += 1
                else:
                    unknown += 1

    # ---- C. R metrics (official signals only, closed, gross_r) ----
    r_col = next((c for c in ["gross_r", "net_r", "pnl_r"] if c in signals.columns), None)

    total_r = None
    avg_r = None
    pf = None
    pf_warning = None
    closed_evaluable = 0

    if r_col and status_col:
        closed_mask = status.isin(["CLOSED", "WON", "LOST"])
        r_vals = pd.to_numeric(signals[r_col], errors="coerce")
        closed_r = r_vals[closed_mask].dropna()
        closed_evaluable = len(closed_r)

        if closed_evaluable > 0:
            total_r = float(closed_r.sum())
            avg_r = float(closed_r.mean())

            positive = closed_r[closed_r > 0].sum()
            negative = abs(closed_r[closed_r < 0].sum())
            if negative > 0:
                pf = float(positive / negative)
            elif positive > 0:
                pf = float("inf")
                pf_warning = "Sample warning: no losses with gross_r in closed signals"

    # Latest signal
    latest_id = None
    if "id" in signals.columns and "created_at" in signals.columns:
        sorted_df = signals.sort_values("created_at", ascending=False)
        latest_id = int(sorted_df["id"].iloc[0]) if not sorted_df.empty else None

    return {
        "available": True,
        "total": total,
        # Lifecycle status
        "lifecycle_pending": pending,
        "lifecycle_activated": activated,
        "lifecycle_closed": closed,
        "lifecycle_cancelled": cancelled,
        "lifecycle_expired": expired,
        "lifecycle_other": other,
        # Official result (resolved via canonical → physical → gross_r → unknown)
        "result_win": win,
        "result_loss": loss,
        "result_be": be,
        "result_unknown": unknown,
        "result_canonical_count": canonical_count,
        "result_derived_count": derived_count,
        # R metrics
        "closed_evaluable": closed_evaluable,
        "total_r": round(total_r, 4) if total_r is not None else None,
        "avg_r": round(avg_r, 4) if avg_r is not None else None,
        "profit_factor": round(pf, 4) if pf is not None and pf != float("inf") else ("∞" if pf == float("inf") else None),
        "pf_warning": pf_warning,
        "latest_signal_id": latest_id,
    }


def _build_executability(signals: pd.DataFrame) -> dict:
    if signals is None or signals.empty:
        return {"available": False}

    # Extract fields
    ed_series = extract_execution_detached(signals)
    ap_series = extract_adapter_parity(signals)

    smb_results = []
    demo_results = []
    rbf_results = []

    for idx, row in signals.iterrows():
        # same_market_bar — extract nested timestamps from metrics_json first
        metrics = row.get("metrics_json")
        obj = _safe_json_load(metrics)

        # born_timestamp: prefer metrics_json → swing_v1.born_timestamp, then physical column, then created_at
        born = _resolve_nested_timestamp(obj, row, "born_timestamp") or row.get("created_at")

        # activation_bar_timestamp: prefer metrics_json, then physical column
        act_bar = _resolve_nested_timestamp(obj, row, "activation_bar_timestamp")

        # activation_timestamp: prefer metrics_json, then physical column
        act_ts = _resolve_nested_timestamp(obj, row, "activation_timestamp")

        smb = resolve_same_market_bar(metrics, born, act_bar, act_ts)
        smb_results.append(smb)

        # demo compatibility
        ap_val = ap_series.loc[idx] if ap_series is not None else None
        ed_val = ed_series.loc[idx] if ed_series is not None else None
        demo = classify_demo_compatibility(ap_val, ed_val)
        demo_results.append(demo)

        # retroactive bar fill
        created = row.get("created_at")
        rbf = derive_retroactive_bar_fill(created, created, act_bar)
        rbf_results.append(rbf)

    # Summarise counts
    smb_true = sum(1 for r in smb_results if r["value"] is True)
    smb_false = sum(1 for r in smb_results if r["value"] is False)
    smb_none = sum(1 for r in smb_results if r["value"] is None)
    smb_derived = sum(1 for r in smb_results if r["derivation_source"] == "DERIVED_FROM_TIMESTAMPS")
    smb_canonical = sum(1 for r in smb_results if r["derivation_source"] == "CANONICAL_FIELD")

    if ed_series is not None:
        ed_true = int(ed_series.dropna().astype(bool).sum())
        ed_false = int((ed_series.dropna().astype(bool) == False).sum())
        ed_none = int(ed_series.isna().sum())
    else:
        ed_true = ed_false = ed_none = 0

    demo_counts = {}
    for d in demo_results:
        demo_counts[d] = demo_counts.get(d, 0) + 1

    rbf_true = sum(1 for r in rbf_results if r is True)
    rbf_false = sum(1 for r in rbf_results if r is False)
    rbf_none = sum(1 for r in rbf_results if r is None)

    return {
        "available": True,
        "same_market_bar": {
            "true": smb_true,
            "false": smb_false,
            "none": smb_none,
            "derived": smb_derived,
            "canonical": smb_canonical,
        },
        "execution_detached": {
            "true": ed_true,
            "false": ed_false,
            "none": ed_none,
        },
        "demo_compatibility": demo_counts,
        "retroactive_bar_fill": {
            "true": rbf_true,
            "false": rbf_false,
            "none": rbf_none,
        },
    }


def _resolve_nested_timestamp(obj: Optional[dict], row, field: str) -> Optional[Any]:
    """Resolve a timestamp: prefer metrics_json → swing_v1.{field}, then physical column."""
    if obj is not None:
        ts = _nested_get(obj, f"swing_v1.{field}")
        if ts is not None:
            try:
                return pd.Timestamp(ts)
            except Exception:
                pass
    # Fallback to physical column
    val = row.get(field) if isinstance(row, dict) else getattr(row, field, None)
    if val is not None:
        try:
            return pd.Timestamp(val)
        except Exception:
            pass
    return None


def _fingerprint_segmentation(signals: pd.DataFrame, primary_fingerprint: Optional[str]) -> dict:
    """Identify distinct fingerprints within filtered SWING signals.

    Returns segmentation info for UI: counts per fingerprint, warning if mixed.
    """
    if signals is None or signals.empty:
        return {"available": False, "fingerprints": {}, "warning": None}

    fp_counts: dict[str, int] = {}
    if "metrics_json" in signals.columns:
        for val in signals["metrics_json"].dropna():
            obj = _safe_json_load(val)
            fp = _nested_get(obj, "swing_v1.config_fingerprint")
            if fp and isinstance(fp, str) and len(fp) >= 8:
                fp_counts[fp] = fp_counts.get(fp, 0) + 1

    num_fps = len(fp_counts)
    warning = None
    if num_fps > 1:
        warning = "MIXED CONFIG — multiple SWING fingerprints in window. PF may not be comparable across versions."
    elif num_fps == 0:
        warning = "No config fingerprint found in any SWING signal."

    return {
        "available": True,
        "fingerprints": fp_counts,
        "num_distinct": num_fps,
        "primary": primary_fingerprint,
        "warning": warning,
    }


def _build_signal_table(signals: pd.DataFrame) -> pd.DataFrame:
    if signals is None or signals.empty:
        return pd.DataFrame()

    cols_to_keep = [
        "id", "symbol", "side", "status", "created_at",
    ]
    available_cols = [c for c in cols_to_keep if c in signals.columns]
    table = signals[available_cols].copy()

    # Add derived columns
    if "created_at" in table.columns:
        table["created_at_co"] = table["created_at"].apply(
            lambda t: t + COLOMBIA_OFFSET if pd.notna(t) else None
        )

    # Normalized side
    if "normalized_side" in signals.columns:
        table["side"] = signals["normalized_side"]
    else:
        table["side"] = normalize_side(signals)

    # same_market_bar — extract nested timestamps correctly
    smb_vals = []
    smb_sources = []
    for _, row in signals.iterrows():
        metrics = row.get("metrics_json")
        obj = _safe_json_load(metrics)
        born = _resolve_nested_timestamp(obj, row, "born_timestamp") or row.get("created_at")
        act_bar = _resolve_nested_timestamp(obj, row, "activation_bar_timestamp")
        act_ts = _resolve_nested_timestamp(obj, row, "activation_timestamp")
        smb = resolve_same_market_bar(metrics, born, act_bar, act_ts)
        smb_vals.append(smb["value"])
        smb_sources.append(smb["derivation_source"])
    table["same_market_bar"] = smb_vals
    table["smb_source"] = smb_sources

    # execution_detached
    ed_series = extract_execution_detached(signals)
    table["execution_detached"] = ed_series.values if ed_series is not None else None

    # demo classification
    ap_series = extract_adapter_parity(signals)
    demo_vals = []
    for idx in signals.index:
        ap_val = ap_series.loc[idx] if ap_series is not None else None
        ed_val = ed_series.loc[idx] if ed_series is not None else None
        demo_vals.append(classify_demo_compatibility(ap_val, ed_val))
    table["demo_classification"] = demo_vals

    # Official result and source
    results = signals.apply(resolve_official_result, axis=1)
    table["official_result_value"] = results.apply(lambda r: r["value"])
    table["official_result_source"] = results.apply(lambda r: r["source"])

    # Config fingerprint
    fp_vals = []
    for _, row in signals.iterrows():
        obj = _safe_json_load(row.get("metrics_json"))
        fp = _nested_get(obj, "swing_v1.config_fingerprint")
        fp_vals.append(fp if fp and isinstance(fp, str) and len(fp) >= 8 else None)
    table["config_fingerprint"] = fp_vals

    # Gross R in table
    for col in ["gross_r", "net_r", "pnl_r"]:
        if col in signals.columns:
            table["gross_r"] = signals[col]
            break

    return table


def _build_experiments_panel(experiments: pd.DataFrame) -> dict:
    if experiments is None or experiments.empty:
        return {"available": False, "rows": 0}

    return {
        "available": True,
        "rows": len(experiments),
        "table": experiments,
    }


def _build_scanner_panel(scanner: pd.DataFrame) -> dict:
    if scanner is None or scanner.empty:
        return {"available": False, "status": "No data available"}

    if "status" in scanner.columns and scanner["status"].iloc[0] in (
        "UNVERIFIED_NO_DATA",
        "UNVERIFIED_TABLE_MISSING",
    ):
        return {
            "available": False,
            "status": "No data available",
            "confidence": "STALE / LOW CONFIDENCE / NON-OFFICIAL",
        }

    return {
        "available": True,
        "rows": len(scanner),
        "confidence": "SECONDARY_DIAGNOSTIC",
    }