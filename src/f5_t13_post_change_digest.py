"""F5_T13 — Post-change Strategy Impact Digest.

Compact AI-ready digest that measures the impact of F5_T12_v3 OFA Risk Context
Gate (deployed 2026-07-01 10:00 COL) on post-change data only.

Read-only/dashboard-local analytics. Does NOT modify runtime, strategy,
thresholds, TP/SL, lifecycle, DB schema, Telegram, or any bot operation.

Uses sent_to_telegram as the primary denominator. Strictly separates
official_signals, sent_to_telegram, candidate_snapshots, events, and facts.
"""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from typing import Any

F5_T13_DIGEST_JSON_FILENAME = "30_f5_t13_post_change_strategy_impact_digest.json"
F5_T13_DIGEST_MD_FILENAME = "30_f5_t13_post_change_strategy_impact_digest.md"
F5_T13_SCHEMA_VERSION = "f5_t13_post_change_strategy_impact_digest_v1"
MAX_DIGEST_CHARS = 95_000

# 2026-07-01 10:00 COL = 2026-07-01 15:00 UTC
POST_CHANGE_CUTOFF = "2026-07-01 15:00:00"

GUARDS_PRIORITY = [
    "ofa_live_rvol_too_low",
    "ofa_low_vol_shadow_only",
    "ofa_long_low_vol_shadow_only",
    "ofa_live_regime_blocked",
    "ofa_live_symbol_not_allowed",
    "copyability_rr_degraded",
    "ofa_live_atr_extension_high",
    "risk_context_gate",
]

WATCH_SYMBOLS = {"SUI", "ADA", "SOL", "XRP", "NEAR", "DOGE"}


# ---------------------------------------------------------------------------
# Safe helpers (same pattern as F5_T10 / F5_T12)
# ---------------------------------------------------------------------------


def _safe_get(data: Any, path: list[Any], default: Any = None) -> Any:
    cur = data
    for key in path:
        if isinstance(cur, dict):
            cur = cur.get(key, default)
        elif isinstance(cur, list) and isinstance(key, int) and 0 <= key < len(cur):
            cur = cur[key]
        else:
            return default
    return cur


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _num(value: Any) -> float | None:
    if value is None:
        return None
    try:
        v = float(value)
        import math
        if math.isnan(v) or math.isinf(v):
            return None
        return v
    except (TypeError, ValueError):
        return None


def _is_true(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


def _json_size(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False, default=str))


