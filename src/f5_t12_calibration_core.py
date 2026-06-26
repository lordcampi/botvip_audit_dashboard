"""
F5_T12 — Calibration Cockpit Core Library
==========================================
Pure metric computation functions for the F5_T12 Calibration Cockpit.
No UI, no DB writes, no bot runtime modifications.

Designed to work with the confirmed schema:
  signal_records: id, symbol, signal_type, setup, in_killzone, operating_mode,
                  btc_trend, weekend, spread_pct, quote_volume, metrics_json,
                  exit_reason, pnl_r, gross_r, net_r, engine_name, is_shadow,
                  signal_tier, market_regime
  signal_events: id, signal_id, event_type, event_time, price, message,
                 metadata_json, telegram_message_id
  scanner_candidate_shadow_snapshots: id, created_at, cycle_id, mode, symbol,
                                      reason, adx, rvol, atr_extension, score,
                                      metadata_json
"""

from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple, Union

# ---------------------------------------------------------------------------
# Safe numeric helpers
# ---------------------------------------------------------------------------


def safe_float(value: Any, default: Optional[float] = None) -> Optional[float]:
    """Convert value to float safely. Returns default on failure."""
    if value is None:
        return default
    if isinstance(value, (int, float)):
        result = float(value)
        if math.isnan(result) or math.isinf(result):
            return default
        return result
    if isinstance(value, str):
        value = value.strip()
        if not value:
            return default
        try:
            result = float(value)
            if math.isnan(result) or math.isinf(result):
                return default
            return result
        except (ValueError, TypeError):
            return default
    return default



def safe_int(value: Any, default: Optional[int] = None) -> Optional[int]:
    """Convert value to int safely."""
    if value is None:
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return default
        return int(value)
    if isinstance(value, str):
        value = value.strip()
        if not value:
            return default
        try:
            return int(float(value))
        except (ValueError, TypeError):
            return default
    return default


def parse_json_safe(text: Any) -> dict:
    """Parse JSON string safely. Returns empty dict on failure."""
    if text is None:
        return {}
    if isinstance(text, dict):
        return text
    if isinstance(text, str):
        text = text.strip()
        if not text:
            return {}
        try:
            return json.loads(text)
        except (json.JSONDecodeError, ValueError, TypeError):
            return {}
    return {}


# ---------------------------------------------------------------------------
# Normalization helpers
# ---------------------------------------------------------------------------


def normalize_side(signal_type: Any) -> str:
    """Normalize signal_type to LONG/SHORT/UNKNOWN."""
    if signal_type is None:
        return "UNKNOWN"
    s = str(signal_type).strip().upper()
    if s in ("LONG", "BUY", "CALL"):
        return "LONG"
    if s in ("SHORT", "SELL", "PUT"):
        return "SHORT"
    return "UNKNOWN"


def normalize_session(created_at: Any) -> str:
    """Classify a timestamp into a session bucket.

    Session buckets (Colombia/Bogota timezone assumed, UTC-5):
      asia:        00:00-06:59  (05:00-11:59 UTC)
      london_open: 07:00-09:59  (12:00-14:59 UTC)
      london_mid:  10:00-12:59  (15:00-17:59 UTC)
      ny_open:     13:00-15:59  (18:00-20:59 UTC)
      ny_mid:      16:00-18:59  (21:00-23:59 UTC)
      ny_close:    19:00-21:59  (00:00-02:59 UTC)
      off_hours:   22:00-23:59  (03:00-04:59 UTC)
    """
    if created_at is None:
        return "unknown"
    try:
        if isinstance(created_at, str):
            dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        elif isinstance(created_at, (int, float)):
            dt = datetime.fromtimestamp(created_at, tz=timezone.utc)
        elif hasattr(created_at, "hour"):
            dt = created_at
        else:
            return "unknown"
        # Convert to Bogota time (UTC-5)
        hour = dt.hour
        if dt.tzinfo is not None:
            utc_offset = dt.utcoffset()
            if utc_offset is not None:
                # Bogota is UTC-5, so subtract 5 hours from UTC
                hour = (dt.hour - 5 + utc_offset.total_seconds() / 3600) % 24
    except (ValueError, TypeError, AttributeError):
        return "unknown"

    hour = int(hour) % 24

    if hour < 7:
        return "asia"
    elif hour < 10:
        return "london_open"
    elif hour < 13:
        return "london_mid"
    elif hour < 16:
        return "ny_open"
    elif hour < 19:
        return "ny_mid"
    elif hour < 22:
        return "ny_close"
    else:
        return "off_hours"




def compute_weekend_flag(row: dict) -> bool:
    """Determine if a row is a weekend signal.

    Priority:
      1. 'weekend' column if present and truthy.
      2. Fallback to session bucket (saturday/sunday).
    """
    weekend_raw = row.get("weekend")
    if weekend_raw is not None:
        if isinstance(weekend_raw, bool):
            return weekend_raw
        if isinstance(weekend_raw, (int, float)):
            return bool(weekend_raw)
        s = str(weekend_raw).strip().lower()
        if s in ("1", "true", "yes", "t"):
            return True
        if s in ("0", "false", "no", "f"):
            return False

    # Fallback: check created_at for saturday/sunday
    created_at = row.get("created_at")
    if created_at is not None:
        try:
            if isinstance(created_at, str):
                dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
            elif isinstance(created_at, (int, float)):
                dt = datetime.fromtimestamp(created_at, tz=timezone.utc)
            elif hasattr(created_at, "weekday"):
                dt = created_at
            else:
                return False
            return dt.weekday() >= 5  # Saturday=5, Sunday=6
        except (ValueError, TypeError):
            pass
    return False



