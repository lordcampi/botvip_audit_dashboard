from __future__ import annotations

"""
swing_reminder_engine.py — R5A SWING reminder decision engine.

Evaluates whether a reminder should be produced based on:
  - R4 ReviewHistoryManager (last review metadata)
  - R1 PostgreSQL read-only (current signal evidence)
  - R5 SwingReminderState (cooldown/dedup state)

No Telegram, no tokens, no writes to PostgreSQL, no CONTROL changes.
"""

import hashlib
import json
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

# ---------------------------------------------------------------------------
# Public constants
# ---------------------------------------------------------------------------
STRATEGY_SCOPE = "SWING_TREND_RECLAIM_V1"

# Thresholds
TECH_MIN_DAYS = 3
TECH_MIN_NEW_SIGNALS = 1

STRAT_MIN_DAYS = 7
STRAT_MIN_NEW_CLOSED = 5

CALIB_MIN_DAYS = 14
CALIB_MIN_NEW_CLOSED = 30
CALIB_READINESS_NOT_INSUFFICIENT = True  # readiness must not be DATA_INSUFFICIENT

# Priority order (highest first)
REMINDER_PRIORITY = ("CALIB", "STRAT", "TECH")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _env_mode() -> str:
    """Read SWING_TELEGRAM_MODE. Defaults to 'off'."""
    val = os.environ.get("SWING_TELEGRAM_MODE", "off").strip().lower()
    if val in ("enabled", "shadow", "off"):
        return val
    return "off"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _compute_candidate_hash(
    reminder_type: str,
    new_signal_count: int,
    new_closed_count: int,
    last_review_id: Optional[str],
    fingerprint: Optional[str],
) -> str:
    """Compute a deterministic SHA-256 hash for the reminder candidate.

    Used for dedup: if the same hash was already evaluated, skip.
    Does NOT include evaluated_at_utc — hash must be time-invariant for dedup.
    """
    payload = {
        "reminder_type": reminder_type,
        "new_signal_count": new_signal_count,
        "new_closed_count": new_closed_count,
        "last_review_id": last_review_id,
        "fingerprint": fingerprint,
    }
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _count_new_signals_pg(
    conn,
    since_utc: datetime,
) -> tuple[int, int]:
    """Count new SWING signals since *since_utc* from PostgreSQL.

    Uses load_signal_records_pg filtered by SWING strategy and time window.

    Returns (total_new_signals, new_closed_signals).
    """
    from .swing_loaders import load_signal_records_pg

    now = _utc_now()
    # Convert since_utc to Colombia time for the loader
    colombia_offset = timedelta(hours=-5)
    window_start = since_utc + colombia_offset
    window_end = now + colombia_offset

    df = load_signal_records_pg(conn, window_start=window_start, window_end=window_end)

    if df is None or df.empty:
        return 0, 0

    # Filter by strategy scope
    if "strategy" in df.columns:
        df = df[df["strategy"].astype(str).str.upper() == STRATEGY_SCOPE.replace("-", "_").upper()]

    total = len(df)

    # Count closed
    closed = 0
    if "status" in df.columns:
        status = df["status"].astype(str).str.lower()
        closed = int(status.isin(["closed", "won", "lost"]).sum())

    return total, closed


def _count_signals_all_time_pg(conn) -> tuple[int, int]:
    """Count ALL SWING signals (no time filter) for initial state.

    Returns (total_signals, total_closed).
    """
    from .swing_loaders import load_signal_records_pg

    df = load_signal_records_pg(conn, window_start=None, window_end=None, limit=50000)

    if df is None or df.empty:
        return 0, 0

    if "strategy" in df.columns:
        df = df[df["strategy"].astype(str).str.upper() == STRATEGY_SCOPE.replace("-", "_").upper()]

    total = len(df)
    closed = 0
    if "status" in df.columns:
        status = df["status"].astype(str).str.lower()
        closed = int(status.isin(["closed", "won", "lost"]).sum())

    return total, closed


def _build_candidate_message(
    reminder_type: str,
    new_signal_count: int,
    new_closed_count: int,
    last_review_id: Optional[str],
    last_review_at: Optional[str],
) -> str:
    """Build the candidate message text (for logging / shadow output)."""
    type_label = {"TECH": "TECH", "STRAT": "STRAT", "CALIB": "CALIB"}.get(reminder_type, reminder_type)
    return (
        f"[{type_label}] SWING reminder candidate — "
        f"nuevas señales: {new_signal_count}, "
        f"nuevas cerradas: {new_closed_count}, "
        f"última revisión: {last_review_id or 'ninguna'} "
        f"({last_review_at or 'N/A'})"
    )


