from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any


def _is_true(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


def _num(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except Exception:
        return None


def _avg(values: list[float]) -> float | None:
    if not values:
        return None
    return round(sum(values) / len(values), 6)


def signal_facts_only(facts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [row for row in facts if row.get("record_type") == "signal"]


def candidate_facts_only(facts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [row for row in facts if row.get("record_type") == "candidate"]


def compute_lifecycle_metrics(facts: list[dict[str, Any]]) -> dict[str, Any]:
    signals = signal_facts_only(facts)
    candidates = candidate_facts_only(facts)
    status_counts = Counter(str(row.get("status")) for row in signals)
    exit_reason_counts = Counter(str(row.get("exit_reason")) for row in signals)
    blocked_reason_counts = Counter(str(row.get("blocked_reason")) for row in candidates)

    sent = sum(1 for row in signals if _is_true(row.get("sent_to_telegram")))
    primary_tp = sum(1 for row in signals if _is_true(row.get("primary_tp_hit")))
    real_sl = sum(1 for row in signals if _is_true(row.get("real_stop_loss_hit")))
    breakeven = sum(1 for row in signals if _is_true(row.get("breakeven_stop_hit")))
    runner_be = sum(1 for row in signals if _is_true(row.get("runner_breakeven_stop_hit")))
    time_stop = sum(1 for row in signals if _is_true(row.get("time_stop_exit")))
    cancelled = sum(1 for row in signals if _is_true(row.get("cancelled")))
    no_progress = sum(1 for row in signals if _is_true(row.get("no_progress_exit")))
    mfe_stall = sum(1 for row in signals if _is_true(row.get("mfe_stall_exit")))
    data_gap_events = sum(int(_num(row.get("data_gap_events")) or 0) for row in signals)

    net_rs = [_num(row.get("net_r")) for row in signals]
    net_rs = [x for x in net_rs if x is not None]
    times_to_entry = [_num(row.get("time_to_entry_minutes")) for row in signals]
    times_to_entry = [x for x in times_to_entry if x is not None]
    times_to_close = [_num(row.get("time_to_close_minutes")) for row in signals]
    times_to_close = [x for x in times_to_close if x is not None]
    times_to_tp = [_num(row.get("time_to_primary_tp_minutes")) for row in signals]
    times_to_tp = [x for x in times_to_tp if x is not None]

    official_win_rate = round(primary_tp / max(1, primary_tp + real_sl), 6)
    sent_win_rate = round(primary_tp / max(1, sent), 6)

    return {
        "signals_total": len(signals),
        "candidates_total": len(candidates),
        "sent_to_telegram": sent,
        "not_sent_or_shadow_only": max(0, len(signals) - sent),
        "primary_tp_hit": primary_tp,
        "real_stop_loss_hit": real_sl,
        "breakeven_stop_hit": breakeven,
        "runner_breakeven_stop_hit": runner_be,
        "time_stop_exit": time_stop,
        "cancelled_or_expired": cancelled,
        "no_progress_exit": no_progress,
        "mfe_stall_exit": mfe_stall,
        "data_gap_events": data_gap_events,
        "official_win_rate_tp_vs_sl": official_win_rate,
        "sent_win_rate_primary_tp_over_sent": sent_win_rate,
        "avg_net_r": _avg(net_rs),
        "avg_time_to_entry_minutes": _avg(times_to_entry),
        "avg_time_to_primary_tp_minutes": _avg(times_to_tp),
        "avg_time_to_close_minutes": _avg(times_to_close),
        "status_counts": dict(status_counts.most_common()),
        "exit_reason_counts": dict(exit_reason_counts.most_common()),
        "top_blocked_reasons": dict(blocked_reason_counts.most_common(30)),
        "near_miss_candidates": sum(1 for row in candidates if _is_true(row.get("near_miss"))),
        "would_send_candidates": sum(1 for row in candidates if _is_true(row.get("would_send_signal"))),
        "telegram_notified_note": "telegram_notified field is not reliable in this window; sent_to_telegram is the source of truth for delivery metrics.",
        "telegram_notified_status": "legacy_or_unreliable",
        "primary_denominator": "sent_to_telegram",
    }


def compute_group_metrics(facts: list[dict[str, Any]], group_key: str, record_type: str = "signal") -> list[dict[str, Any]]:
    rows = [row for row in facts if row.get("record_type") == record_type]
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        buckets[str(row.get(group_key))].append(row)

    out: list[dict[str, Any]] = []
    for group, items in buckets.items():
        primary_tp = sum(1 for row in items if _is_true(row.get("primary_tp_hit")))
        real_sl = sum(1 for row in items if _is_true(row.get("real_stop_loss_hit")))
        sent = sum(1 for row in items if _is_true(row.get("sent_to_telegram")))
        out.append({
            "group_key": group_key,
            "group": group,
            "count": len(items),
            "sent": sent,
            "primary_tp_hit": primary_tp,
            "real_stop_loss_hit": real_sl,
            "tp_vs_sl_win_rate": round(primary_tp / max(1, primary_tp + real_sl), 6),
        })
    out.sort(key=lambda x: x["count"], reverse=True)
    return out