# ---------------------------------------------------------------------------
# Exit family bucketing
# ---------------------------------------------------------------------------


def bucket_exit_family(row: dict) -> str:
    """Classify exit_reason into a family bucket.

    Families:
      take_profit       -> tp / runner_tp_hit
      stop_loss         -> sl / stop_loss
      breakeven         -> be / breakeven / breakeven_stop
      runner_breakeven  -> runner_breakeven / runner_breakeven_stop
      time_stop         -> time_stop / time
      no_progress       -> no_progress
      mfe_stall         -> mfe_stall
      expired           -> expired / expired_pending
      unknown           -> everything else
    """
    exit_reason = row.get("exit_reason")
    if exit_reason is None:
        return "unknown_or_open"

    reason = str(exit_reason).strip().lower()

    # Direct exit_reason matching
    if reason in ("take_profit", "tp", "primary_tp_hit", "runner_tp_hit", "partial_tp_hit"):
        return "take_profit"
    if reason in ("stop_loss", "sl", "real_stop_loss", "stop_loss_hit"):
        return "stop_loss"
    if reason in ("breakeven", "breakeven_stop", "be", "breakeven_stop_hit", "sl_moved_to_breakeven"):
        return "breakeven"
    if reason in ("runner_breakeven", "runner_breakeven_stop", "runner_breakeven_stop_hit"):
        return "runner_breakeven"
    if reason in ("time_stop", "time", "time_stop_exit"):
        return "time_stop"
    if reason in ("no_progress", "no_progress_exit"):
        return "no_progress"
    if reason in ("mfe_stall", "mfe_stall_exit"):
        return "mfe_stall"
    if reason in ("expired", "expired_pending", "cancelled_expired"):
        return "expired"

    # Partial match for compound reasons
    if "no_progress" in reason:
        return "no_progress"
    if "mfe_stall" in reason:
        return "mfe_stall"
    if "breakeven" in reason:
        return "breakeven"
    if "runner" in reason and "breakeven" in reason:
        return "runner_breakeven"
    if "time_stop" in reason or "time" in reason:
        return "time_stop"
    if "tp" in reason or "take_profit" in reason:
        return "take_profit"
    if "sl" in reason or "stop_loss" in reason:
        return "stop_loss"
    if "expired" in reason:
        return "expired"

    return "unknown_or_open"


def is_managed_exit(exit_family: str) -> bool:
    """Return True if the exit family is a managed (non-directional) exit."""
    return exit_family in ("no_progress", "mfe_stall", "time_stop", "breakeven", "runner_breakeven", "expired", "unknown_or_open")


def is_directional_exit(exit_family: str) -> bool:
    """Return True if the exit family is a directional TP/SL."""
    return exit_family in ("take_profit", "stop_loss")


# ---------------------------------------------------------------------------
# R-value extraction
# ---------------------------------------------------------------------------


def extract_r_values(rows: List[dict], r_column: str = "net_r") -> List[float]:
    """Extract clean R values from a list of row dicts."""
    values: List[float] = []
    for row in rows:
        r = safe_float(row.get(r_column))
        if r is not None and math.isfinite(r):
            values.append(r)
    return values


def extract_r_values_with_meta(rows: List[dict], r_column: str = "net_r") -> Tuple[List[float], int, int]:
    """Extract R values and count wins/losses."""
    values = extract_r_values(rows, r_column)
    wins = sum(1 for v in values if v > 0)
    losses = sum(1 for v in values if v < 0)
    return values, wins, losses


# ---------------------------------------------------------------------------
# Profit Factor computation
# ---------------------------------------------------------------------------


def compute_profit_factor_stats(
    rows: List[dict],
    r_column: str = "net_r",
    min_count: int = 1,
) -> dict:
    """Compute Profit Factor and related statistics from a list of rows.

    Returns a dict with:
      count, r_values_count, wins, losses,
      gross_win_r, gross_loss_abs_r, net_r,
      avg_r, avg_win_r, avg_loss_r,
      profit_factor, decision, confidence
    """
    r_values = extract_r_values(rows, r_column)
    r_count = len(r_values)

    if r_count == 0:
        return {
            "count": len(rows),
            "r_values_count": 0,
            "wins": 0,
            "losses": 0,
            "gross_win_r": 0.0,
            "gross_loss_abs_r": 0.0,
            "net_r": 0.0,
            "avg_r": None,
            "avg_win_r": None,
            "avg_loss_r": None,
            "profit_factor": None,
            "decision": "NO_R_VALUES",
            "confidence": "LOW",
        }

    gross_win_r = sum(v for v in r_values if v > 0)
    gross_loss_abs_r = abs(sum(v for v in r_values if v < 0))
    net_r = gross_win_r - gross_loss_abs_r
    avg_r = sum(r_values) / r_count

    win_values = [v for v in r_values if v > 0]
    loss_values = [v for v in r_values if v < 0]
    wins = len(win_values)
    losses = len(loss_values)

    avg_win_r = sum(win_values) / wins if wins > 0 else None
    avg_loss_r = sum(loss_values) / losses if losses > 0 else None

    # Profit Factor
    if gross_loss_abs_r > 0:
        profit_factor = gross_win_r / gross_loss_abs_r
    elif gross_win_r > 0:
        profit_factor = None  # No losses, infinite PF
    else:
        profit_factor = None  # No wins and no losses

    # Decision
    decision = classify_decision(
        r_values_count=r_count,
        profit_factor=profit_factor,
        net_r=net_r,
        gross_win_r=gross_win_r,
        gross_loss_abs_r=gross_loss_abs_r,
        min_count=min_count,
    )

    # Confidence
    if r_count < 5:
        confidence = "VERY_LOW"
    elif r_count < 10:
        confidence = "LOW"
    elif r_count < 30:
        confidence = "MEDIUM"
    else:
        confidence = "HIGH"

    return {
        "count": len(rows),
        "r_values_count": r_count,
        "wins": wins,
        "losses": losses,
        "gross_win_r": round(gross_win_r, 4),
        "gross_loss_abs_r": round(gross_loss_abs_r, 4),
        "net_r": round(net_r, 4),
        "avg_r": round(avg_r, 4),
        "avg_win_r": round(avg_win_r, 4) if avg_win_r is not None else None,
        "avg_loss_r": round(avg_loss_r, 4) if avg_loss_r is not None else None,
        "profit_factor": round(profit_factor, 4) if profit_factor is not None else None,
        "decision": decision,
        "confidence": confidence,
    }


