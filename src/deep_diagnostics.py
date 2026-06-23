from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


def _is_true(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _num(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except Exception:
        return None


def _avg(values: list[float]) -> float | None:
    values = [v for v in values if v is not None]
    if not values:
        return None
    return round(sum(values) / len(values), 6)


def _safe_key(value: Any) -> str:
    if value is None or value == "":
        return "unknown"
    return str(value)


def _counter(rows: list[dict[str, Any]], key: str, limit: int = 20) -> dict[str, int]:
    return dict(Counter(_safe_key(row.get(key)) for row in rows).most_common(limit))


def _has_geometry(row: dict[str, Any]) -> bool:
    return _num(row.get("entry_price")) is not None and _num(row.get("tp_price")) is not None and _num(row.get("sl_price")) is not None


def _has_mfe_mae(row: dict[str, Any]) -> bool:
    return _num(row.get("mfe")) is not None and _num(row.get("mae")) is not None


def _mfe_bucket(mfe: float | None) -> str:
    if mfe is None:
        return "unknown"
    if mfe < 0.25:
        return "mfe_lt_0_25r"
    if mfe < 0.75:
        return "mfe_0_25_to_0_75r"
    if mfe < 1.0:
        return "mfe_0_75_to_1_0r"
    return "mfe_gt_1_0r"


def select_near_miss_rows(facts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    near_reasons = {"near_miss_chop", "ofa_shadow_reclaim_blocked", "ofa_shadow_sweep_blocked", "rvol_low", "atr_extension_high", "adx_low"}
    rows = []
    for row in facts:
        if row.get("record_type") != "candidate":
            continue
        reason = _safe_key(row.get("blocked_reason"))
        if _is_true(row.get("near_miss")) or _is_true(row.get("would_send_signal")) or reason in near_reasons:
            rows.append(row)
    return rows


def compute_near_miss_quality_summary(facts: list[dict[str, Any]]) -> dict[str, Any]:
    rows = select_near_miss_rows(facts)
    with_geometry = [r for r in rows if _has_geometry(r)]
    with_mfe_mae = [r for r in rows if _has_mfe_mae(r)]
    without_geometry = [r for r in rows if not _has_geometry(r)]
    without_mfe_mae = [r for r in rows if not _has_mfe_mae(r)]
    return {
        "near_miss_total_selected": len(rows),
        "with_geometry": len(with_geometry),
        "without_geometry": len(without_geometry),
        "with_mfe_mae": len(with_mfe_mae),
        "without_mfe_mae": len(without_mfe_mae),
        "geometry_rate": round(len(with_geometry) / max(1, len(rows)), 6),
        "mfe_mae_rate": round(len(with_mfe_mae) / max(1, len(rows)), 6),
        "by_blocked_reason": _counter(rows, "blocked_reason", 30),
        "with_geometry_by_reason": _counter(with_geometry, "blocked_reason", 30),
        "without_geometry_by_reason": _counter(without_geometry, "blocked_reason", 30),
    }


def compute_near_miss_outcome_by_reason(facts: list[dict[str, Any]]) -> dict[str, Any]:
    rows = select_near_miss_rows(facts)
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        buckets[_safe_key(row.get("blocked_reason"))].append(row)
    out: dict[str, Any] = {}
    for reason, items in sorted(buckets.items(), key=lambda kv: len(kv[1]), reverse=True):
        with_geometry = [r for r in items if _has_geometry(r)]
        with_mfe_mae = [r for r in items if _has_mfe_mae(r)]
        mfe_values = [_num(r.get("mfe")) for r in items]
        mae_values = [_num(r.get("mae")) for r in items]
        out[reason] = {
            "total": len(items),
            "with_geometry": len(with_geometry),
            "without_geometry": len(items) - len(with_geometry),
            "with_mfe_mae": len(with_mfe_mae),
            "without_mfe_mae": len(items) - len(with_mfe_mae),
            "hypothetical_result_counts": _counter(items, "hypothetical_result", 20),
            "hypothetical_exit_reason_counts": _counter(items, "hypothetical_exit_reason", 20),
            "avg_mfe": _avg([x for x in mfe_values if x is not None]),
            "avg_mae": _avg([x for x in mae_values if x is not None]),
            "mfe_bucket_counts": dict(Counter(_mfe_bucket(_num(r.get("mfe"))) for r in items).most_common()),
            "top_symbols": _counter(items, "symbol", 10),
            "top_sides": _counter(items, "side", 10),
        }
    return out


def compute_no_progress_diagnostics(facts: list[dict[str, Any]], sample_limit: int = 80) -> dict[str, Any]:
    rows = [r for r in facts if r.get("record_type") == "signal" and _is_true(r.get("no_progress_exit"))]
    sample = []
    for row in rows[:sample_limit]:
        sample.append({
            "signal_id": row.get("signal_id"), "symbol": row.get("symbol"), "side": row.get("side"),
            "setup_type": row.get("setup_type"), "market_regime": row.get("market_regime"),
            "score": row.get("score"), "rvol": row.get("rvol"), "mfe": row.get("mfe"), "mae": row.get("mae"),
            "data_gap_events": row.get("data_gap_events"), "time_to_entry_minutes": row.get("time_to_entry_minutes"),
            "time_to_close_minutes": row.get("time_to_close_minutes"), "exit_reason": row.get("exit_reason"), "net_r": row.get("net_r"),
        })
    return {
        "total_no_progress": len(rows),
        "avg_mfe": _avg([_num(r.get("mfe")) for r in rows if _num(r.get("mfe")) is not None]),
        "avg_mae": _avg([_num(r.get("mae")) for r in rows if _num(r.get("mae")) is not None]),
        "avg_data_gap_events": _avg([_num(r.get("data_gap_events")) for r in rows if _num(r.get("data_gap_events")) is not None]),
        "avg_time_to_close_minutes": _avg([_num(r.get("time_to_close_minutes")) for r in rows if _num(r.get("time_to_close_minutes")) is not None]),
        "mfe_bucket_counts": dict(Counter(_mfe_bucket(_num(r.get("mfe"))) for r in rows).most_common()),
        "by_symbol": _counter(rows, "symbol", 20),
        "by_side": _counter(rows, "side", 10),
        "by_setup_type": _counter(rows, "setup_type", 10),
        "by_market_regime": _counter(rows, "market_regime", 10),
        "sample_rows": sample,
    }


def _outcome_label(row: dict[str, Any]) -> str:
    if _is_true(row.get("primary_tp_hit")):
        return "primary_tp_hit"
    if _is_true(row.get("real_stop_loss_hit")):
        return "real_stop_loss_hit"
    if _is_true(row.get("no_progress_exit")):
        return "no_progress_exit"
    if _is_true(row.get("breakeven_stop_hit")):
        return "breakeven_stop_hit"
    if _is_true(row.get("runner_breakeven_stop_hit")):
        return "runner_breakeven_stop_hit"
    if _is_true(row.get("time_stop_exit")):
        return "time_stop_exit"
    if _is_true(row.get("cancelled")):
        return "cancelled_or_expired"
    return "other_or_open"


def compute_data_quality_by_outcome(facts: list[dict[str, Any]]) -> dict[str, Any]:
    rows = [r for r in facts if r.get("record_type") == "signal"]
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        buckets[_outcome_label(row)].append(row)
    out: dict[str, Any] = {}
    for outcome, items in sorted(buckets.items(), key=lambda kv: len(kv[1]), reverse=True):
        out[outcome] = {
            "count": len(items),
            "sent_to_telegram": sum(1 for r in items if _is_true(r.get("sent_to_telegram"))),
            "avg_data_gap_events": _avg([_num(r.get("data_gap_events")) for r in items if _num(r.get("data_gap_events")) is not None]),
            "signals_with_data_gap": sum(1 for r in items if (_num(r.get("data_gap_events")) or 0) > 0),
            "avg_net_r": _avg([_num(r.get("net_r")) for r in items if _num(r.get("net_r")) is not None]),
            "by_setup_type": _counter(items, "setup_type", 10),
            "by_market_regime": _counter(items, "market_regime", 10),
        }
    return out


def compute_deep_diagnostics(facts: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "near_miss_quality_summary": compute_near_miss_quality_summary(facts),
        "near_miss_outcome_by_reason": compute_near_miss_outcome_by_reason(facts),
        "no_progress_diagnostics": compute_no_progress_diagnostics(facts),
        "data_quality_by_outcome": compute_data_quality_by_outcome(facts),
        "interpretation_guardrails": [
            "Use these diagnostics for observation and hypothesis design only.",
            "Do not change thresholds from a single-day sample.",
            "Separate evaluable near-misses from skipped_no_geometry cases.",
            "Treat high data_gap_events as lower confidence evidence.",
        ],
    }


def write_deep_diagnostics(diagnostics: dict[str, Any], path: str | Path) -> None:
    selected = Path(path)
    selected.parent.mkdir(parents=True, exist_ok=True)
    selected.write_text(json.dumps(diagnostics, indent=2, ensure_ascii=False, default=str), encoding="utf-8", newline="\n")
