from __future__ import annotations

"""
swing_reminder_scheduler.py — R5C SWING reminder scheduler CLI.

Wires: R4 history → R1 read-only evidence → R5A engine/state → R5B sender.

Usage:
    python -m src.swing_reminder_scheduler
    python src/swing_reminder_scheduler.py

Modes (via SWING_TELEGRAM_MODE):
    off      — exit immediately, no PG, no state mutation, no Telegram
    shadow   — evaluate candidate, update last_shadow_* only, never send
    enabled  — evaluate + send, update last_sent_* only on success

Never imports legacy Telegram, SQLite, requests, or BotVIP.
"""

import json
import os
import sys
from datetime import datetime, timezone
from typing import Any, Optional

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
DEFAULT_DASHBOARD_URL = "http://127.0.0.1:18501/Swing_Strategy_Review"
MAX_REMINDER_MESSAGE_CHARS = 500
MIN_REMINDER_MESSAGE_CHARS = 20
EXIT_OK = 0
EXIT_ERROR = 1
EXIT_OFF = 0  # off mode is not an error


# ---------------------------------------------------------------------------
# Environment
# ---------------------------------------------------------------------------
def _env_mode() -> str:
    """Read SWING_TELEGRAM_MODE. Defaults to 'off'."""
    val = os.environ.get("SWING_TELEGRAM_MODE", "off").strip().lower()
    if val in ("enabled", "shadow", "off"):
        return val
    return "off"


def _dashboard_url() -> str:
    """Return the dashboard URL for reminder messages."""
    return os.environ.get("SWING_DASHBOARD_URL", DEFAULT_DASHBOARD_URL).strip()


# ---------------------------------------------------------------------------
# Message builder
# ---------------------------------------------------------------------------
def _build_reminder_message(
    reminder_type: str,
    new_signal_count: int,
    new_closed_count: int,
    last_review_id: Optional[str],
    readiness: Optional[str],
    dashboard_url: str,
) -> str:
    """Build a short, safe reminder message (<500 chars).

    Contains: type, last review, counts, readiness, dashboard URL.
    No individual signals, no secrets, no IP.
    CALIB: never authorizes CONTROL.
    """
    type_labels = {
        "TECH": "🔍 Revisión técnica",
        "STRAT": "📊 Revisión estratégica",
        "CALIB": "⚙️ Revisión de calibración",
    }
    label = type_labels.get(reminder_type, "Revisión SWING")

    lines = [label]
    lines.append(f"Señales nuevas: {new_signal_count}  |  Cerradas nuevas: {new_closed_count}")

    if last_review_id:
        lines.append(f"Última revisión: {last_review_id}")

    if readiness and readiness != "UNKNOWN":
        lines.append(f"Readiness: {readiness}")

    if reminder_type == "CALIB":
        lines.append("⚠️ Solo revisar — no modificar CONTROL")

    lines.append(f"\nDashboard → {dashboard_url}")
    lines.append("Recordatorio automático R5 — no responde")

    msg = "\n".join(lines)

    # Enforce length limit
    if len(msg) > MAX_REMINDER_MESSAGE_CHARS:
        # Truncate with ellipsis, keeping URL intact
        truncated = msg[: MAX_REMINDER_MESSAGE_CHARS - 20] + "…\n" + f"Dashboard → {dashboard_url}"
        return truncated

    return msg


# ---------------------------------------------------------------------------
# Sanitised output builder
# ---------------------------------------------------------------------------
def _build_output(
    mode: str,
    evaluated: bool,
    reminder_type: Optional[str],
    sent: bool,
    shadow_recorded: bool,
    reason: str,
    new_signal_count: int,
    new_closed_count: int,
    evidence_cutoff_source: Optional[str],
    error_class: Optional[str],
    retryable: bool,
    sender_result: Optional[dict],
    engine_result: Optional[dict],
) -> dict:
    """Build a structured, sanitised output dict.

    Never includes: token, chat_id, individual signal data, full message content
    in logs (message is only sent via Telegram, not printed).
    """
    output: dict[str, Any] = {
        "mode": mode,
        "evaluated": evaluated,
        "candidate": engine_result.get("should_remind") if engine_result else False,
        "reminder_type": reminder_type,
        "sent": sent,
        "shadow": shadow_recorded,
        "reason": reason,
        "new_signal_count": new_signal_count,
        "new_closed_count": new_closed_count,
        "evidence_cutoff_source": evidence_cutoff_source,
        "error_class": error_class,
        "retryable": retryable,
    }
    return output