def classify_decision(
    r_values_count: int,
    profit_factor: Optional[float],
    net_r: float,
    gross_win_r: float,
    gross_loss_abs_r: float,
    min_count: int = 10,
) -> str:
    """Classify a segment's decision based on PF stats.

    Rules:
      - r_values_count < min_count: INSUFFICIENT_SAMPLE
      - PF is None and gross_loss_abs_r == 0 and gross_win_r > 0: KEEP_WATCH_NO_LOSSES
      - PF >= 1.25 and net_r > 0: KEEP_OR_EXPAND
      - 0.8 <= PF < 1.25: WATCH
      - PF < 0.8 and net_r < 0: RESTRICT
      - else: REVIEW
    """
    if r_values_count < min_count:
        return "INSUFFICIENT_SAMPLE"

    if profit_factor is None:
        if gross_loss_abs_r == 0 and gross_win_r > 0:
            return "KEEP_WATCH_NO_LOSSES"
        return "REVIEW"

    if profit_factor >= 1.25 and net_r > 0:
        return "KEEP_OR_EXPAND"
    elif 0.8 <= profit_factor < 1.25:
        return "WATCH"
    elif profit_factor < 0.8 and net_r < 0:
        return "RESTRICT"
    else:
        return "REVIEW"


# ---------------------------------------------------------------------------
# Segment-based grouping
# ---------------------------------------------------------------------------


def segment_rows(rows: List[dict], dimension: str) -> Dict[str, List[dict]]:
    """Group rows by a dimension key.

    Supported dimensions:
      ALL, exit_family, symbol, signal_type, market_regime, weekend,
      session_bucket, setup, engine_name, signal_tier
    """
    segments: Dict[str, List[dict]] = {}

    for row in rows:
        if dimension == "ALL":
            key = "ALL"
        elif dimension == "exit_family":
            key = bucket_exit_family(row)
        elif dimension == "symbol":
            key = str(row.get("symbol", "UNKNOWN")).strip().upper() or "UNKNOWN"
        elif dimension == "signal_type":
            key = normalize_side(row.get("signal_type"))
        elif dimension == "market_regime":
            key = str(row.get("market_regime", "UNKNOWN")).strip().upper() or "UNKNOWN"
        elif dimension == "weekend":
            key = "WEEKEND" if compute_weekend_flag(row) else "WEEKDAY"
        elif dimension == "session_bucket":
            key = normalize_session(row.get("created_at"))
        elif dimension == "setup":
            key = str(row.get("setup", "UNKNOWN")).strip().upper() or "UNKNOWN"
        elif dimension == "engine_name":
            key = str(row.get("engine_name", "UNKNOWN")).strip() or "UNKNOWN"
        elif dimension == "signal_tier":
            key = str(row.get("signal_tier", "UNKNOWN")).strip().upper() or "UNKNOWN"
        else:
            key = "UNKNOWN"

        if key not in segments:
            segments[key] = []
        segments[key].append(row)

    return segments


# ---------------------------------------------------------------------------
# No-Progress diagnostics
# ---------------------------------------------------------------------------


