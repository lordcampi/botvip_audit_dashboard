from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

F5_T09A_LIFECYCLE_RECONCILIATION_FILENAME = "19_telegram_lifecycle_reconciliation_v2.json"
F5_T09A_SCHEMA_VERSION = "f5_t09a_telegram_lifecycle_reconciliation_v2"


def _text(value: Any) -> str:
    return str(value or "").strip()


def _lower(value: Any) -> str:
    return _text(value).lower()


def _upper(value: Any) -> str:
    return _text(value).upper()


def _bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "y", "win", "won"}


def _float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except Exception:
        return None


def _event_index(events: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = defaultdict(lambda: {
        "event_types": [],
        "event_count": 0,
        "primary_tp_hit": False,
        "runner_tp_hit": False,
        "runner_breakeven_stop_hit": False,
        "breakeven_stop_hit": False,
        "time_stop_exit": False,
        "stop_loss_hit": False,
        "no_progress_exit": False,
        "mfe_stall_exit": False,
        "sl_moved_to_breakeven": False,
        "breakeven_armed": False,
    })
    for event in events:
        sid = _text(event.get("signal_id"))
        if not sid:
            continue
        etype = _upper(event.get("event_type"))
        item = grouped[sid]
        item["event_count"] += 1
        if etype:
            item["event_types"].append(etype)
        if etype == "PRIMARY_TP_HIT":
            item["primary_tp_hit"] = True
        elif etype == "RUNNER_TP_HIT":
            item["runner_tp_hit"] = True
        elif etype == "RUNNER_BREAKEVEN_STOP_HIT":
            item["runner_breakeven_stop_hit"] = True
        elif etype == "BREAKEVEN_STOP_HIT":
            item["breakeven_stop_hit"] = True
        elif etype == "TIME_STOP_EXIT":
            item["time_stop_exit"] = True
        elif etype == "STOP_LOSS_HIT":
            item["stop_loss_hit"] = True
        elif etype == "NO_PROGRESS_EXIT":
            item["no_progress_exit"] = True
        elif etype == "MFE_STALL_EXIT":
            item["mfe_stall_exit"] = True
        elif etype == "SL_MOVED_TO_BREAKEVEN":
            item["sl_moved_to_breakeven"] = True
        elif etype == "BREAKEVEN_ARMED":
            item["breakeven_armed"] = True
    for item in grouped.values():
        item["event_types"] = sorted(set(item["event_types"]))
    return dict(grouped)


def _official_result(row: dict[str, Any], ev: dict[str, Any]) -> str:
    status = _upper(row.get("status"))
    official = _upper(row.get("official_result"))
    primary_tp = _bool(row.get("primary_tp_hit")) or bool(ev.get("primary_tp_hit"))
    real_sl = _bool(row.get("real_stop_loss_hit")) or bool(ev.get("stop_loss_hit"))
    locked = _bool(row.get("official_result_locked"))
    if primary_tp or official == "WIN" or status == "WON":
        return "WIN_PROTECTED" if locked or primary_tp else "WIN_UNVERIFIED_LOCK"
    if real_sl or status == "LOST":
        return "LOSS_OFFICIAL"
    if status in {"EXPIRED", "CANCELLED", "CANCELED"}:
        return "CANCELLED_OR_EXPIRED"
    if _lower(row.get("exit_reason")):
        return "MANAGED_EXIT_NO_OFFICIAL_TP"
    return "OPEN_OR_PENDING"


def _runner_result(row: dict[str, Any], ev: dict[str, Any], official_result: str) -> str:
    exit_reason = _lower(row.get("exit_reason"))
    net_r = _float(row.get("net_r"))
    pnl_r = _float(row.get("pnl_r"))
    effective_r = net_r if net_r is not None else pnl_r

    if _bool(row.get("runner_tp_hit")) or bool(ev.get("runner_tp_hit")) or exit_reason == "runner_tp_hit":
        return "RUNNER_TP"
    if _bool(row.get("runner_breakeven_stop_hit")) or bool(ev.get("runner_breakeven_stop_hit")) or exit_reason == "runner_breakeven_stop":
        return "RUNNER_BREAKEVEN_STOP"
    if _bool(row.get("breakeven_stop_hit")) or bool(ev.get("breakeven_stop_hit")) or exit_reason == "breakeven_stop":
        return "BREAKEVEN_STOP"
    if _bool(row.get("time_stop_exit")) or bool(ev.get("time_stop_exit")) or exit_reason == "time_stop":
        if official_result == "WIN_PROTECTED" and effective_r is not None and effective_r < 0:
            return "RUNNER_TIME_STOP_NEGATIVE_VISUAL_RISK"
        return "TIME_STOP"
    if _bool(row.get("mfe_stall_exit")) or bool(ev.get("mfe_stall_exit")) or exit_reason == "mfe_stall":
        return "MFE_STALL"
    if _bool(row.get("no_progress_exit")) or bool(ev.get("no_progress_exit")) or exit_reason == "no_progress":
        return "NO_PROGRESS"
    if exit_reason == "stop_loss" and official_result == "WIN_PROTECTED":
        return "STOP_LOSS_AFTER_OFFICIAL_WIN_VISUAL_RISK"
    if exit_reason:
        return exit_reason.upper()
    return "NONE"


def _visual_contradiction(row: dict[str, Any], official_result: str, runner_result: str) -> bool:
    if official_result != "WIN_PROTECTED":
        return False
    exit_reason = _lower(row.get("exit_reason"))
    status = _upper(row.get("status"))
    net_r = _float(row.get("net_r"))
    pnl_r = _float(row.get("pnl_r"))
    effective_r = net_r if net_r is not None else pnl_r
    risky_runner = runner_result in {
        "RUNNER_TIME_STOP_NEGATIVE_VISUAL_RISK",
        "STOP_LOSS_AFTER_OFFICIAL_WIN_VISUAL_RISK",
        "NO_PROGRESS",
        "MFE_STALL",
    }
    risky_exit = exit_reason in {"stop_loss", "time_stop", "no_progress", "mfe_stall"}
    negative_after_win = effective_r is not None and effective_r < 0
    lost_label_after_win = status == "LOST"
    return bool(risky_runner or risky_exit or negative_after_win or lost_label_after_win)


def build_telegram_lifecycle_reconciliation_v2(
    *,
    facts: list[dict[str, Any]],
    events: list[dict[str, Any]],
    signals: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build reporting-only reconciliation of official WIN vs runner closure."""

    ev_index = _event_index(events)
    signal_rows = [row for row in facts if row.get("record_type") == "signal"]
    rows: list[dict[str, Any]] = []
    official_counts: Counter[str] = Counter()
    runner_counts: Counter[str] = Counter()
    final_counts: Counter[str] = Counter()
    contradictions = 0

    for row in signal_rows:
        sid = _text(row.get("signal_id"))
        ev = ev_index.get(sid, {})
        official = _official_result(row, ev)
        runner = _runner_result(row, ev, official)
        final_public = "WIN_PROTECTED" if official == "WIN_PROTECTED" else official
        contradiction = _visual_contradiction(row, official, runner)
        official_counts[official] += 1
        runner_counts[runner] += 1
        final_counts[final_public] += 1
        contradictions += int(contradiction)

        if official == "WIN_PROTECTED" or contradiction or runner != "NONE":
            rows.append({
                "signal_id": sid,
                "symbol": row.get("symbol"),
                "side": row.get("side"),
                "setup_type": row.get("setup_type"),
                "created_at": row.get("created_at"),
                "opened_at": row.get("opened_at"),
                "closed_at": row.get("closed_at"),
                "status": row.get("status"),
                "exit_reason": row.get("exit_reason"),
                "official_result": official,
                "runner_result": runner,
                "final_public_result": final_public,
                "visual_contradiction": contradiction,
                "primary_tp_hit": _bool(row.get("primary_tp_hit")) or bool(ev.get("primary_tp_hit")),
                "official_result_locked": _bool(row.get("official_result_locked")),
                "runner_tp_hit": _bool(row.get("runner_tp_hit")) or bool(ev.get("runner_tp_hit")),
                "runner_breakeven_stop_hit": _bool(row.get("runner_breakeven_stop_hit")) or bool(ev.get("runner_breakeven_stop_hit")),
                "breakeven_stop_hit": _bool(row.get("breakeven_stop_hit")) or bool(ev.get("breakeven_stop_hit")),
                "time_stop_exit": _bool(row.get("time_stop_exit")) or bool(ev.get("time_stop_exit")),
                "real_stop_loss_hit": _bool(row.get("real_stop_loss_hit")) or bool(ev.get("stop_loss_hit")),
                "net_r": _float(row.get("net_r")),
                "pnl_r": _float(row.get("pnl_r")),
                "mfe": _float(row.get("mfe")),
                "mae": _float(row.get("mae")),
                "event_count": row.get("event_count") or ev.get("event_count"),
                "event_types": row.get("event_types") or ",".join(ev.get("event_types", [])),
                "interpretation_note": (
                    "Official WIN remains protected; runner closure must not be counted as official loss."
                    if official == "WIN_PROTECTED" and runner != "NONE"
                    else "No official WIN/runner contradiction detected."
                ),
            })

    rows.sort(key=lambda item: (not bool(item.get("visual_contradiction")), str(item.get("created_at") or ""), str(item.get("signal_id") or "")))
    max_rows = 300
    return {
        "schema_version": F5_T09A_SCHEMA_VERSION,
        "section": "telegram_lifecycle_reconciliation_v2",
        "read_only": True,
        "mode": "shadow_observational_only",
        "purpose": "Separate official WIN protected outcome from later runner closure for AI review and public-message consistency.",
        "guardrails": [
            "PRIMARY_TP_HIT is the official protected WIN.",
            "Runner, TP2, breakeven or time-stop events cannot invalidate an official WIN.",
            "This section is reporting-only and does not change runtime lifecycle or strategy.",
        ],
        "summary": {
            "signals_total": len(signal_rows),
            "rows_emitted": min(len(rows), max_rows),
            "rows_available": len(rows),
            "official_result_counts": dict(official_counts.most_common()),
            "runner_result_counts": dict(runner_counts.most_common()),
            "final_public_result_counts": dict(final_counts.most_common()),
            "visual_contradiction_count": contradictions,
            "truncated": len(rows) > max_rows,
        },
        "rows": rows[:max_rows],
    }