# ---------------------------------------------------------------------------
# Main scheduler
# ---------------------------------------------------------------------------
def run(exit_on_complete: bool = True) -> int:
    """Execute one scheduler cycle.

    Parameters
    ----------
    exit_on_complete:
        If True, calls sys.exit with the exit code. Set to False in tests.

    Returns
    -------
    int
        Exit code: 0 for success/shadow/off, 1 for operational failure.
    """
    mode = _env_mode()
    dashboard_url = _dashboard_url()

    # --- Mode: off ---
    if mode == "off":
        result = _build_output(
            mode="off",
            evaluated=False,
            reminder_type=None,
            sent=False,
            shadow_recorded=False,
            reason="SWING_TELEGRAM_MODE is off — no evaluation or sending",
            new_signal_count=0,
            new_closed_count=0,
            evidence_cutoff_source=None,
            error_class=None,
            retryable=False,
            sender_result=None,
            engine_result=None,
        )
        _print_result(result)
        code = EXIT_OFF
        if exit_on_complete:
            sys.exit(code)
        return code

    # --- Load dependencies ---
    try:
        from .postgres_readonly import build_readonly_conn
        from .swing_review_history import ReviewHistoryManager
        from .swing_reminder_state import SwingReminderState
        from .swing_reminder_engine import evaluate_reminder
    except ImportError as e:
        result = _build_output(
            mode=mode,
            evaluated=False,
            reminder_type=None,
            sent=False,
            shadow_recorded=False,
            reason=f"Import error: {e}",
            new_signal_count=0,
            new_closed_count=0,
            evidence_cutoff_source=None,
            error_class="OPERATIONAL_ERROR",
            retryable=False,
            sender_result=None,
            engine_result=None,
        )
        _print_result(result)
        code = EXIT_ERROR
        if exit_on_complete:
            sys.exit(code)
        return code

    # --- Initialise R4 / R5 state ---
    history_mgr = ReviewHistoryManager()
    reminder_state = SwingReminderState()

    # Check for corrupt state (fail-closed)
    if os.path.isfile(reminder_state._state_path) and not reminder_state.is_valid():
        result = _build_output(
            mode=mode,
            evaluated=False,
            reminder_type=None,
            sent=False,
            shadow_recorded=False,
            reason="Reminder state file is corrupt — blocked for safety",
            new_signal_count=0,
            new_closed_count=0,
            evidence_cutoff_source=None,
            error_class="STATE_CORRUPT",
            retryable=False,
            sender_result=None,
            engine_result=None,
        )
        _print_result(result)
        code = EXIT_ERROR
        if exit_on_complete:
            sys.exit(code)
        return code

    # --- PostgreSQL connection ---
    conn = None
    try:
        conn = build_readonly_conn()
    except Exception as exc:
        result = _build_output(
            mode=mode,
            evaluated=False,
            reminder_type=None,
            sent=False,
            shadow_recorded=False,
            reason=f"PostgreSQL connection failed: {exc}",
            new_signal_count=0,
            new_closed_count=0,
            evidence_cutoff_source=None,
            error_class="PG_CONNECTION_ERROR",
            retryable=True,
            sender_result=None,
            engine_result=None,
        )
        _print_result(result)
        code = EXIT_ERROR
        if exit_on_complete:
            sys.exit(code)
        return code

    try:
        # --- Evaluate ---
        engine_result = evaluate_reminder(
            conn=conn,
            history_manager=history_mgr,
            reminder_state=reminder_state,
        )

        evaluated = True
        should_remind = engine_result.get("should_remind", False)
        reminder_type = engine_result.get("reminder_type")
        new_signal_count = engine_result.get("new_signal_count", 0)
        new_closed_count = engine_result.get("new_closed_count", 0)
        evidence_cutoff_source = engine_result.get("evidence_cutoff_source")
        engine_reason = engine_result.get("reason", "")

        # --- Shadow mode: record only, never send ---
        if mode == "shadow" or not should_remind:
            sent = False
            shadow_recorded = should_remind  # shadow evaluation was recorded by engine
            error_class = None
            retryable = False
            reason = engine_reason

            if mode == "enabled" and not should_remind:
                reason = engine_reason
            elif mode == "enabled" and should_remind:
                reason = engine_reason  # will be updated below if we attempt send

            result = _build_output(
                mode=mode,
                evaluated=evaluated,
                reminder_type=reminder_type,
                sent=sent,
                shadow_recorded=shadow_recorded,
                reason=reason,
                new_signal_count=new_signal_count,
                new_closed_count=new_closed_count,
                evidence_cutoff_source=evidence_cutoff_source,
                error_class=error_class,
                retryable=retryable,
                sender_result=None,
                engine_result=engine_result,
            )
            _print_result(result)

            # Ensure state is initialised for clean shutdown
            reminder_state.initialise_if_missing()

            code = EXIT_OK
            if exit_on_complete:
                sys.exit(code)
            return code

        # --- Enabled mode: try to send ---
        if mode == "enabled" and should_remind:
            # Check token/chat_id presence first
            token = os.environ.get("SWING_TELEGRAM_BOT_TOKEN", "").strip()
            chat_id = os.environ.get("SWING_TELEGRAM_CHAT_ID", "").strip()

            if not token or not chat_id:
                result = _build_output(
                    mode=mode,
                    evaluated=evaluated,
                    reminder_type=reminder_type,
                    sent=False,
                    shadow_recorded=False,
                    reason="Missing SWING_TELEGRAM_BOT_TOKEN or SWING_TELEGRAM_CHAT_ID — cannot send",
                    new_signal_count=new_signal_count,
                    new_closed_count=new_closed_count,
                    evidence_cutoff_source=evidence_cutoff_source,
                    error_class="CONFIG_INVALID",
                    retryable=False,
                    sender_result=None,
                    engine_result=engine_result,
                )
                _print_result(result)
                code = EXIT_ERROR
                if exit_on_complete:
                    sys.exit(code)
                return code

            # Build the message
            # Extract readiness from engine result
            readiness = None
            reviews = history_mgr.list_reviews()
            if reviews:
                readiness = reviews[0].get("readiness_decision")

            message = _build_reminder_message(
                reminder_type=reminder_type,
                new_signal_count=new_signal_count,
                new_closed_count=new_closed_count,
                last_review_id=engine_result.get("last_review_id"),
                readiness=readiness,
                dashboard_url=dashboard_url,
            )

            # Validate message length
            if len(message) < MIN_REMINDER_MESSAGE_CHARS:
                result = _build_output(
                    mode=mode,
                    evaluated=evaluated,
                    reminder_type=reminder_type,
                    sent=False,
                    shadow_recorded=False,
                    reason="Generated message too short — blocked",
                    new_signal_count=new_signal_count,
                    new_closed_count=new_closed_count,
                    evidence_cutoff_source=evidence_cutoff_source,
                    error_class="MESSAGE_INVALID",
                    retryable=False,
                    sender_result=None,
                    engine_result=engine_result,
                )
                _print_result(result)
                code = EXIT_ERROR
                if exit_on_complete:
                    sys.exit(code)
                return code

            # --- Send via R5B sender ---
            try:
                from .swing_telegram_sender import SwingTelegramSender

                sender = SwingTelegramSender(token=token, chat_id=chat_id)
                sender_result = sender.send_message(message)
            except Exception as exc:
                reminder_state.record_telegram_error(str(exc)[:500])
                result = _build_output(
                    mode=mode,
                    evaluated=evaluated,
                    reminder_type=reminder_type,
                    sent=False,
                    shadow_recorded=False,
                    reason=f"Sender construction failed: {exc}",
                    new_signal_count=new_signal_count,
                    new_closed_count=new_closed_count,
                    evidence_cutoff_source=evidence_cutoff_source,
                    error_class="SENDER_ERROR",
                    retryable=False,
                    sender_result=None,
                    engine_result=engine_result,
                )
                _print_result(result)
                code = EXIT_ERROR
                if exit_on_complete:
                    sys.exit(code)
                return code

            success = sender_result.get("success", False)
            error_class = sender_result.get("error_class")
            retryable = sender_result.get("retryable", False)

            if success:
                # Update last_sent_* in state
                from .swing_reminder_state import VALID_REMINDER_TYPES

                now_iso = datetime.now(timezone.utc).isoformat()
                import hashlib
                sent_hash = hashlib.sha256(message.encode("utf-8")).hexdigest()

                if reminder_type in VALID_REMINDER_TYPES:
                    reminder_state.update_reminder_entry(
                        reminder_type,
                        {
                            "last_sent_at_utc": now_iso,
                            "last_sent_message_hash": sent_hash,
                        },
                    )
                reminder_state.reset_telegram_error_counter()

                reason = f"Sent {reminder_type} reminder successfully"
                sent = True
                shadow_recorded = False
                code = EXIT_OK
            else:
                # Record error but do NOT update last_sent_*
                reminder_state.record_telegram_error(
                    sender_result.get("sanitized_reason", "Unknown Telegram error")
                )
                reason = f"Send failed: {sender_result.get('sanitized_reason', 'Unknown')}"
                sent = False
                shadow_recorded = False
                code = EXIT_ERROR

            result = _build_output(
                mode=mode,
                evaluated=evaluated,
                reminder_type=reminder_type,
                sent=sent,
                shadow_recorded=shadow_recorded,
                reason=reason,
                new_signal_count=new_signal_count,
                new_closed_count=new_closed_count,
                evidence_cutoff_source=evidence_cutoff_source,
                error_class=error_class,
                retryable=retryable,
                sender_result=sender_result if success else None,
                engine_result=engine_result,
            )
            _print_result(result)

            if exit_on_complete:
                sys.exit(code)
            return code

    except Exception as exc:
        # Catch-all for unexpected operational failures
        result = _build_output(
            mode=mode,
            evaluated=False,
            reminder_type=None,
            sent=False,
            shadow_recorded=False,
            reason=f"Unexpected error: {exc}",
            new_signal_count=0,
            new_closed_count=0,
            evidence_cutoff_source=None,
            error_class="OPERATIONAL_ERROR",
            retryable=True,
            sender_result=None,
            engine_result=None,
        )
        _print_result(result)
        code = EXIT_ERROR
        if exit_on_complete:
            sys.exit(code)
        return code

    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def _print_result(result: dict) -> None:
    """Print the result as JSON to stdout."""
    print(json.dumps(result, ensure_ascii=False, default=str))


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    run(exit_on_complete=True)