def compute_no_progress_stats(rows: List[dict]) -> dict:
    """Compute no-progress specific statistics from a list of rows.

    Returns:
      count, rate_over_sent, avg_net_r, gross_loss_abs_r,
      avg_mfe_r, avg_mae_r, mfe_zero_count, mfe_lt_0_15r_count,
      adverse_first_minutes_count, low_vol_count, btc_conflict_count,
      spread_sensitive_count, entered_too_late_count, top_symbols, action
    """
    total = len(rows)
    if total == 0:
        return {
            "count": 0,
            "rate_over_sent": None,
            "avg_net_r": None,
            "gross_loss_abs_r": 0.0,
            "avg_mfe_r": None,
            "avg_mae_r": None,
            "mfe_zero_count": 0,
            "mfe_lt_0_15r_count": 0,
            "adverse_first_minutes_count": 0,
            "low_vol_count": 0,
            "btc_conflict_count": 0,
            "spread_sensitive_count": 0,
            "entered_too_late_count": 0,
            "top_symbols": [],
            "action": "INSUFFICIENT_SAMPLE",
        }

    # R values
    r_values = extract_r_values(rows)
    net_r = sum(r_values) if r_values else 0.0
    gross_loss_abs_r = abs(sum(v for v in r_values if v < 0)) if r_values else 0.0
    avg_net_r = (sum(r_values) / len(r_values)) if r_values else None

    # MFE/MAE from metrics_json
    mfe_values: List[float] = []
    mae_values: List[float] = []
    mfe_zero = 0
    mfe_lt_0_15r = 0
    adverse_first = 0
    low_vol = 0
    btc_conflict = 0
    spread_sensitive = 0
    entered_too_late = 0

    symbol_counts: Dict[str, int] = {}

    for row in rows:
        metrics = parse_json_safe(row.get("metrics_json"))

        # MFE
        mfe_val = metrics.get("mfe")
        if mfe_val is None:
            mfe_val = row.get("mfe")
        mfe = safe_float(mfe_val)
        if mfe is not None and math.isfinite(mfe):
            mfe_values.append(mfe)
            if mfe <= 0:
                mfe_zero += 1
            elif mfe < 0.15:
                mfe_lt_0_15r += 1

        # MAE
        mae = safe_float(metrics.get("mae") or row.get("mae"))
        if mae is not None and math.isfinite(mae):
            mae_values.append(mae)

        # Adverse first minutes
        adverse = metrics.get("adverse_first_minutes")
        if adverse is not None:
            try:
                if float(adverse) > 0:
                    adverse_first += 1
            except (ValueError, TypeError):
                pass

        # Low vol
        rvol = safe_float(row.get("rvol") or metrics.get("rvol"))
        if rvol is not None and rvol < 1.0:
            low_vol += 1

        # BTC conflict: LONG when BTC is bearish, or SHORT when BTC is bullish
        btc_trend = str(row.get("btc_trend", "")).strip().lower()
        signal_type = normalize_side(row.get("signal_type"))
        if btc_trend and signal_type != "UNKNOWN":
            if (btc_trend == "bearish" and signal_type == "LONG") or \
               (btc_trend == "bullish" and signal_type == "SHORT"):
                btc_conflict += 1



        # Spread sensitive
        spread = safe_float(row.get("spread_pct"))
        if spread is not None and spread > 0.05:
            spread_sensitive += 1

        # Entered too late (time_to_entry > 5 min)
        time_to_entry = safe_float(metrics.get("time_to_entry_minutes"))
        if time_to_entry is not None and time_to_entry > 5:
            entered_too_late += 1

        # Symbol count
        sym = str(row.get("symbol", "")).strip().upper()
        if sym:
            symbol_counts[sym] = symbol_counts.get(sym, 0) + 1

    # Top symbols
    top_symbols = sorted(symbol_counts.items(), key=lambda x: -x[1])[:5]
    top_symbols_list = [{"symbol": s, "count": c} for s, c in top_symbols]

    avg_mfe_r = (sum(mfe_values) / len(mfe_values)) if mfe_values else None
    avg_mae_r = (sum(mae_values) / len(mae_values)) if mae_values else None

    # Determine action
    action = _classify_no_progress_action(
        total=total,
        mfe_zero=mfe_zero,
        mfe_lt_0_15r=mfe_lt_0_15r,
        low_vol=low_vol,
        btc_conflict=btc_conflict,
        spread_sensitive=spread_sensitive,
        entered_too_late=entered_too_late,
    )

    return {
        "count": total,
        "rate_over_sent": None,  # caller must provide sent_to_telegram_count
        "avg_net_r": round(avg_net_r, 4) if avg_net_r is not None else None,
        "gross_loss_abs_r": round(gross_loss_abs_r, 4),
        "avg_mfe_r": round(avg_mfe_r, 4) if avg_mfe_r is not None else None,
        "avg_mae_r": round(avg_mae_r, 4) if avg_mae_r is not None else None,
        "mfe_zero_count": mfe_zero,
        "mfe_lt_0_15r_count": mfe_lt_0_15r,
        "adverse_first_minutes_count": adverse_first,
        "low_vol_count": low_vol,
        "btc_conflict_count": btc_conflict,
        "spread_sensitive_count": spread_sensitive,
        "entered_too_late_count": entered_too_late,
        "top_symbols": top_symbols_list,
        "action": action,
    }


def _classify_no_progress_action(
    total: int,
    mfe_zero: int,
    mfe_lt_0_15r: int,
    low_vol: int,
    btc_conflict: int,
    spread_sensitive: int,
    entered_too_late: int,
) -> str:
    """Classify the dominant action for a no-progress segment."""
    if total < 5:
        return "INSUFFICIENT_SAMPLE"

    mfe_zero_rate = mfe_zero / max(1, total)
    mfe_low_rate = mfe_lt_0_15r / max(1, total)
    low_vol_rate = low_vol / max(1, total)
    btc_conflict_rate = btc_conflict / max(1, total)
    spread_rate = spread_sensitive / max(1, total)
    late_rate = entered_too_late / max(1, total)

    if mfe_zero_rate > 0.5:
        return "MFE_ZERO_HIGH -> BLOCK_PRE_ENTRY"
    if mfe_low_rate > 0.6:
        return "MFE_LOW_HIGH -> REQUIRE_EXPANSION_CONFIRMATION"
    if low_vol_rate > 0.4:
        return "LOW_VOL_NO_EXPANSION -> REQUIRE_EXPANSION_CONFIRMATION"
    if btc_conflict_rate > 0.3:
        return "BTC_CONFLICT_CLUSTER -> APPLY_BTC_CONFLICT_PENALTY"
    if spread_rate > 0.3:
        return "SPREAD_SENSITIVE -> REQUIRE_SPREAD_FILTER"
    if late_rate > 0.3:
        return "ENTERED_TOO_LATE -> ENTRY_TIMING_REVIEW"

    return "WATCH_ONLY"


# ---------------------------------------------------------------------------
# Managed exit damage
# ---------------------------------------------------------------------------