def _limit(value: Any, *, max_items: int = 8, depth: int = 2) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if depth <= 0:
        return {"_truncated": True, "_type": type(value).__name__, "_size_chars": _json_size(value)}
    if isinstance(value, list):
        return [_limit(item, max_items=max_items, depth=depth - 1) for item in value[:max_items]]
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key in sorted(value.keys())[:max_items]:
            out[str(key)] = _limit(value.get(key), max_items=max_items, depth=depth - 1)
        if len(value) > max_items:
            out["_omitted_keys"] = len(value) - max_items
        return out
    return str(value)


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    s = sorted(values)
    n = len(s)
    if n % 2 == 1:
        return s[n // 2]
    return (s[n // 2 - 1] + s[n // 2]) / 2.0


# ---------------------------------------------------------------------------
# Core metrics for a list of rows (signal facts only)
# ---------------------------------------------------------------------------


def _r_values(rows: list[dict[str, Any]], key: str = "net_r") -> list[float]:
    vals = [_num(r.get(key)) for r in rows]
    return [v for v in vals if v is not None]


def _core_metrics(rows: list[dict[str, Any]], r_key: str = "net_r") -> dict[str, Any]:
    total = len(rows)
    r_vals = _r_values(rows, r_key)
    r_count = len(r_vals)
    wins_list = [v for v in r_vals if v > 0]
    losses_list = [v for v in r_vals if v < 0]
    gross_profit = sum(wins_list)
    gross_loss = abs(sum(losses_list))
    net_r = gross_profit - gross_loss
    avg_r = round(sum(r_vals) / r_count, 6) if r_count else None
    pf = round(gross_profit / gross_loss, 4) if gross_loss > 0 else (None if gross_profit == 0 else None)
    winrate = round(len(wins_list) / r_count, 6) if r_count else None

    return {
        "count": total,
        "r_values_count": r_count,
        "wins": len(wins_list),
        "losses": len(losses_list),
        "breakeven": r_count - len(wins_list) - len(losses_list),
        "gross_profit_r": round(gross_profit, 4),
        "gross_loss_r": round(gross_loss, 4),
        "net_r": round(net_r, 4),
        "profit_factor": pf,
        "avg_r": avg_r,
        "winrate": winrate,
    }


# ---------------------------------------------------------------------------
# Denominators
# ---------------------------------------------------------------------------


def _build_denominators(
    post_signals: list[dict[str, Any]],
    post_candidates: list[dict[str, Any]],
) -> dict[str, Any]:
    sent_to_telegram = sum(1 for r in post_signals if _is_true(r.get("sent_to_telegram")))
    telegram_notified = sum(1 for r in post_signals if _is_true(r.get("telegram_notified")))
    mismatch_note = None
    if sent_to_telegram != telegram_notified:
        mismatch_note = (
            f"WARNING: sent_to_telegram ({sent_to_telegram}) != "
            f"telegram_notified ({telegram_notified}). "
            f"Using sent_to_telegram as primary denominator."
        )

    blocked_by_ofa_base = sum(1 for r in post_candidates if _is_true(r.get("blocked")))
    blocked_by_risk_context = sum(
        1 for r in post_candidates
        if _is_true(r.get("blocked")) and "risk_context_gate" in str(r.get("blocked_reason") or "").lower()
    )
    shadow_only = sum(1 for r in post_candidates if not _is_true(r.get("blocked")))

    return {
        "official_signals": len(post_signals),
        "sent_to_telegram": sent_to_telegram,
        "telegram_notified_raw": telegram_notified,
        "telegram_notified_mismatch_note": mismatch_note,
        "candidate_snapshots_total": len(post_candidates),
        "candidate_snapshots_evaluable": sum(1 for r in post_candidates if r.get("net_r") is not None),
        "candidate_snapshots_non_evaluable": sum(1 for r in post_candidates if r.get("net_r") is None),
        "blocked_by_ofa_base": blocked_by_ofa_base,
        "blocked_by_risk_context_gate": blocked_by_risk_context,
        "shadow_only": shadow_only,
        "denominator_note": (
            "sent_to_telegram is the primary denominator. "
            "candidate_snapshots, events, and facts are NOT trades. "
            "Do not sum blocked/candidate snapshots as trades."
        ),
    }


# ---------------------------------------------------------------------------
# Section builders
# ---------------------------------------------------------------------------


def _build_core_summary(post_signals: list[dict[str, Any]]) -> dict[str, Any]:
    sent_rows = [r for r in post_signals if _is_true(r.get("sent_to_telegram"))]
    closed_rows = [r for r in post_signals if r.get("closed_at") is not None]
    pending_rows = [r for r in post_signals if r.get("closed_at") is None and not _is_true(r.get("cancelled"))]
    expired_rows = [r for r in post_signals if _is_true(r.get("cancelled"))]

    metrics = _core_metrics(sent_rows)
    return {
        "post_change_official_signals": len(post_signals),
        "post_change_sent_to_telegram": len(sent_rows),
        "post_change_closed": len(closed_rows),
        "post_change_pending": len(pending_rows),
        "post_change_expired": len(expired_rows),
        "winners": metrics["wins"],
        "losers": metrics["losses"],
        "breakeven": metrics["breakeven"],
        "gross_profit_r": metrics["gross_profit_r"],
        "gross_loss_r": metrics["gross_loss_r"],
        "net_r": metrics["net_r"],
        "profit_factor": metrics["profit_factor"],
        "avg_r": metrics["avg_r"],
        "winrate": metrics["winrate"],
        "denominator_used": "sent_to_telegram",
    }


def _build_pre_post_comparison(
    pre_signals: list[dict[str, Any]],
    post_signals: list[dict[str, Any]],
) -> dict[str, Any]:
    pre_sent = [r for r in pre_signals if _is_true(r.get("sent_to_telegram"))]
    post_sent = [r for r in post_signals if _is_true(r.get("sent_to_telegram"))]

    pre_metrics = _core_metrics(pre_sent)
    post_metrics = _core_metrics(post_sent)

    return {
        "pre_change": {
            "sent_to_telegram": len(pre_sent),
            "profit_factor": pre_metrics["profit_factor"],
            "net_r": pre_metrics["net_r"],
            "avg_r": pre_metrics["avg_r"],
            "winrate": pre_metrics["winrate"],
            "available": len(pre_sent) > 0,
        },
        "post_change": {
            "sent_to_telegram": len(post_sent),
            "profit_factor": post_metrics["profit_factor"],
            "net_r": post_metrics["net_r"],
            "avg_r": post_metrics["avg_r"],
            "winrate": post_metrics["winrate"],
            "available": len(post_sent) > 0,
        },
        "note": "Pre-change data only available if the report window spans before 2026-07-01 15:00 UTC. 'available'=false means no pre-change data in this window.",
    }


def _build_symbols(post_signals: list[dict[str, Any]]) -> dict[str, Any]:
    sent = [r for r in post_signals if _is_true(r.get("sent_to_telegram"))]
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in sent:
        sym = str(r.get("symbol") or "UNKNOWN").strip().upper()
        buckets[sym].append(r)

    symbols_table: list[dict[str, Any]] = []
    for sym, rows in buckets.items():
        m = _core_metrics(rows)
        symbols_table.append({
            "symbol": sym,
            "count": m["count"],
            "sent_count": m["count"],
            "wins": m["wins"],
            "losses": m["losses"],
            "net_r": m["net_r"],
            "avg_r": m["avg_r"],
            "profit_factor": m["profit_factor"],
            "gross_profit_r": m["gross_profit_r"],
            "gross_loss_r": m["gross_loss_r"],
            "watch": sym in WATCH_SYMBOLS,
        })

    symbols_table.sort(key=lambda x: x["net_r"] or 0)

    return {
        "by_symbol": symbols_table,
        "top10_worst": symbols_table[:10],
        "top10_best": list(reversed(symbols_table[-10:])),
        "watched_symbols_present": [s for s in symbols_table if s["watch"]],
    }


def _build_direction(post_signals: list[dict[str, Any]]) -> dict[str, Any]:
    sent = [r for r in post_signals if _is_true(r.get("sent_to_telegram"))]
    longs = [r for r in sent if str(r.get("side") or "").strip().upper() in {"LONG", "BUY", "CALL"}]
    shorts = [r for r in sent if str(r.get("side") or "").strip().upper() in {"SHORT", "SELL", "PUT"}]

    return {
        "LONG": _core_metrics(longs),
        "SHORT": _core_metrics(shorts),
    }


def _build_regime(post_signals: list[dict[str, Any]]) -> dict[str, Any]:
    sent = [r for r in post_signals if _is_true(r.get("sent_to_telegram"))]
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in sent:
        regime = str(r.get("market_regime") or "UNKNOWN").strip().upper()
        buckets[regime].append(r)

    return {regime: _core_metrics(rows) for regime, rows in sorted(buckets.items())}


def _build_killzone(post_signals: list[dict[str, Any]]) -> dict[str, Any]:
    sent = [r for r in post_signals if _is_true(r.get("sent_to_telegram"))]
    in_kz = [r for r in sent if _is_true(r.get("killzone"))]
    out_kz = [r for r in sent if not _is_true(r.get("killzone")) and r.get("killzone") is not None]
    unknown_kz = [r for r in sent if r.get("killzone") is None]

    return {
        "KILLZONE": _core_metrics(in_kz),
        "OUT_OF_KILLZONE": _core_metrics(out_kz),
        "UNKNOWN": _core_metrics(unknown_kz),
    }


def _build_exit_reasons(post_signals: list[dict[str, Any]]) -> dict[str, Any]:
    sent = [r for r in post_signals if _is_true(r.get("sent_to_telegram"))]
    closed = [r for r in sent if r.get("closed_at") is not None]
    pending_active = [r for r in sent if r.get("closed_at") is None and not _is_true(r.get("cancelled"))]
    expired = [r for r in sent if _is_true(r.get("cancelled"))]

    reason_map = {
        "primary_tp_hit": "primary_tp_hit",
        "real_stop_loss_hit": "real_stop_loss_hit",
        "no_progress_exit": "no_progress_exit",
        "mfe_stall_exit": "mfe_stall_exit",
        "time_stop_exit": "time_stop_exit",
        "breakeven_stop_hit": "breakeven_stop",
        "runner_breakeven_stop_hit": "runner_breakeven_stop",
    }

    result: dict[str, Any] = {}
    for reason_key, label in reason_map.items():
        rows = [r for r in closed if _is_true(r.get(reason_key))]
        if not rows and reason_key not in {"no_progress_exit", "mfe_stall_exit"}:
            result[label] = {"count": 0, "available": False}
            continue
        m = _core_metrics(rows)
        result[label] = {
            "count": m["count"],
            "wins": m["wins"],
            "losses": m["losses"],
            "gross_profit_r": m["gross_profit_r"],
            "gross_loss_r": m["gross_loss_r"],
            "net_r": m["net_r"],
            "avg_r": m["avg_r"],
            "available": True,
        }

    result["pending_active"] = {"count": len(pending_active), "note": "Signals still active/pending, no outcome yet."}
    result["expired"] = {"count": len(expired), "note": "Cancelled/expired signals."}

    return result


def _build_mfe_mae(post_signals: list[dict[str, Any]]) -> dict[str, Any]:
    sent = [r for r in post_signals if _is_true(r.get("sent_to_telegram"))]
    closed = [r for r in sent if r.get("closed_at") is not None]

    mfe_vals = [_num(r.get("mfe")) for r in closed]
    mfe_vals = [v for v in mfe_vals if v is not None]
    mae_vals = [_num(r.get("mae")) for r in closed]
    mae_vals = [v for v in mae_vals if v is not None]

    if not mfe_vals and not mae_vals:
        return {"mfe_mae_status": "unavailable_or_partial", "note": "No MFE/MAE data available for post-change closed signals."}

    # Group by outcome type
    def _outcome_key(r: dict[str, Any]) -> str:
        if _is_true(r.get("primary_tp_hit")):
            return "winners"
        if _is_true(r.get("real_stop_loss_hit")):
            return "real_stop_loss"
        if _is_true(r.get("no_progress_exit")):
            return "no_progress"
        if _is_true(r.get("mfe_stall_exit")):
            return "mfe_stall"
        if _is_true(r.get("time_stop_exit")):
            return "time_stop"
        return "other"

    outcome_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in closed:
        outcome_groups[_outcome_key(r)].append(r)

    by_outcome: dict[str, Any] = {}
    for outcome, rows in sorted(outcome_groups.items()):
        mfe_g = [_num(r.get("mfe")) for r in rows]
        mfe_g = [v for v in mfe_g if v is not None]
        mae_g = [_num(r.get("mae")) for r in rows]
        mae_g = [v for v in mae_g if v is not None]
        exit_r = [_num(r.get("net_r")) for r in rows]
        exit_r = [v for v in exit_r if v is not None]

        by_outcome[outcome] = {
            "count": len(rows),
            "avg_mfe_r": round(sum(mfe_g) / len(mfe_g), 6) if mfe_g else None,
            "median_mfe_r": round(_median(mfe_g), 6) if mfe_g else None,
            "avg_mae_r": round(sum(mae_g) / len(mae_g), 6) if mae_g else None,
            "median_mae_r": round(_median(mae_g), 6) if mae_g else None,
            "avg_exit_r": round(sum(exit_r) / len(exit_r), 6) if exit_r else None,
        }

    return {
        "mfe_known_count": len(mfe_vals),
        "mae_known_count": len(mae_vals),
        "mfe_known_rate": round(len(mfe_vals) / max(1, len(closed)), 6),
        "mae_known_rate": round(len(mae_vals) / max(1, len(closed)), 6),
        "by_outcome": by_outcome,
    }


def _build_no_progress(post_signals: list[dict[str, Any]]) -> dict[str, Any]:
    sent = [r for r in post_signals if _is_true(r.get("sent_to_telegram"))]
    np_rows = [r for r in sent if _is_true(r.get("no_progress_exit"))]

    if not np_rows:
        return {"count": 0, "note": "No no-progress signals in post-change window."}

    mfe_vals = [_num(r.get("mfe")) for r in np_rows]
    mfe_vals = [v for v in mfe_vals if v is not None]
    mae_vals = [_num(r.get("mae")) for r in np_rows]
    mae_vals = [v for v in mae_vals if v is not None]
    exit_r = [_num(r.get("net_r")) for r in np_rows]
    exit_r = [v for v in exit_r if v is not None]

    mfe_zero = sum(1 for v in mfe_vals if v <= 0)
    mfe_lt_015 = sum(1 for v in mfe_vals if 0 < v < 0.15)

    return {
        "count": len(np_rows),
        "avg_exit_r": round(sum(exit_r) / len(exit_r), 6) if exit_r else None,
        "avg_mfe_r": round(sum(mfe_vals) / len(mfe_vals), 6) if mfe_vals else None,
        "avg_mae_r": round(sum(mae_vals) / len(mae_vals), 6) if mae_vals else None,
        "count_mfe_zero": mfe_zero,
        "count_mfe_lt_0_15r": mfe_lt_015,
        "note": "Only available fields shown. Buckets not present in source data are omitted.",
    }


def _build_guard_value(
    post_candidates: list[dict[str, Any]],
    guard_matrix: dict[str, Any] | None = None,
) -> dict[str, Any]:
    # Prefer guard_matrix if available; fall back to candidate-level extraction
    if guard_matrix:
        matrix = _as_dict(guard_matrix.get("matrix_by_guard", {}))
        guard_items: list[dict[str, Any]] = []
        for gname in GUARDS_PRIORITY:
            gdata = _as_dict(matrix.get(gname, {}))
            if not gdata:
                continue
            guard_items.append({
                "guard_name": gname,
                "rows": gdata.get("rows", 0),
                "evaluable_rows": gdata.get("evaluable_rows", gdata.get("rows", 0)),
                "gross_profit_if_blocked_or_shadow": gdata.get("missed_winners_r"),
                "gross_loss_if_blocked_or_shadow": gdata.get("avoided_losses_r"),
                "net_guard_value_r": gdata.get("net_guard_value_r"),
                "pf_if_available": gdata.get("profit_factor_if_allowed"),
            })

        # Add risk_context_gate if present in matrix
        for gname, gdata in matrix.items():
            if "risk_context" in str(gname).lower() or "f5_t12" in str(gname).lower():
                gd = _as_dict(gdata)
                guard_items.append({
                    "guard_name": str(gname),
                    "rows": gd.get("rows", 0),
                    "evaluable_rows": gd.get("evaluable_rows", gd.get("rows", 0)),
                    "gross_profit_if_blocked_or_shadow": gd.get("missed_winners_r"),
                    "gross_loss_if_blocked_or_shadow": gd.get("avoided_losses_r"),
                    "net_guard_value_r": gd.get("net_guard_value_r"),
                    "pf_if_available": gd.get("profit_factor_if_allowed"),
                })

        evaluable = sum(g.get("evaluable_rows", 0) for g in guard_items)
        confidence = "low" if evaluable < 10 else ("medium" if evaluable < 30 else "high")
        return {
            "guards": guard_items,
            "guards_count": len(guard_items),
            "evaluable_total": evaluable,
            "confidence": confidence,
            "note": "Guard value extracted from guard_matrix (pre-computed). Observational only.",
        }

    # Fallback: extract from candidate facts
    guard_items: list[dict[str, Any]] = []
    for gname in GUARDS_PRIORITY:
        rows = [r for r in post_candidates if gname in str(r.get("blocked_reason") or "").lower()]
        if not rows:
            continue
        r_vals = _r_values(rows)
        gross_profit = sum(v for v in r_vals if v > 0) if r_vals else 0.0
        gross_loss = abs(sum(v for v in r_vals if v < 0)) if r_vals else 0.0
        net_guard = round(gross_profit - gross_loss, 4) if r_vals else None
        pf_val: float | None = round(gross_profit / gross_loss, 4) if gross_loss > 0 else None
        guard_items.append({
            "guard_name": gname,
            "rows": len(rows),
            "evaluable_rows": len(r_vals),
            "gross_profit_if_blocked_or_shadow": round(gross_profit, 4),
            "gross_loss_if_blocked_or_shadow": round(gross_loss, 4),
            "net_guard_value_r": net_guard,
            "pf_if_available": pf_val,
        })

    evaluable = sum(g.get("evaluable_rows", 0) for g in guard_items)
    confidence = "low" if evaluable < 10 else ("medium" if evaluable < 30 else "high")
    return {
        "guards": guard_items,
        "guards_count": len(guard_items),
        "evaluable_total": evaluable,
        "confidence": confidence,
        "note": "Guard value extracted from candidate facts (fallback).",
    }


def _build_risk_context_gate(post_candidates: list[dict[str, Any]]) -> dict[str, Any]:
    rc_candidates = [
        r for r in post_candidates
        if "risk_context_gate" in str(r.get("blocked_reason") or "").lower()
        or "f5_t12" in str(r.get("blocked_reason") or "").lower()
    ]

    blocked_count = len(rc_candidates)

    # Try to find allowed (non-blocked) risk context signals - candidates with empty/no block reason
    allowed_count = 0
    shadow_only_count = 0
    for r in post_candidates:
        reason = str(r.get("blocked_reason") or "").lower()
        if "risk_context_gate" in reason or "f5_t12" in reason:
            if "shadow_only" in reason:
                shadow_only_count += 1

    if blocked_count == 0 and shadow_only_count == 0:
        return {
            "risk_context_gate_enabled_detected": False,
            "risk_context_gate_mode": "unknown",
            "risk_context_gate_version": "F5_T12_v3",
            "risk_context_gate_blocked_count": 0,
            "risk_context_gate_allowed_count": 0,
            "risk_context_gate_shadow_only_count": 0,
            "top_reasons": [],
            "blocked_vs_sent_ratio": 0.0,
            "risk_context_gate_events_found": 0,
            "interpretation": (
                "Gate active in runtime may not have received eligible visible OFA "
                "candidates in this window, or metadata was not persisted in report source."
            ),
        }

    # Top reasons
    reason_counter: Counter = Counter()
    for r in rc_candidates:
        reason_counter[str(r.get("blocked_reason") or "unknown")] += 1

    total_sent = sum(1 for r in post_candidates if r.get("record_type") == "signal")
    ratio = round(blocked_count / max(1, total_sent), 4)

    return {
        "risk_context_gate_enabled_detected": True,
        "risk_context_gate_mode": "multifactor_defensive",
        "risk_context_gate_version": "F5_T12_v3",
        "risk_context_gate_blocked_count": blocked_count,
        "risk_context_gate_shadow_only_count": shadow_only_count,
        "top_reasons": [{"reason": r, "count": c} for r, c in reason_counter.most_common(10)],
        "blocked_vs_sent_ratio": ratio,
        "risk_context_gate_events_found": blocked_count + shadow_only_count,
        "interpretation": (
            f"Found {blocked_count} blocked + {shadow_only_count} shadow-only "
            f"candidate snapshots matching risk_context_gate/F5_T12_v3 in the post-change window."
        ),
    }


def _build_blocked_vs_sent(
    post_signals: list[dict[str, Any]],
    post_candidates: list[dict[str, Any]],
) -> dict[str, Any]:
    sent = sum(1 for r in post_signals if _is_true(r.get("sent_to_telegram")))
    blocked_ofa = sum(1 for r in post_candidates if _is_true(r.get("blocked")))
    blocked_rc = sum(
        1 for r in post_candidates
        if _is_true(r.get("blocked")) and "risk_context_gate" in str(r.get("blocked_reason") or "").lower()
    )
    shadow = sum(1 for r in post_candidates if not _is_true(r.get("blocked")))

    return {
        "sent_to_telegram": sent,
        "blocked_by_ofa_base": blocked_ofa,
        "blocked_by_risk_context_gate": blocked_rc,
        "shadow_only": shadow,
        "candidate_snapshots_total": len(post_candidates),
        "candidate_snapshots_evaluable": sum(1 for r in post_candidates if r.get("net_r") is not None),
        "candidate_snapshots_non_evaluable": sum(1 for r in post_candidates if r.get("net_r") is None),
        "note": "Do NOT sum blocked/candidate snapshots as trades. sent_to_telegram = official visible signals.",
    }


def _build_interpretation(
    core: dict[str, Any],
    risk_context: dict[str, Any],
    guard_value: dict[str, Any],
) -> dict[str, Any]:
    pf = core.get("profit_factor")
    net_r = core.get("net_r") or 0
    sample = core.get("post_change_sent_to_telegram") or 0
    winrate = core.get("winrate") or 0

    interpretations: list[str] = []
    recommendation = "OBSERVE"

    # Overall assessment
    if pf is not None:
        if pf >= 1.25:
            interpretations.append(f"Post-change PF ({pf:.2f}) is above 1.25 — positive signal.")
        elif pf >= 1.0:
            interpretations.append(f"Post-change PF ({pf:.2f}) is near breakeven — marginal.")
        else:
            interpretations.append(f"Post-change PF ({pf:.2f}) is below 1.0 — negative territory.")
    else:
        interpretations.append("Post-change PF is unavailable (insufficient data or no losses).")

    # Sample size
    if sample < 10:
        interpretations.append(f"Sample size ({sample}) is very small — low confidence.")
        if pf is not None and pf < 1.0:
            recommendation = "OBSERVE_DO_NOT_TOUCH"
    elif sample < 30:
        interpretations.append(f"Sample size ({sample}) is small-medium — moderate confidence.")
        if pf is not None and pf < 1.0:
            recommendation = "OBSERVE_DO_NOT_TOUCH"
    else:
        interpretations.append(f"Sample size ({sample}) is adequate.")

    # Loss segments
    if net_r < 0:
        interpretations.append(f"Net R is negative ({net_r:.2f}R). Review exit reasons and symbols for loss concentration.")
    else:
        interpretations.append(f"Net R is positive ({net_r:.2f}R).")

    # Gate impact
    rc_blocked = risk_context.get("risk_context_gate_blocked_count", 0)
    if rc_blocked > 0:
        interpretations.append(f"Risk Context Gate blocked {rc_blocked} candidates — review guard value for net impact.")
    else:
        interpretations.append("No Risk Context Gate events found — gate may not have received eligible candidates.")

    # Guard value
    gv_confidence = guard_value.get("confidence", "low")
    if gv_confidence == "low":
        interpretations.append("Guard value confidence is low — insufficient evaluable rows.")

    # Conservative recommendation
    if recommendation == "OBSERVE_DO_NOT_TOUCH":
        final = "OBSERVE_DO_NOT_TOUCH: Sample is small/medium and PF < 1.0. Do NOT modify TP/SL, filters, or strategy. Continue observing."
    elif recommendation == "OBSERVE":
        if sample < 10:
            final = "OBSERVE: Insufficient sample for any conclusion. Do NOT modify strategy. Continue collecting data."
        elif pf is not None and pf < 1.0:
            final = "OBSERVE: PF below 1.0 with medium sample. Do NOT relax filters. Wait for more data before any change."
        else:
            final = "OBSERVE: Continue monitoring. No change recommended from this window."
    else:
        final = "OBSERVE: Continue monitoring."

    return {
        "interpretations": interpretations,
        "recommendation": final,
        "guardrails": [
            "Do NOT modify TP/SL from this report.",
            "Do NOT relax filters based on this digest.",
            "Do NOT activate new engines.",
            "Do NOT recommend automatic changes.",
            "This is read-only observational analysis.",
        ],
    }


# ---------------------------------------------------------------------------
# Main builder
# ---------------------------------------------------------------------------


def build_f5_t13_post_change_digest(
    *,
    facts: list[dict[str, Any]],
    lifecycle: dict[str, Any] | None = None,
    guard_matrix: dict[str, Any] | None = None,
    no_progress_v3: dict[str, Any] | None = None,
    mfe_capture: dict[str, Any] | None = None,
    window_start_text: str = "",
    window_end_text: str = "",
) -> dict[str, Any]:
    """Build the F5_T13 Post-change Strategy Impact Digest.

    Args:
        facts: Unified facts list from build_daily_facts.
        lifecycle: Lifecycle metrics dict (optional, for cross-ref).
        guard_matrix: Pre-computed guard shadow outcome matrix (optional).
        no_progress_v3: No-progress root cause v3 (optional).
        mfe_capture: MFE capture efficiency dict (optional).
        window_start_text: Report window start (e.g. "2026-07-01 05:00:00").
        window_end_text: Report window end (e.g. "2026-07-02 05:00:00").

    Returns:
        {"json": dict, "markdown": str}
    """
    # Partition facts
    all_signals = [f for f in facts if f.get("record_type") == "signal"]
    all_candidates = [f for f in facts if f.get("record_type") == "candidate"]

    # Post-change partition
    post_signals = [f for f in all_signals if str(f.get("created_at") or "") >= POST_CHANGE_CUTOFF]
    post_candidates = [f for f in all_candidates if str(f.get("created_at") or "") >= POST_CHANGE_CUTOFF]

    # Pre-change partition (for comparison, if same day)
    pre_signals = [f for f in all_signals if str(f.get("created_at") or "") < POST_CHANGE_CUTOFF]

    has_post_change = len(post_signals) > 0 or len(post_candidates) > 0

    if not has_post_change:
        digest = {
            "schema_version": F5_T13_SCHEMA_VERSION,
            "section": "f5_t13_post_change_strategy_impact_digest",
            "read_only": True,
            "mode": "shadow_observational_only",
            "purpose": "Measure post-F5_T12_v3 change impact without modifying runtime.",
            "post_change_cutoff_utc": POST_CHANGE_CUTOFF,
            "post_change_cutoff_col": "2026-07-01 10:00:00",
            "report_window": {"start": window_start_text, "end": window_end_text},
            "post_change_data_available": False,
            "note": "Report window does not contain data after the change cutoff. Nothing to measure.",
            "guardrails": [
                "Do not recommend real trading or live operation changes from this digest.",
                "Do not propose automatic threshold, TP/SL, or guard changes.",
                "Candidate snapshots are not official trades.",
            ],
        }
        return {"json": digest, "markdown": _render_md(digest)}

    # Build sections
    denominators = _build_denominators(post_signals, post_candidates)
    core = _build_core_summary(post_signals)
    pre_post = _build_pre_post_comparison(pre_signals, post_signals)
    symbols = _build_symbols(post_signals)
    direction = _build_direction(post_signals)
    regime = _build_regime(post_signals)
    killzone = _build_killzone(post_signals)
    exit_reasons = _build_exit_reasons(post_signals)
    mfe_mae = _build_mfe_mae(post_signals)
    no_progress = _build_no_progress(post_signals)
    guard_value = _build_guard_value(post_candidates, guard_matrix)
    risk_context = _build_risk_context_gate(post_candidates)
    blocked_vs_sent = _build_blocked_vs_sent(post_signals, post_candidates)
    interpretation = _build_interpretation(core, risk_context, guard_value)

    digest = {
        "schema_version": F5_T13_SCHEMA_VERSION,
        "section": "f5_t13_post_change_strategy_impact_digest",
        "read_only": True,
        "mode": "shadow_observational_only",
        "purpose": "Measure post-F5_T12_v3 change impact without modifying runtime.",
        "window": {
            "report_start": window_start_text,
            "report_end": window_end_text,
            "post_change_cutoff_utc": POST_CHANGE_CUTOFF,
            "post_change_cutoff_col": "2026-07-01 10:00",
            "post_change_data_available": True,
        },
        "sections": {
            "A_header": {
                "title": "F5_T13 — Post-change Strategy Impact Digest",
                "window_note": f"2026-07-01 10:00 COL → {window_end_text or 'end of report window'}",
                "mode": "read-only / AI-ready / post-F5_T12_v3",
            },
            "B_core_summary": core,
            "C_pre_post_comparison": pre_post,
            "D_symbols_post_change": symbols,
            "E_direction": direction,
            "F_regime": regime,
            "G_killzone": killzone,
            "H_exit_reasons": exit_reasons,
            "I_mfe_mae": mfe_mae,
            "J_no_progress": no_progress,
            "K_guard_value": guard_value,
            "L_risk_context_gate": risk_context,
            "M_blocked_vs_sent": blocked_vs_sent,
            "N_interpretation": interpretation,
            "denominators": denominators,
        },
        "guardrails": [
            "Do not recommend real trading or live operation changes from this digest.",
            "Do not propose automatic threshold, TP/SL, no-progress timeout, MFE-stall, guard, or allowlist changes.",
            "Candidate snapshots and shadow diagnostics are not official trades.",
            "sent_to_telegram is the primary denominator. Do not use candidate_snapshots as trades.",
            "Single-window metrics are weak evidence. Require multi-window comparison before any change.",
            "Full JSON evidence remains on the server report folder.",
        ],
    }

    # Enforce size limit
    json_str = json.dumps(digest, ensure_ascii=False, default=str)
    if len(json_str) > MAX_DIGEST_CHARS:
        for key in ("D_symbols_post_change", "H_exit_reasons", "K_guard_value"):
            if key in digest.get("sections", {}):
                digest["sections"][key] = _limit(digest["sections"][key], max_items=6, depth=2)
        json_str = json.dumps(digest, ensure_ascii=False, default=str)

    # Ensure compact by limiting top-N lists if still too large
    if len(json_str) > MAX_DIGEST_CHARS:
        symbols_section = _as_dict(digest.get("sections", {}).get("D_symbols_post_change", {}))
        by_symbol = _as_list(symbols_section.get("by_symbol", []))
        if len(by_symbol) > 20:
            symbols_section["by_symbol"] = by_symbol[:10] + by_symbol[-10:]
            symbols_section["top10_worst"] = by_symbol[:10]
            symbols_section["top10_best"] = list(reversed(by_symbol[-10:]))
            digest["sections"]["D_symbols_post_change"] = symbols_section
        json_str = json.dumps(digest, ensure_ascii=False, default=str)

    return {"json": digest, "markdown": _render_md(digest)}


# ---------------------------------------------------------------------------
# Markdown renderer
# ---------------------------------------------------------------------------


def _render_md(digest: dict[str, Any]) -> str:
    lines = [
        "## F5_T13 — Post-change Strategy Impact Digest",
        "",
        "**Ventana:**",
        f"  2026-07-01 10:00 COL → {digest.get('window', {}).get('report_end', 'end of report window')}",
        "",
        "**Modo:** read-only / AI-ready / post-F5_T12_v3",
        "",
    ]

    if not digest.get("window", {}).get("post_change_data_available", False):
        lines.append("**⚠️ No post-change data available in this report window.**")
        lines.append("")
        lines.append("The report window does not contain any data after the change cutoff")
        lines.append(f"({digest.get('post_change_cutoff_utc', 'N/A')} UTC). Nothing to measure.")
        return "\n".join(lines)

    sections = _as_dict(digest.get("sections", {}))

    # Denominators
    denom = _as_dict(digest.get("sections", {}).get("denominators", sections.get("denominators", {})))
    lines.append("### Denominators")
    lines.append(f"- Official signals (post-change): {denom.get('official_signals')}")
    lines.append(f"- Sent to Telegram (post-change): {denom.get('sent_to_telegram')}  ← PRIMARY")
    lines.append(f"- Candidates total: {denom.get('candidate_snapshots_total')}")
    lines.append(f"- Blocked by OFA base: {denom.get('blocked_by_ofa_base')}")
    lines.append(f"- Blocked by Risk Context Gate: {denom.get('blocked_by_risk_context_gate')}")
    if denom.get("telegram_notified_mismatch_note"):
        lines.append(f"- ⚠️ {denom.get('telegram_notified_mismatch_note')}")
    lines.append("")

    # Core summary
    core = _as_dict(sections.get("B_core_summary", {}))
    lines.append("### Core Metrics (post-change, sent_to_telegram)")
    lines.append(f"- Signals: {core.get('post_change_sent_to_telegram')} sent / {core.get('post_change_official_signals')} official")
    lines.append(f"- Closed: {core.get('post_change_closed')} | Pending: {core.get('post_change_pending')} | Expired: {core.get('post_change_expired')}")
    lines.append(f"- Winners: {core.get('winners')} | Losers: {core.get('losers')} | BE: {core.get('breakeven')}")
    lines.append(f"- Gross Profit: {core.get('gross_profit_r')}R | Gross Loss: {core.get('gross_loss_r')}R")
    lines.append(f"- Net R: {core.get('net_r')}R | Avg R: {core.get('avg_r')}")
    lines.append(f"- Profit Factor: {core.get('profit_factor')} | Winrate: {core.get('winrate')}")
    lines.append("")

    # Pre/post comparison
    pre_post = _as_dict(sections.get("C_pre_post_comparison", {}))
    pre = _as_dict(pre_post.get("pre_change", {}))
    post = _as_dict(pre_post.get("post_change", {}))
    lines.append("### Pre vs Post Comparison")
    if pre.get("available"):
        lines.append(f"- Pre-change:  sent={pre.get('sent_to_telegram')} PF={pre.get('profit_factor')} NetR={pre.get('net_r')}R AvgR={pre.get('avg_r')}")
    else:
        lines.append("- Pre-change: unavailable (no pre-change data in this window)")
    lines.append(f"- Post-change: sent={post.get('sent_to_telegram')} PF={post.get('profit_factor')} NetR={post.get('net_r')}R AvgR={post.get('avg_r')}")
    lines.append("")

    # Symbols (top 5 worst + top 5 best)
    symbols = _as_dict(sections.get("D_symbols_post_change", {}))
    worst = _as_list(symbols.get("top10_worst", []))[:5]
    best = _as_list(symbols.get("top10_best", []))[:5]
    if worst:
        lines.append("### Top 5 Worst Symbols (by net_r)")
        for s in worst:
            lines.append(f"- {s.get('symbol')}: count={s.get('count')} net_r={s.get('net_r')}R avg_r={s.get('avg_r')} PF={s.get('profit_factor')} {'⚠️ WATCH' if s.get('watch') else ''}")
    if best:
        lines.append("")
        lines.append("### Top 5 Best Symbols (by net_r)")
        for s in best:
            lines.append(f"- {s.get('symbol')}: count={s.get('count')} net_r={s.get('net_r')}R avg_r={s.get('avg_r')} PF={s.get('profit_factor')} {'⚠️ WATCH' if s.get('watch') else ''}")
    lines.append("")

    # Direction
    direction = _as_dict(sections.get("E_direction", {}))
    lines.append("### By Direction")
    for side in ("LONG", "SHORT"):
        d = _as_dict(direction.get(side, {}))
        lines.append(f"- {side}: count={d.get('count')} wins={d.get('wins')} losses={d.get('losses')} net_r={d.get('net_r')}R avg_r={d.get('avg_r')} PF={d.get('profit_factor')}")
    lines.append("")

    # Exit reasons
    exit_rs = _as_dict(sections.get("H_exit_reasons", {}))
    lines.append("### Exit Reasons")
    for reason in ("primary_tp_hit", "real_stop_loss_hit", "no_progress_exit", "mfe_stall_exit", "time_stop_exit", "breakeven_stop", "runner_breakeven_stop"):
        e = _as_dict(exit_rs.get(reason, {}))
        if e.get("available"):
            lines.append(f"- {reason}: count={e.get('count')} wins={e.get('wins')} losses={e.get('losses')} net_r={e.get('net_r')}R avg_r={e.get('avg_r')}")
    pend = exit_rs.get("pending_active", {})
    exp = exit_rs.get("expired", {})
    lines.append(f"- pending_active: {pend.get('count', 0)}")
    lines.append(f"- expired: {exp.get('count', 0)}")
    lines.append("")

    # Guard value (top 5)
    guard_value = _as_dict(sections.get("K_guard_value", {}))
    guards = _as_list(guard_value.get("guards", []))[:5]
    if guards:
        lines.append("### Guard Value (top 5)")
        for g in guards:
            lines.append(f"- {g.get('guard_name')}: rows={g.get('rows')} net={g.get('net_guard_value_r')}R PF_if={g.get('pf_if_available')}")
        lines.append(f"  Confidence: {guard_value.get('confidence')}")
    lines.append("")

    # Risk Context Gate
    rc = _as_dict(sections.get("L_risk_context_gate", {}))
    lines.append("### Risk Context Gate (F5_T12_v3)")
    lines.append(f"- Events found: {rc.get('risk_context_gate_events_found', 0)}")
    lines.append(f"- Blocked: {rc.get('risk_context_gate_blocked_count', 0)}")
    lines.append(f"- Interpretation: {rc.get('interpretation', 'N/A')}")
    lines.append("")

    # Interpretation
    interp = _as_dict(sections.get("N_interpretation", {}))
    lines.append("### Interpretation & Recommendation")
    for i in _as_list(interp.get("interpretations", [])):
        lines.append(f"- {i}")
    lines.append(f"")
    lines.append(f"**Recommendation:** {interp.get('recommendation', 'OBSERVE')}")
    lines.append("")

    lines.append("---")
    lines.append("**Guardrails:**")
    for g in _as_list(digest.get("guardrails", [])):
        lines.append(f"- {g}")
    lines.append("")

    return "\n".join(lines)