def _extract_readiness_from_reviews(reviews: list[dict]) -> Optional[str]:
    """Extract readiness_decision from the most recent review, if any."""
    if not reviews:
        return None
    # reviews are sorted newest-first
    for entry in reviews:
        rd = entry.get("readiness_decision")
        if rd and rd != "UNKNOWN":
            return str(rd).upper()
    return None


def _parse_r4_datetime(val: Any) -> Optional[datetime]:
    """Parse an ISO-8601 string from R4 index to a timezone-aware datetime."""
    if val is None:
        return None
    if isinstance(val, datetime):
        return val
    try:
        dt = datetime.fromisoformat(str(val))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------
def evaluate_reminder(
    conn,  # psycopg2 read-only connection
    history_manager,  # ReviewHistoryManager from R4
    reminder_state,  # SwingReminderState from R5
) -> dict:
    """Evaluate whether a SWING review reminder should be produced.

    Parameters
    ----------
    conn:
        An active psycopg2 read-only connection (R1).
    history_manager:
        An instance of ReviewHistoryManager (R4), used to read last review.
    reminder_state:
        An instance of SwingReminderState (R5), used for cooldown/dedup.

    Returns
    -------
    dict with keys:
        should_remind: bool
        reminder_type: str | None  (TECH / STRAT / CALIB)
        mode: str                   (off / shadow / enabled)
        reason: str
        new_signal_count: int
        new_closed_count: int
        last_review_id: str | None
        current_fingerprint: str | None
        candidate_hash: str | None
        candidate_message: str | None
        evaluated_at_utc: str
        control_change_allowed: bool  (always False)
    """
    original_mode = _env_mode()
    mode = original_mode
    now = _utc_now()
    now_iso = now.isoformat()

    # Base result (no reminder)
    result: dict[str, Any] = {
        "should_remind": False,
        "reminder_type": None,
        "mode": original_mode,
        "reason": "",
        "new_signal_count": 0,
        "new_closed_count": 0,
        "last_review_id": None,
        "current_fingerprint": None,
        "candidate_hash": None,
        "candidate_message": None,
        "evaluated_at_utc": now_iso,
        "control_change_allowed": False,
        "evidence_cutoff_utc": None,
        "evidence_cutoff_source": None,
    }

    # --- mode off → exit immediately ---
    if mode == "off":
        result["reason"] = "SWING_TELEGRAM_MODE is off — no evaluation performed"
        return result

    # --- mode enabled (not yet supported in R5A) — evaluate but don't send ---
    if mode == "enabled":
        # Evaluate for shadow-like behavior, but mark the reason
        mode = "shadow"
        enabled_note = "SWING_TELEGRAM_MODE=enabled is not supported in R5A — no send will occur. "
    else:
        enabled_note = ""

    # --- Get last review from R4 history ---
    reviews = history_manager.list_reviews()

    last_review_id: Optional[str] = None
    last_review_at: Optional[datetime] = None

    # --- Determine evidence cutoff ---
    # The last review covered signals up to window_end_utc (inclusive in
    # half-open terms).  Signals created at or after window_end are new.
    # Fallback: if window_end_utc is missing from the R4 index, use
    # generated_at_utc explicitly — never silently continue with a wrong
    # cutoff.
    evidence_cutoff: Optional[datetime] = None
    evidence_cutoff_source: Optional[str] = None

    if reviews:
        latest = reviews[0]  # sorted newest-first
        last_review_id = latest.get("review_id")
        last_review_at = _parse_r4_datetime(latest.get("generated_at_utc"))

        # Primary: window_end_utc (half-open: signals at or after this are new)
        wend = _parse_r4_datetime(latest.get("window_end_utc"))
        if wend is not None:
            evidence_cutoff = wend
            evidence_cutoff_source = "window_end_utc"
            last_review_at = wend  # use window_end for the cutoff, not generated_at
        elif last_review_at is not None:
            # Fallback explicit (generated_at_utc — less precise but available)
            evidence_cutoff = last_review_at
            evidence_cutoff_source = "generated_at_utc"

    result["evidence_cutoff_utc"] = evidence_cutoff.isoformat() if evidence_cutoff else None
    result["evidence_cutoff_source"] = evidence_cutoff_source

    # --- Get current signal evidence from PostgreSQL ---
    try:
        if evidence_cutoff is not None:
            new_total, new_closed = _count_new_signals_pg(conn, evidence_cutoff)
        else:
            # No reviews yet — count all signals as "new"
            new_total, new_closed = _count_signals_all_time_pg(conn)
    except Exception as exc:
        result["reason"] = f"PostgreSQL query failed: {exc}"
        return result

    # --- Get current fingerprint ---
    current_fingerprint = None
    try:
        from .swing_loaders import load_signal_records_pg, extract_fingerprint

        recent_df = load_signal_records_pg(conn, window_start=None, window_end=None, limit=100)
        if recent_df is not None and not recent_df.empty:
            current_fingerprint = extract_fingerprint(recent_df)
    except Exception:
        current_fingerprint = None

    # --- Determine which reminder type applies (priority order) ---
    days_since_review = None
    if evidence_cutoff is not None:
        days_since_review = (now - evidence_cutoff).total_seconds() / 86400.0

    readiness = _extract_readiness_from_reviews(reviews)

    selected_type: Optional[str] = None
    selected_reason = ""

    for rtype in REMINDER_PRIORITY:
        if rtype == "CALIB":
            if days_since_review is None or days_since_review >= CALIB_MIN_DAYS:
                if new_closed >= CALIB_MIN_NEW_CLOSED:
                    if readiness is None or readiness != "DATA_INSUFFICIENT":
                        selected_type = "CALIB"
                        selected_reason = (
                            f"CALIB: ≥{CALIB_MIN_DAYS}d since last review, "
                            f"{new_closed} new closed (≥{CALIB_MIN_NEW_CLOSED}), "
                            f"readiness={readiness or 'UNKNOWN'}"
                        )
                        break
                    else:
                        # Readiness insufficient — fall through to lower priority
                        continue

        elif rtype == "STRAT":
            if days_since_review is None or days_since_review >= STRAT_MIN_DAYS:
                if new_closed >= STRAT_MIN_NEW_CLOSED:
                    selected_type = "STRAT"
                    selected_reason = (
                        f"STRAT: ≥{STRAT_MIN_DAYS}d since last review, "
                        f"{new_closed} new closed (≥{STRAT_MIN_NEW_CLOSED})"
                    )
                    break

        elif rtype == "TECH":
            if days_since_review is None or days_since_review >= TECH_MIN_DAYS:
                if new_total >= TECH_MIN_NEW_SIGNALS:
                    selected_type = "TECH"
                    selected_reason = (
                        f"TECH: ≥{TECH_MIN_DAYS}d since last review, "
                        f"{new_total} new signals (≥{TECH_MIN_NEW_SIGNALS})"
                    )
                    break

    if selected_type is None:
        if days_since_review is None:
            result["reason"] = "No reviews in history — but no threshold met (should not happen with all-time counts)"
        else:
            result["reason"] = (
                f"No reminder type applicable: {days_since_review:.1f}d since last review, "
                f"{new_total} new signals, {new_closed} new closed"
            )
        result["new_signal_count"] = new_total
        result["new_closed_count"] = new_closed
        result["last_review_id"] = last_review_id
        result["current_fingerprint"] = current_fingerprint
        return result

    # --- Compute candidate hash for dedup (time-invariant) ---
    candidate_hash = _compute_candidate_hash(
        reminder_type=selected_type,
        new_signal_count=new_total,
        new_closed_count=new_closed,
        last_review_id=last_review_id,
        fingerprint=current_fingerprint,
    )

    # --- Anti-spam: check against previous shadow candidate hash ---
    prev_entry = reminder_state.get_reminder_entry(selected_type)
    prev_hash = prev_entry.get("last_shadow_candidate_hash")
    prev_count = prev_entry.get("last_known_signal_count", 0)
    prev_closed = prev_entry.get("last_known_closed_count", 0)

    if prev_hash == candidate_hash and prev_count == new_total and prev_closed == new_closed:
        result["reason"] = (
            f"Duplicate candidate ({selected_type}): same hash and counts as previous evaluation. Skipped."
        )
        result["new_signal_count"] = new_total
        result["new_closed_count"] = new_closed
        result["last_review_id"] = last_review_id
        result["current_fingerprint"] = current_fingerprint
        result["candidate_hash"] = candidate_hash
        return result

    # --- Build candidate message ---
    last_review_at_str = last_review_at.isoformat() if last_review_at else None
    candidate_message = _build_candidate_message(
        reminder_type=selected_type,
        new_signal_count=new_total,
        new_closed_count=new_closed,
        last_review_id=last_review_id,
        last_review_at=last_review_at_str,
    )

    # --- Record shadow evaluation in state ---
    reminder_state.record_shadow_evaluation(
        reminder_type=selected_type,
        candidate_hash=candidate_hash,
        signal_count=new_total,
        closed_count=new_closed,
        latest_review_id=last_review_id,
    )
    reminder_state.record_check()

    # --- Build result ---
    result["should_remind"] = True
    result["reminder_type"] = selected_type
    result["reason"] = enabled_note + selected_reason
    result["new_signal_count"] = new_total
    result["new_closed_count"] = new_closed
    result["last_review_id"] = last_review_id
    result["current_fingerprint"] = current_fingerprint
    result["candidate_hash"] = candidate_hash
    result["candidate_message"] = candidate_message

    return result