def compute_managed_exit_stats(rows: List[dict]) -> dict:
    """Compute managed exit damage statistics.

    Returns PF, avg_r, net_r, avg_mfe_before_exit, avg_mae_before_exit,
    avg_duration_min, managed_direct_ratio, decision.
    """
    total = len(rows)
    if total == 0:
        return {
            "count": 0,
            "r_values_count": 0,
            "avg_r": None,
            "net_r": 0.0,
            "gross_win_r": 0.0,
            "gross_loss_abs_r": 0.0,
            "profit_factor": None,
            "avg_mfe_before_exit": None,
            "avg_mae_before_exit": None,
            "avg_duration_min": None,
            "managed_direct_ratio": None,
            "decision": "INSUFFICIENT_SAMPLE",
        }

    pf_stats = compute_profit_factor_stats(rows, min_count=5)

    # MFE/MAE from metrics_json
    mfe_values: List[float] = []
    mae_values: List[float] = []
    duration_values: List[float] = []

    for row in rows:
        metrics = parse_json_safe(row.get("metrics_json"))
        mfe = safe_float(metrics.get("mfe") or row.get("mfe"))
        if mfe is not None and math.isfinite(mfe):
            mfe_values.append(mfe)
        mae = safe_float(metrics.get("mae") or row.get("mae"))
        if mae is not None and math.isfinite(mae):
            mae_values.append(mae)

        # Duration from metrics or computed from timestamps
        duration = safe_float(metrics.get("duration_minutes"))
        if duration is None:
            opened = row.get("opened_at")
            closed = row.get("closed_at")
            if opened and closed:
                try:
                    if isinstance(opened, str):
                        opened_dt = datetime.fromisoformat(opened.replace("Z", "+00:00"))
                    else:
                        opened_dt = opened
                    if isinstance(closed, str):
                        closed_dt = datetime.fromisoformat(closed.replace("Z", "+00:00"))
                    else:
                        closed_dt = closed
                    if hasattr(opened_dt, "timestamp") and hasattr(closed_dt, "timestamp"):
                        duration = (closed_dt - opened_dt).total_seconds() / 60.0
                except (ValueError, TypeError, AttributeError):
                    pass
        if duration is not None and math.isfinite(duration):
            duration_values.append(duration)

    avg_mfe = (sum(mfe_values) / len(mfe_values)) if mfe_values else None
    avg_mae = (sum(mae_values) / len(mae_values)) if mae_values else None
    avg_duration = (sum(duration_values) / len(duration_values)) if duration_values else None

    # Decision based on PF
    decision = _classify_managed_exit_decision(pf_stats)

    return {
        "count": total,
        "r_values_count": pf_stats["r_values_count"],
        "avg_r": pf_stats["avg_r"],
        "net_r": pf_stats["net_r"],
        "gross_win_r": pf_stats["gross_win_r"],
        "gross_loss_abs_r": pf_stats["gross_loss_abs_r"],
        "profit_factor": pf_stats["profit_factor"],
        "avg_mfe_before_exit": round(avg_mfe, 4) if avg_mfe is not None else None,
        "avg_mae_before_exit": round(avg_mae, 4) if avg_mae is not None else None,
        "avg_duration_min": round(avg_duration, 1) if avg_duration is not None else None,
        "managed_direct_ratio": None,  # caller must provide
        "decision": decision,
    }


def _classify_managed_exit_decision(pf_stats: dict) -> str:
    """Classify decision for a managed exit family."""
    pf = pf_stats.get("profit_factor")
    net_r = pf_stats.get("net_r", 0.0)
    r_count = pf_stats.get("r_values_count", 0)

    if r_count < 5:
        return "INSUFFICIENT_SAMPLE"
    if pf is None:
        if net_r > 0:
            return "KEEP"
        return "REVIEW"
    if pf >= 1.0 and net_r > 0:
        return "KEEP"
    elif pf >= 0.5 and net_r > 0:
        return "KEEP_CONTEXTUAL"
    elif pf < 0.5 and net_r < 0:
        return "REVIEW_CAPTURE_RULE"
    else:
        return "REVIEW"


# ---------------------------------------------------------------------------
# Filter contribution helpers
# ---------------------------------------------------------------------------


def compute_filter_contribution(
    blocked_rows: List[dict],
    evaluable_rows: List[dict],
) -> dict:
    """Compute filter contribution metrics.

    blocked_rows: candidates blocked by this filter (no geometry).
    evaluable_rows: candidates that passed this filter with evaluable geometry.

    Returns:
      blocked_count, evaluable_count, evaluable_rate,
      hypothetical_wins, hypothetical_losses, time_stop_count,
      avoided_loss_r, missed_win_r, net_filter_value_r,
      profit_factor_if_allowed, decision, confidence
    """
    blocked_count = len(blocked_rows)
    evaluable_count = len(evaluable_rows)
    total = blocked_count + evaluable_count
    evaluable_rate = evaluable_count / max(1, total)

    # Hypothetical outcomes from evaluable rows
    hypothetical_wins = 0
    hypothetical_losses = 0
    time_stop_count = 0
    hypothetical_r_values: List[float] = []

    for row in evaluable_rows:
        # Use net_r if available (from signal_records)
        r = safe_float(row.get("net_r") or row.get("gross_r") or row.get("pnl_r"))
        if r is not None and math.isfinite(r):
            hypothetical_r_values.append(r)
            if r > 0:
                hypothetical_wins += 1
            elif r < 0:
                hypothetical_losses += 1

        # Check for time_stop exit
        exit_family = bucket_exit_family(row)
        if exit_family == "time_stop":
            time_stop_count += 1

    # For candidate rows without R, try hypothetical_result from metadata_json
    for row in evaluable_rows:
        if safe_float(row.get("net_r")) is None:
            meta = parse_json_safe(row.get("metadata_json"))
            hypo_result = meta.get("hypothetical_result")
            if hypo_result:
                hr = str(hypo_result).strip().lower()
                if hr in ("win", "tp"):
                    hypothetical_wins += 1
                    hypothetical_r_values.append(1.0)  # assume +1R
                elif hr in ("loss", "sl"):
                    hypothetical_losses += 1
                    hypothetical_r_values.append(-1.0)  # assume -1R

    # Compute filter value
    gross_win = sum(v for v in hypothetical_r_values if v > 0)
    gross_loss = abs(sum(v for v in hypothetical_r_values if v < 0))
    net_filter_value_r = gross_win - gross_loss

    # Avoided loss: what would have been lost if filter didn't block
    avoided_loss_r = 0.0
    missed_win_r = 0.0
    for row in blocked_rows:
        meta = parse_json_safe(row.get("metadata_json"))
        hypo_result = meta.get("hypothetical_result")
        if hypo_result:
            hr = str(hypo_result).strip().lower()
            # Try to get more precise R from metadata
            hypo_r = safe_float(meta.get("net_rr") or meta.get("gross_rr"))
            if hr in ("loss", "sl"):
                if hypo_r is not None:
                    avoided_loss_r += abs(hypo_r)
                else:
                    avoided_loss_r += 1.0  # assume -1R avoided
            elif hr in ("win", "tp"):
                if hypo_r is not None:
                    missed_win_r += hypo_r
                else:
                    missed_win_r += 1.0  # assume +1R missed

    # PF if allowed
    if gross_loss > 0:
        pf_if_allowed = gross_win / gross_loss
    elif gross_win > 0:
        pf_if_allowed = None  # no losses
    else:
        pf_if_allowed = None

    # Decision
    decision = _classify_filter_decision(
        net_filter_value_r=net_filter_value_r,
        pf_if_allowed=pf_if_allowed,
        evaluable_count=evaluable_count,
        evaluable_rate=evaluable_rate,
    )

    # Confidence
    if evaluable_count < 5:
        confidence = "VERY_LOW"
    elif evaluable_count < 15:
        confidence = "LOW"
    elif evaluable_count < 50:
        confidence = "MEDIUM"
    else:
        confidence = "HIGH"

    return {
        "blocked_count": blocked_count,
        "evaluable_count": evaluable_count,
        "evaluable_rate": round(evaluable_rate, 4),
        "hypothetical_wins": hypothetical_wins,
        "hypothetical_losses": hypothetical_losses,
        "time_stop_count": time_stop_count,
        "avoided_loss_r": round(avoided_loss_r, 4),
        "missed_win_r": round(missed_win_r, 4),
        "net_filter_value_r": round(net_filter_value_r, 4),
        "profit_factor_if_allowed": round(pf_if_allowed, 4) if pf_if_allowed is not None else None,
        "decision": decision,
        "confidence": confidence,
    }


def _classify_filter_decision(
    net_filter_value_r: float,
    pf_if_allowed: Optional[float],
    evaluable_count: int,
    evaluable_rate: float,
) -> str:
    """Classify decision for a filter based on contribution metrics.

    Rules:
      - evaluable_count < 5: INSUFFICIENT_SAMPLE
      - evaluable_rate < 0.3: NEEDS_GEOMETRY
      - net_filter_value_r > 0 and pf_if_allowed is not None and pf_if_allowed < 0.8: KEEP
      - net_filter_value_r < 0 and pf_if_allowed is not None and pf_if_allowed > 1.2: RELAX
      - net_filter_value_r < -2.0: REMOVE_CANDIDATE
      - else: CONTEXTUAL
    """
    if evaluable_count < 5:
        return "INSUFFICIENT_SAMPLE"
    if evaluable_rate < 0.3:
        return "NEEDS_GEOMETRY"
    if net_filter_value_r > 0 and pf_if_allowed is not None and pf_if_allowed < 0.8:
        return "KEEP"
    if net_filter_value_r < 0 and pf_if_allowed is not None and pf_if_allowed > 1.2:
        return "RELAX"
    if net_filter_value_r < -2.0:
        return "REMOVE_CANDIDATE"
    return "CONTEXTUAL"


# ---------------------------------------------------------------------------
# Symbol calibration
# ---------------------------------------------------------------------------


def compute_symbol_calibration(rows: List[dict], symbol: str) -> dict:
    """Compute calibration stats for a single symbol.

    Returns:
      symbol, count, directional_count, managed_count, tp_count, sl_count,
      no_progress_count, gross_win_r, gross_loss_abs_r, net_r,
      profit_factor, avg_mfe_r, avg_mae_r,
      weekday_pf, weekend_pf, managed_direct_ratio, decision
    """
    total = len(rows)
    if total == 0:
        return {
            "symbol": symbol,
            "count": 0,
            "directional_count": 0,
            "managed_count": 0,
            "tp_count": 0,
            "sl_count": 0,
            "no_progress_count": 0,
            "gross_win_r": 0.0,
            "gross_loss_abs_r": 0.0,
            "net_r": 0.0,
            "profit_factor": None,
            "avg_mfe_r": None,
            "avg_mae_r": None,
            "weekday_pf": None,
            "weekend_pf": None,
            "managed_direct_ratio": None,
            "decision": "INSUFFICIENT_SAMPLE",
        }

    # Split by exit family
    directional = [r for r in rows if is_directional_exit(bucket_exit_family(r))]
    managed = [r for r in rows if is_managed_exit(bucket_exit_family(r))]
    tp_rows = [r for r in rows if bucket_exit_family(r) == "take_profit"]
    sl_rows = [r for r in rows if bucket_exit_family(r) == "stop_loss"]
    np_rows = [r for r in rows if bucket_exit_family(r) == "no_progress"]

    # Overall PF
    pf_stats = compute_profit_factor_stats(rows, min_count=5)

    # Weekend split
    weekday_rows = [r for r in rows if not compute_weekend_flag(r)]
    weekend_rows = [r for r in rows if compute_weekend_flag(r)]
    weekday_pf_stats = compute_profit_factor_stats(weekday_rows, min_count=3)
    weekend_pf_stats = compute_profit_factor_stats(weekend_rows, min_count=3)

    # MFE/MAE
    mfe_values: List[float] = []
    mae_values: List[float] = []
    for row in rows:
        metrics = parse_json_safe(row.get("metrics_json"))
        mfe = safe_float(metrics.get("mfe") or row.get("mfe"))
        if mfe is not None and math.isfinite(mfe):
            mfe_values.append(mfe)
        mae = safe_float(metrics.get("mae") or row.get("mae"))
        if mae is not None and math.isfinite(mae):
            mae_values.append(mae)

    avg_mfe = (sum(mfe_values) / len(mfe_values)) if mfe_values else None
    avg_mae = (sum(mae_values) / len(mae_values)) if mae_values else None

    # Managed/direct ratio
    managed_direct_ratio = len(managed) / max(1, len(directional))

    # Decision
    decision = _classify_symbol_decision(
        pf=pf_stats.get("profit_factor"),
        net_r=pf_stats.get("net_r", 0.0),
        r_count=pf_stats.get("r_values_count", 0),
        no_progress_count=len(np_rows),
        sl_count=len(sl_rows),
        total=total,
    )

    return {
        "symbol": symbol,
        "count": total,
        "directional_count": len(directional),
        "managed_count": len(managed),
        "tp_count": len(tp_rows),
        "sl_count": len(sl_rows),
        "no_progress_count": len(np_rows),
        "gross_win_r": pf_stats["gross_win_r"],
        "gross_loss_abs_r": pf_stats["gross_loss_abs_r"],
        "net_r": pf_stats["net_r"],
        "profit_factor": pf_stats["profit_factor"],
        "avg_mfe_r": round(avg_mfe, 4) if avg_mfe is not None else None,
        "avg_mae_r": round(avg_mae, 4) if avg_mae is not None else None,
        "weekday_pf": weekday_pf_stats.get("profit_factor"),
        "weekend_pf": weekend_pf_stats.get("profit_factor"),
        "managed_direct_ratio": round(managed_direct_ratio, 4),
        "decision": decision,
    }


def _classify_symbol_decision(
    pf: Optional[float],
    net_r: float,
    r_count: int,
    no_progress_count: int,
    sl_count: int,
    total: int,
) -> str:
    """Classify symbol tier decision."""
    if r_count < 5:
        return "INSUFFICIENT_SAMPLE"
    if pf is None:
        if net_r > 0:
            return "WATCH_POSITIVE"
        return "WATCH"
    if pf >= 1.25 and net_r > 0:
        return "ALLOW"
    elif 0.8 <= pf < 1.25:
        return "ALLOW_WITH_CONTEXT"
    elif pf < 0.4 and r_count >= 10:
        return "BLOCK_TEMPORARY"
    elif pf < 0.8 and (no_progress_count / max(1, total)) > 0.3:
        return "RESTRICT"
    elif pf < 0.8:
        return "RESTRICT"
    else:
        return "WATCH"


# ---------------------------------------------------------------------------
# Candidate promotion
# ---------------------------------------------------------------------------


def classify_candidate_class(row: dict) -> str:
    """Classify a candidate row into a candidate class based on reason and metadata.

    Classes:
      sweep_only, sweep_plus_reclaim, reclaim_blocked,
      absorption_confirmed, delta_confirmed, stacked_imbalance_confirmed,
      vwap_aligned, volume_profile_level, unknown_or_no_geometry
    """
    reason = str(row.get("reason", "")).strip().lower()
    meta = parse_json_safe(row.get("metadata_json"))

    # Check metadata first for explicit class
    candidate_class = meta.get("candidate_class", "")
    if candidate_class:
        return str(candidate_class).strip().lower()

    # Classify by reason
    if "sweep" in reason and "reclaim" in reason:
        return "sweep_plus_reclaim"
    if "sweep" in reason:
        return "sweep_only"
    if "reclaim" in reason:
        return "reclaim_blocked"
    if "absorption" in reason:
        return "absorption_confirmed"
    if "delta" in reason:
        return "delta_confirmed"
    if "stacked_imbalance" in reason or "imbalance" in reason:
        return "stacked_imbalance_confirmed"
    if "vwap" in reason:
        return "vwap_aligned"
    if "volume_profile" in reason or "liquidity" in reason:
        return "volume_profile_level"

    # Check metadata for OFA features
    ofa_features = meta.get("ofa_features", {})
    if isinstance(ofa_features, dict):
        if ofa_features.get("absorption"):
            return "absorption_confirmed"
        if ofa_features.get("delta_divergence"):
            return "delta_confirmed"
        if ofa_features.get("stacked_imbalance"):
            return "stacked_imbalance_confirmed"
        if ofa_features.get("vwap_aligned"):
            return "vwap_aligned"

    return "unknown_or_no_geometry"


def compute_candidate_promotion(rows: List[dict]) -> dict:
    """Compute promotion stats for a group of candidate rows.

    Returns:
      candidate_class, count, evaluable_count, avg_mfe_r, avg_mae_r,
      hypothetical_wins, hypothetical_losses, hypothetical_pf,
      net_r, promotion_decision, required_confirmations
    """
    total = len(rows)
    if total == 0:
        return {
            "candidate_class": "unknown",
            "count": 0,
            "evaluable_count": 0,
            "avg_mfe_r": None,
            "avg_mae_r": None,
            "hypothetical_wins": 0,
            "hypothetical_losses": 0,
            "hypothetical_pf": None,
            "net_r": 0.0,
            "promotion_decision": "INSUFFICIENT_SAMPLE",
            "required_confirmations": 0,
        }

    # Separate evaluable (have geometry) from non-evaluable
    evaluable = []
    non_evaluable = []
    for row in rows:
        meta = parse_json_safe(row.get("metadata_json"))
        has_geometry = bool(meta.get("geometry") or meta.get("entry_price") or meta.get("has_geometry"))
        if has_geometry:
            evaluable.append(row)
        else:
            non_evaluable.append(row)

    evaluable_count = len(evaluable)

    # Hypothetical outcomes from evaluable rows
    hypothetical_wins = 0
    hypothetical_losses = 0
    hypothetical_r_values: List[float] = []
    mfe_values: List[float] = []
    mae_values: List[float] = []

    for row in evaluable:
        meta = parse_json_safe(row.get("metadata_json"))

        # Try to get R from metadata
        r = safe_float(meta.get("net_rr") or meta.get("gross_rr") or row.get("net_r"))
        if r is not None and math.isfinite(r):
            hypothetical_r_values.append(r)
            if r > 0:
                hypothetical_wins += 1
            elif r < 0:
                hypothetical_losses += 1

        # MFE/MAE from metadata
        mfe = safe_float(meta.get("mfe") or meta.get("max_mfe"))
        if mfe is not None and math.isfinite(mfe):
            mfe_values.append(mfe)
        mae = safe_float(meta.get("mae") or meta.get("max_mae"))
        if mae is not None and math.isfinite(mae):
            mae_values.append(mae)

        # Fallback: hypothetical_result
        if safe_float(meta.get("net_rr")) is None:
            hypo_result = meta.get("hypothetical_result")
            if hypo_result:
                hr = str(hypo_result).strip().lower()
                if hr in ("win", "tp"):
                    hypothetical_wins += 1
                    hypothetical_r_values.append(1.0)
                elif hr in ("loss", "sl"):
                    hypothetical_losses += 1
                    hypothetical_r_values.append(-1.0)

    gross_win = sum(v for v in hypothetical_r_values if v > 0)
    gross_loss = abs(sum(v for v in hypothetical_r_values if v < 0))
    net_r = gross_win - gross_loss

    if gross_loss > 0:
        hypothetical_pf = gross_win / gross_loss
    elif gross_win > 0:
        hypothetical_pf = None
    else:
        hypothetical_pf = None

    avg_mfe = (sum(mfe_values) / len(mfe_values)) if mfe_values else None
    avg_mae = (sum(mae_values) / len(mae_values)) if mae_values else None

    # Promotion decision
    promotion_decision, required_confirmations = _classify_promotion_decision(
        evaluable_count=evaluable_count,
        hypothetical_pf=hypothetical_pf,
        net_r=net_r,
        total=total,
    )

    return {
        "candidate_class": "computed",
        "count": total,
        "evaluable_count": evaluable_count,
        "avg_mfe_r": round(avg_mfe, 4) if avg_mfe is not None else None,
        "avg_mae_r": round(avg_mae, 4) if avg_mae is not None else None,
        "hypothetical_wins": hypothetical_wins,
        "hypothetical_losses": hypothetical_losses,
        "hypothetical_pf": round(hypothetical_pf, 4) if hypothetical_pf is not None else None,
        "net_r": round(net_r, 4),
        "promotion_decision": promotion_decision,
        "required_confirmations": required_confirmations,
    }


def _classify_promotion_decision(
    evaluable_count: int,
    hypothetical_pf: Optional[float],
    net_r: float,
    total: int,
) -> Tuple[str, int]:
    """Classify promotion decision for a candidate class.

    Returns (decision, required_confirmations).
    """
    if evaluable_count < 3:
        return "INSUFFICIENT_SAMPLE", 0
    if evaluable_count < 5:
        return "WATCH_ONLY", 0

    if hypothetical_pf is None:
        if net_r > 0:
            return "WATCH_POSITIVE", 3
        return "WATCH_ONLY", 0

    if hypothetical_pf >= 1.5 and net_r > 0:
        return "PROMOTE", 2
    elif hypothetical_pf >= 1.0 and net_r > 0:
        return "WATCH_POSITIVE", 3
    elif hypothetical_pf < 0.5 and net_r < 0:
        return "RESTRICT", 0
    else:
        return "WATCH_ONLY", 0


# ---------------------------------------------------------------------------
# JSON serialization helpers
# ---------------------------------------------------------------------------


def compact_json_serialize(data: Any, max_chars: int = 100000) -> str:
    """Serialize data to compact JSON, ensuring it doesn't exceed max_chars."""
    encoded = json.dumps(data, indent=2, default=str, ensure_ascii=False)
    if len(encoded) > max_chars:
        # Truncate at the last complete object boundary
        encoded = encoded[:max_chars]
        # Find last complete line
        last_newline = encoded.rfind("\n")
        if last_newline > 0:
            encoded = encoded[:last_newline]
        encoded += '\n  "//TRUNCATED": true\n}'
    return encoded


def write_compact_json(data: Any, filepath: str, max_chars: int = 100000) -> str:
    """Write compact JSON to file, returning the filepath."""
    import os
    content = compact_json_serialize(data, max_chars)
    os.makedirs(os.path.dirname(filepath) or ".", exist_ok=True)
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(content)
    return filepath
