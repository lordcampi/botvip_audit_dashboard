"""F5_T14 — TP Policy Simulation.

Simulates alternative take-profit policies using closed historical signals
and available MFE/MAE data. Compares the current TP1-protected + runner +
breakeven policy against single-TP and delayed-breakeven alternatives.

Read-only/dashboard-local analytics. Does NOT modify runtime, strategy,
thresholds, TP/SL, lifecycle, DB schema, Telegram, or any bot operation.

Uses sent_to_telegram as primary denominator. Strictly separates
official_signals, sent_to_telegram, candidate_snapshots, events, and facts.
"""
from __future__ import annotations

import json
from collections import defaultdict
from typing import Any

F5_T14_DIGEST_JSON_FILENAME = "31_f5_t14_tp_policy_simulation.json"
F5_T14_DIGEST_MD_FILENAME = "31_f5_t14_tp_policy_simulation.md"
F5_T14_SCHEMA_VERSION = "f5_t14_tp_policy_simulation_v1"
MAX_DIGEST_CHARS = 95_000

# 2026-07-01 10:00 COL = 2026-07-01 15:00 UTC
POST_CHANGE_CUTOFF = "2026-07-01 15:00:00"

WATCH_SYMBOLS = {"NEAR", "DOGE", "SUI", "ADA", "SOL", "XRP"}

# Multipliers for single-TP simulation (relative to TP1 distance in R)
TP_MULTIPLIERS = [1.10, 1.15, 1.20, 1.25, 1.50]

# Delayed BE thresholds in R
BE_THRESHOLDS = [
    {"label": "at_tp1_hit", "r_threshold": None, "desc": "Move BE when TP1 is hit (current behavior reference)"},
    {"label": "mfe_ge_1_10r", "r_threshold": 1.10, "desc": "Move BE only if MFE >= 1.10R"},
    {"label": "mfe_ge_1_25r", "r_threshold": 1.25, "desc": "Move BE only if MFE >= 1.25R"},
    {"label": "mfe_ge_1_50r", "r_threshold": 1.50, "desc": "Move BE only if MFE >= 1.50R"},
]

# Post-TP1 extension buckets (extra R beyond TP1)
POST_TP1_BUCKETS = [
    ("reached_tp1_only", 0, 0),
    ("reached_tp1_plus_0_10R", 0.0001, 0.10),
    ("reached_tp1_plus_0_25R", 0.1001, 0.25),
    ("reached_tp1_plus_0_50R", 0.2501, 0.50),
    ("reached_tp1_plus_1_00R", 0.5001, 1.00),
    ("reached_tp1_plus_1_50R", 1.0001, float("inf")),
]


# ---------------------------------------------------------------------------
# Safe helpers
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
# Core metrics
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
    pf = round(gross_profit / gross_loss, 4) if gross_loss > 0 else (None if gross_profit <= 0 and gross_loss >= 0 else None)
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
# TP1 distance estimation
# ---------------------------------------------------------------------------


def _estimate_tp1_r(row: dict[str, Any]) -> float | None:
    """Estimate TP1 distance in R from available fields.

    Uses primary_tp_distance / sl_distance if both exist.
    Falls back to net_r for winner signals where primary_tp_hit is True
    (runner extra R may inflate, so we use the minimum of net_r and
    a default 1.0R assumption for TP1).

    Returns None if geometry is insufficient.
    """
    primary_tp_dist = _num(row.get("primary_tp_distance"))
    sl_dist = _num(row.get("sl_distance"))

    if primary_tp_dist is not None and sl_dist is not None and sl_dist > 0:
        return round(primary_tp_dist / sl_dist, 6)

    # Fallback: use net_r for pure TP1 winners (no runner extension)
    net_r = _num(row.get("net_r"))
    if net_r is not None and net_r > 0 and _is_true(row.get("primary_tp_hit")):
        # Assume TP1 ≈ 1.0R as safe floor estimate
        return min(net_r, 1.0) if net_r > 1.0 else net_r

    return None


def _estimate_mfe_r(row: dict[str, Any]) -> float | None:
    """Extract MFE in R directly from the fact row."""
    return _num(row.get("mfe"))


def _estimate_mae_r(row: dict[str, Any]) -> float | None:
    """Extract MAE in R directly from the fact row."""
    return _num(row.get("mae"))


# ---------------------------------------------------------------------------
# Section A: Current Policy
# ---------------------------------------------------------------------------


def _build_current_policy(post_signals: list[dict[str, Any]]) -> dict[str, Any]:
    sent = [r for r in post_signals if _is_true(r.get("sent_to_telegram"))]
    closed = [r for r in sent if r.get("closed_at") is not None]
    metrics = _core_metrics(closed)

    # Count by exit reason
    tp1_hit = sum(1 for r in closed if _is_true(r.get("primary_tp_hit")))
    real_sl = sum(1 for r in closed if _is_true(r.get("real_stop_loss_hit")))
    be_stop = sum(1 for r in closed if _is_true(r.get("breakeven_stop_hit")))
    runner_be = sum(1 for r in closed if _is_true(r.get("runner_breakeven_stop_hit")))
    time_stop = sum(1 for r in closed if _is_true(r.get("time_stop_exit")))
    no_progress = sum(1 for r in closed if _is_true(r.get("no_progress_exit")))
    mfe_stall = sum(1 for r in closed if _is_true(r.get("mfe_stall_exit")))

    return {
        "denominator": "sent_to_telegram",
        "total_sent": len(sent),
        "total_closed": len(closed),
        "pending_or_active": len(sent) - len(closed),
        "metrics": metrics,
        "exit_reason_counts": {
            "primary_tp_hit": tp1_hit,
            "real_stop_loss_hit": real_sl,
            "breakeven_stop_hit": be_stop,
            "runner_breakeven_stop_hit": runner_be,
            "time_stop_exit": time_stop,
            "no_progress_exit": no_progress,
            "mfe_stall_exit": mfe_stall,
        },
    }


# ---------------------------------------------------------------------------
# Section B: TP1 Hit Analysis
# ---------------------------------------------------------------------------


def _build_tp1_hit_analysis(post_signals: list[dict[str, Any]]) -> dict[str, Any]:
    sent = [r for r in post_signals if _is_true(r.get("sent_to_telegram"))]
    closed = [r for r in sent if r.get("closed_at") is not None]
    tp1_hit_rows = [r for r in closed if _is_true(r.get("primary_tp_hit"))]

    if not tp1_hit_rows:
        return {
            "count_tp1_hit": 0,
            "available": False,
            "note": "No TP1 hits in post-change closed signals.",
        }

    # MFE post-TP1: how far beyond TP1 did price go?
    tp1_r_vals = [_estimate_tp1_r(r) for r in tp1_hit_rows]
    tp1_r_vals_clean = [v for v in tp1_r_vals if v is not None]
    mfe_vals = [_estimate_mfe_r(r) for r in tp1_hit_rows]
    mfe_vals_clean = [v for v in mfe_vals if v is not None]
    final_r_vals = [_num(r.get("net_r")) for r in tp1_hit_rows]
    final_r_vals_clean = [v for v in final_r_vals if v is not None]

    # Extension beyond TP1 = MFE - TP1_R
    extensions: list[float] = []
    only_tp1_count = 0
    extended_count = 0
    for i, r in enumerate(tp1_hit_rows):
        mfe = mfe_vals[i] if i < len(mfe_vals) else None
        tp1_r = tp1_r_vals[i] if i < len(tp1_r_vals) else None
        if mfe is not None and tp1_r is not None:
            ext = mfe - tp1_r
            extensions.append(ext)
            if ext <= 0.05:
                only_tp1_count += 1
            else:
                extended_count += 1

    metrics = _core_metrics(tp1_hit_rows)

    return {
        "count_tp1_hit": len(tp1_hit_rows),
        "available": True,
        "tp1_r_estimated_count": len(tp1_r_vals_clean),
        "avg_tp1_r": round(sum(tp1_r_vals_clean) / len(tp1_r_vals_clean), 6) if tp1_r_vals_clean else None,
        "mfe_known_count": len(mfe_vals_clean),
        "avg_mfe_r": round(sum(mfe_vals_clean) / len(mfe_vals_clean), 6) if mfe_vals_clean else None,
        "max_mfe_r": round(max(mfe_vals_clean), 6) if mfe_vals_clean else None,
        "avg_final_r": metrics["avg_r"],
        "final_gross_profit_r": metrics["gross_profit_r"],
        "final_net_r": metrics["net_r"],
        "final_pf": metrics["profit_factor"],
        "reached_tp1_only_no_extension": only_tp1_count,
        "reached_tp1_and_extended": extended_count,
        "extension_unknown": len(tp1_hit_rows) - only_tp1_count - extended_count,
        "avg_extension_r_beyond_tp1": round(sum(extensions) / len(extensions), 6) if extensions else None,
        "median_extension_r_beyond_tp1": round(_median(extensions), 6) if extensions else None,
        "note": "Extension beyond TP1 = MFE_R - TP1_R. Values > 0 indicate room for wider TP.",
    }


# ---------------------------------------------------------------------------
# Section C: Single TP Simulations
# ---------------------------------------------------------------------------


def _build_single_tp_simulations(post_signals: list[dict[str, Any]]) -> dict[str, Any]:
    sent = [r for r in post_signals if _is_true(r.get("sent_to_telegram"))]
    closed = [r for r in sent if r.get("closed_at") is not None]
    tp1_hit_rows = [r for r in closed if _is_true(r.get("primary_tp_hit"))]

    if not closed:
        return {
            "available": False,
            "note": "No closed signals in post-change window.",
            "simulations": [],
        }

    simulations: list[dict[str, Any]] = []
    has_mfe = any(_estimate_mfe_r(r) is not None for r in closed)

    for multiplier in TP_MULTIPLIERS:
        sim_label = f"tp_single_{str(multiplier).replace('.', '_')}x_tp1"
        sim_net_r = 0.0
        sim_wins = 0
        sim_losses = 0
        sim_be = 0
        sim_gross_profit = 0.0
        sim_gross_loss = 0.0
        approximation_used = False

        for r in closed:
            mfe_r = _estimate_mfe_r(r)
            tp1_r = _estimate_tp1_r(r)
            actual_r = _num(r.get("net_r")) or 0.0
            target_r = None

            if tp1_r is not None:
                target_r = tp1_r * multiplier
            elif mfe_r is not None and _is_true(r.get("primary_tp_hit")):
                # Approximate TP1 as 1.0R for winners without geometry
                target_r = 1.0 * multiplier
                approximation_used = True

            if target_r is not None and mfe_r is not None:
                # Can simulate with MFE geometry
                if mfe_r >= target_r:
                    # Would have hit wider TP
                    sim_net_r += target_r
                    sim_wins += 1
                    sim_gross_profit += target_r
                else:
                    # Did not reach target; fall back to actual outcome
                    # (could be TP1+BE, runner BE, or loss)
                    sim_net_r += actual_r
                    if actual_r > 0:
                        sim_wins += 1
                        sim_gross_profit += actual_r
                    elif actual_r < 0:
                        sim_losses += 1
                        sim_gross_loss += abs(actual_r)
                    else:
                        sim_be += 1
            elif target_r is not None and mfe_r is None and _is_true(r.get("primary_tp_hit")):
                # No MFE data but signal hit TP1 → assume it would hit wider TP
                # (conservative: only for actual TP1 winners)
                sim_net_r += target_r
                sim_wins += 1
                sim_gross_profit += target_r
                approximation_used = True
            else:
                # No geometry available → use actual outcome as-is
                sim_net_r += actual_r
                if actual_r > 0:
                    sim_wins += 1
                    sim_gross_profit += actual_r
                elif actual_r < 0:
                    sim_losses += 1
                    sim_gross_loss += abs(actual_r)
                else:
                    sim_be += 1
                if mfe_r is None:
                    approximation_used = True

        sim_total = sim_wins + sim_losses + sim_be
        sim_pf: float | None = None
        if sim_gross_loss > 0:
            sim_pf = round(sim_gross_profit / sim_gross_loss, 4)
        sim_avg_r = round(sim_net_r / sim_total, 6) if sim_total > 0 else None
        sim_winrate = round(sim_wins / sim_total, 6) if sim_total > 0 else None

        # Calculate how many TP1 winners would have hit this wider TP
        would_hit_wider = 0
        would_miss_wider = 0
        for r in tp1_hit_rows:
            mfe_r = _estimate_mfe_r(r)
            tp1_r = _estimate_tp1_r(r)
            if mfe_r is not None and tp1_r is not None:
                if mfe_r >= tp1_r * multiplier:
                    would_hit_wider += 1
                else:
                    would_miss_wider += 1

        simulations.append({
            "label": sim_label,
            "multiplier": multiplier,
            "description": f"Single TP at {multiplier}x TP1 distance",
            "total_signals": len(closed),
            "wins": sim_wins,
            "losses": sim_losses,
            "breakeven": sim_be,
            "gross_profit_r": round(sim_gross_profit, 4),
            "gross_loss_r": round(sim_gross_loss, 4),
            "net_r": round(sim_net_r, 4),
            "profit_factor": sim_pf,
            "avg_r": sim_avg_r,
            "winrate": sim_winrate,
            "tp1_hit_would_reach_wider_tp": would_hit_wider,
            "tp1_hit_would_miss_wider_tp": would_miss_wider,
            "tp1_hit_unknown": len(tp1_hit_rows) - would_hit_wider - would_miss_wider,
            "mfe_based": has_mfe,
            "approximation_used": approximation_used,
        })

    return {
        "available": True,
        "mfe_based": has_mfe,
        "tp_multiples_tested": TP_MULTIPLIERS,
        "simulations": simulations,
        "approximation_note": (
            "If MFE is unavailable for a signal, the simulation falls back to "
            "actual net_r for non-winners or assumes TP1 winners would reach "
            "wider TP (optimistic). Declare approximation_used=true explicitly."
        ) if not has_mfe else None,
    }


# ---------------------------------------------------------------------------
# Section D: Delayed BE Simulations
# ---------------------------------------------------------------------------


def _build_delayed_be_simulations(post_signals: list[dict[str, Any]]) -> dict[str, Any]:
    sent = [r for r in post_signals if _is_true(r.get("sent_to_telegram"))]
    closed = [r for r in sent if r.get("closed_at") is not None]

    if not closed:
        return {
            "available": False,
            "note": "No closed signals in post-change window.",
            "simulations": [],
        }

    simulations: list[dict[str, Any]] = []
    has_mfe = any(_estimate_mfe_r(r) is not None for r in closed)

    for be_config in BE_THRESHOLDS:
        label = be_config["label"]
        threshold = be_config["r_threshold"]

        sim_net_r = 0.0
        sim_wins = 0
        sim_losses = 0
        sim_be = 0
        sim_gross_profit = 0.0
        sim_gross_loss = 0.0
        saved_from_loss = 0
        flipped_to_profit = 0
        approximation_used = False

        for r in closed:
            mfe_r = _estimate_mfe_r(r)
            mae_r = _estimate_mae_r(r)
            actual_r = _num(r.get("net_r")) or 0.0
            hit_tp1 = _is_true(r.get("primary_tp_hit"))
            hit_sl = _is_true(r.get("real_stop_loss_hit"))

            if threshold is None:
                # "at_tp1_hit" → current behavior reference, just recount
                # Move BE when TP1 hit; if signal hit TP1, it wins TP1_R;
                # else actual outcome
                if hit_tp1:
                    tp1_r = _estimate_tp1_r(r) or 1.0
                    sim_net_r += tp1_r
                    sim_wins += 1
                    sim_gross_profit += tp1_r
                else:
                    sim_net_r += actual_r
                    if actual_r > 0:
                        sim_wins += 1
                        sim_gross_profit += actual_r
                    elif actual_r < 0:
                        sim_losses += 1
                        sim_gross_loss += abs(actual_r)
                    else:
                        sim_be += 1
            elif mfe_r is not None:
                # Delayed BE: only move BE if MFE >= threshold
                if mfe_r >= threshold:
                    # BE moved → signal closes at >= 0 (at worst BE)
                    if hit_tp1:
                        tp1_r = _estimate_tp1_r(r) or 1.0
                        outcome_r = max(tp1_r, 0)  # At minimum 0 (BE)
                        sim_net_r += outcome_r
                        sim_wins += 1
                        sim_gross_profit += outcome_r
                        if actual_r <= 0:
                            flipped_to_profit += 1
                    elif actual_r < 0:
                        # Would have been saved by BE
                        sim_be += 1
                        saved_from_loss += 1
                    elif actual_r > 0:
                        sim_net_r += actual_r
                        sim_wins += 1
                        sim_gross_profit += actual_r
                    else:
                        sim_be += 1
                else:
                    # MFE < threshold → did not move BE → actual outcome
                    sim_net_r += actual_r
                    if actual_r > 0:
                        sim_wins += 1
                        sim_gross_profit += actual_r
                    elif actual_r < 0:
                        sim_losses += 1
                        sim_gross_loss += abs(actual_r)
                    else:
                        sim_be += 1
            else:
                # No MFE → cannot simulate delayed BE → use actual
                sim_net_r += actual_r
                if actual_r > 0:
                    sim_wins += 1
                    sim_gross_profit += actual_r
                elif actual_r < 0:
                    sim_losses += 1
                    sim_gross_loss += abs(actual_r)
                else:
                    sim_be += 1
                approximation_used = True

        sim_total = sim_wins + sim_losses + sim_be
        sim_pf = round(sim_gross_profit / sim_gross_loss, 4) if sim_gross_loss > 0 else None
        sim_avg_r = round(sim_net_r / sim_total, 6) if sim_total > 0 else None
        sim_winrate = round(sim_wins / sim_total, 6) if sim_total > 0 else None

        simulations.append({
            "label": label,
            "threshold_r": threshold,
            "description": be_config["desc"],
            "total_signals": len(closed),
            "wins": sim_wins,
            "losses": sim_losses,
            "breakeven": sim_be,
            "gross_profit_r": round(sim_gross_profit, 4),
            "gross_loss_r": round(sim_gross_loss, 4),
            "net_r": round(sim_net_r, 4),
            "profit_factor": sim_pf,
            "avg_r": sim_avg_r,
            "winrate": sim_winrate,
            "saved_from_loss": saved_from_loss,
            "flipped_from_loss_to_profit": flipped_to_profit,
            "mfe_based": has_mfe,
            "approximation_used": approximation_used,
        })

    return {
        "available": True,
        "mfe_based": has_mfe,
        "be_thresholds_tested": [b["label"] for b in BE_THRESHOLDS],
        "simulations": simulations,
    }


# ---------------------------------------------------------------------------
# Section E: Post-TP1 Extension Distribution
# ---------------------------------------------------------------------------


def _build_post_tp1_extension(post_signals: list[dict[str, Any]]) -> dict[str, Any]:
    sent = [r for r in post_signals if _is_true(r.get("sent_to_telegram"))]
    closed = [r for r in sent if r.get("closed_at") is not None]
    tp1_hit_rows = [r for r in closed if _is_true(r.get("primary_tp_hit"))]

    if not tp1_hit_rows:
        return {
            "available": False,
            "count_tp1_hit": 0,
            "note": "No TP1 hits in post-change closed signals.",
            "buckets": [],
        }

    buckets: dict[str, int] = {}
    unknown_count = 0

    for r in tp1_hit_rows:
        mfe_r = _estimate_mfe_r(r)
        tp1_r = _estimate_tp1_r(r)
        if mfe_r is not None and tp1_r is not None:
            extension = mfe_r - tp1_r
            bucket_found = False
            for bucket_name, lo, hi in POST_TP1_BUCKETS:
                if lo <= extension <= hi:
                    buckets[bucket_name] = buckets.get(bucket_name, 0) + 1
                    bucket_found = True
                    break
            if not bucket_found:
                buckets["reached_tp1_only"] = buckets.get("reached_tp1_only", 0) + 1
        else:
            unknown_count += 1

    bucket_list = []
    for bucket_name, lo, hi in POST_TP1_BUCKETS:
        bucket_list.append({
            "bucket": bucket_name,
            "range_low_r": lo,
            "range_high_r": hi if hi != float("inf") else "inf",
            "count": buckets.get(bucket_name, 0),
        })

    return {
        "available": True,
        "count_tp1_hit": len(tp1_hit_rows),
        "mfe_known_for_extension": len(tp1_hit_rows) - unknown_count,
        "unknown_extension": unknown_count,
        "buckets": bucket_list,
        "interpretation": (
            "Buckets show how far beyond TP1 the price extended. "
            "reached_tp1_only means price barely touched TP1 and reversed. "
            "Larger buckets (0.25R+) suggest room for wider TP on these signals."
        ),
    }


# ---------------------------------------------------------------------------
# Section F: Segment Breakdowns
# ---------------------------------------------------------------------------


def _build_segment_breakdowns(post_signals: list[dict[str, Any]]) -> dict[str, Any]:
    sent = [r for r in post_signals if _is_true(r.get("sent_to_telegram"))]
    closed = [r for r in sent if r.get("closed_at") is not None]

    def _segment(rows: list[dict[str, Any]], key: str) -> dict[str, Any]:
        buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for r in rows:
            val = str(r.get(key) or "UNKNOWN").strip().upper()
            buckets[val].append(r)

        result: dict[str, Any] = {}
        for val, group in sorted(buckets.items()):
            m = _core_metrics(group)
            tp1_rows = [r for r in group if _is_true(r.get("primary_tp_hit"))]
            tp1_mfe_ext = []
            for r in tp1_rows:
                mfe_r = _estimate_mfe_r(r)
                tp1_r = _estimate_tp1_r(r)
                if mfe_r is not None and tp1_r is not None:
                    tp1_mfe_ext.append(mfe_r - tp1_r)
            avg_ext = round(sum(tp1_mfe_ext) / len(tp1_mfe_ext), 6) if tp1_mfe_ext else None
            result[val] = {
                "count": m["count"],
                "wins": m["wins"],
                "losses": m["losses"],
                "net_r": m["net_r"],
                "profit_factor": m["profit_factor"],
                "avg_r": m["avg_r"],
                "tp1_hits": len(tp1_rows),
                "avg_extension_r_beyond_tp1": avg_ext,
                "tolerates_wider_tp_evidence": (
                    "positive" if (avg_ext is not None and avg_ext > 0.15)
                    else ("marginal" if (avg_ext is not None and avg_ext > 0.05) else "negative")
                ) if avg_ext is not None else "unknown",
            }
        return result

    # Top symbols where wider TP would help/hurt
    symbol_segments = _segment(closed, "symbol")
    symbol_ranking = []
    for sym, data in symbol_segments.items():
        if isinstance(data, dict):
            ext = data.get("avg_extension_r_beyond_tp1")
            symbol_ranking.append({
                "symbol": sym,
                "count": data.get("count", 0),
                "tp1_hits": data.get("tp1_hits", 0),
                "avg_extension_r": ext,
                "net_r": data.get("net_r"),
                "tolerates_wider": data.get("tolerates_wider_tp_evidence"),
                "watch": sym in WATCH_SYMBOLS,
            })

    symbol_ranking.sort(key=lambda x: x.get("avg_extension_r") or -999, reverse=True)
    top_wider_friendly = [s for s in symbol_ranking if s.get("avg_extension_r") is not None][:10]
    top_wider_hostile = [s for s in symbol_ranking if s.get("avg_extension_r") is not None][-10:]
    top_wider_hostile.reverse()

    # Side breakdown
    direction = _segment(closed, "side")

    # Regime breakdown
    regime = _segment(closed, "market_regime")

    # Killzone breakdown
    killzone_data: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in closed:
        if _is_true(r.get("killzone")):
            killzone_data["KILLZONE"].append(r)
        elif r.get("killzone") is not None:
            killzone_data["OUT_OF_KILLZONE"].append(r)
        else:
            killzone_data["UNKNOWN"].append(r)
    killzone = {}
    for kz, group in sorted(killzone_data.items()):
        m = _core_metrics(group)
        tp1_kz = [r for r in group if _is_true(r.get("primary_tp_hit"))]
        tp1_ext_kz = []
        for r in tp1_kz:
            mfe_r = _estimate_mfe_r(r)
            tp1_r = _estimate_tp1_r(r)
            if mfe_r is not None and tp1_r is not None:
                tp1_ext_kz.append(mfe_r - tp1_r)
        avg_ext_kz = round(sum(tp1_ext_kz) / len(tp1_ext_kz), 6) if tp1_ext_kz else None
        killzone[kz] = {
            "count": m["count"],
            "net_r": m["net_r"],
            "profit_factor": m["profit_factor"],
            "tp1_hits": len(tp1_kz),
            "avg_extension_r": avg_ext_kz,
        }

    # Exit reason breakdown
    exit_reason_data: dict[str, list[dict[str, Any]]] = defaultdict(list)
    exit_keys = [
        ("primary_tp_hit", "primary_tp_hit"),
        ("real_stop_loss_hit", "real_stop_loss"),
        ("breakeven_stop_hit", "breakeven_stop"),
        ("runner_breakeven_stop_hit", "runner_breakeven_stop"),
        ("time_stop_exit", "time_stop"),
        ("no_progress_exit", "no_progress"),
        ("mfe_stall_exit", "mfe_stall"),
    ]
    for r in closed:
        classified = False
        for key, label in exit_keys:
            if _is_true(r.get(key)):
                exit_reason_data[label].append(r)
                classified = True
                break
        if not classified:
            exit_reason_data["other"].append(r)

    exit_reasons = {}
    for reason, group in sorted(exit_reason_data.items()):
        m = _core_metrics(group)
        mfe_g = [_estimate_mfe_r(r) for r in group]
        mfe_g = [v for v in mfe_g if v is not None]
        exit_reasons[reason] = {
            "count": m["count"],
            "avg_r": m["avg_r"],
            "avg_mfe_r": round(sum(mfe_g) / len(mfe_g), 6) if mfe_g else None,
            "max_mfe_r": round(max(mfe_g), 6) if mfe_g else None,
        }

    return {
        "by_symbol_top10_wider_friendly": top_wider_friendly,
        "by_symbol_top10_wider_hostile": top_wider_hostile,
        "watched_symbols": [s for s in symbol_ranking if s.get("watch")],
        "by_direction": direction,
        "by_regime": regime,
        "by_killzone": killzone,
        "by_exit_reason": exit_reasons,
    }


# ---------------------------------------------------------------------------
# Section G: Data Quality
# ---------------------------------------------------------------------------


def _build_data_quality(post_signals: list[dict[str, Any]]) -> dict[str, Any]:
    sent = [r for r in post_signals if _is_true(r.get("sent_to_telegram"))]
    closed = [r for r in sent if r.get("closed_at") is not None]

    mfe_known = sum(1 for r in closed if _estimate_mfe_r(r) is not None)
    mae_known = sum(1 for r in closed if _estimate_mae_r(r) is not None)
    tp1_r_known = sum(1 for r in closed if _estimate_tp1_r(r) is not None)
    tp1_hit_rows = [r for r in closed if _is_true(r.get("primary_tp_hit"))]
    tp1_ext_known = 0
    for r in tp1_hit_rows:
        if _estimate_mfe_r(r) is not None and _estimate_tp1_r(r) is not None:
            tp1_ext_known += 1

    insufficient_geometry = len(closed) - mfe_known
    approximation_used = mfe_known < len(closed)

    limitations: list[str] = []
    if mfe_known == 0:
        limitations.append("No MFE data available for any closed signal. All simulations use actual net_r as fallback (no simulation possible).")
    elif mfe_known < len(closed) * 0.5:
        limitations.append(f"MFE known for only {mfe_known}/{len(closed)} signals (<50%). Simulations have low confidence.")
    if mae_known == 0:
        limitations.append("No MAE data available. Delayed BE simulations cannot model SL-hit scenarios accurately.")
    if tp1_r_known == 0:
        limitations.append("No TP1 distance geometry available. Using 1.0R default where TP1 was hit.")

    confidence = "high" if mfe_known >= len(closed) * 0.8 else ("medium" if mfe_known >= len(closed) * 0.4 else "low")

    return {
        "mfe_known_count": mfe_known,
        "mae_known_count": mae_known,
        "tp1_r_known_count": tp1_r_known,
        "tp1_post_extension_known_count": tp1_ext_known,
        "total_closed": len(closed),
        "insufficient_geometry_count": insufficient_geometry,
        "approximation_used": approximation_used,
        "confidence": confidence,
        "limitations": limitations if limitations else ["All required geometry available for full simulation."],
    }


# ---------------------------------------------------------------------------
# Section H: Interpretation
# ---------------------------------------------------------------------------


def _build_interpretation(
    current: dict[str, Any],
    single_tp: dict[str, Any],
    delayed_be: dict[str, Any],
    post_tp1_ext: dict[str, Any],
    segments: dict[str, Any],
    data_quality: dict[str, Any],
) -> dict[str, Any]:
    interpretations: list[str] = []
    current_metrics = current.get("metrics", {})
    current_pf = current_metrics.get("profit_factor")
    current_net_r = current_metrics.get("net_r") or 0
    sample = current.get("total_closed") or 0

    # Overall assessment
    if sample < 5:
        interpretations.append(f"Sample size ({sample}) is extremely small — no statistically meaningful conclusions possible.")
        recommendation = "observe_only"
    elif sample < 15:
        interpretations.append(f"Sample size ({sample}) is small — low confidence, observe only.")
        recommendation = "observe_only"
    else:
        # Analyze single TP simulations
        sims = _as_list(single_tp.get("simulations", []))
        best_tp_sim = None
        best_tp_net_r = -999
        best_tp_pf = None
        for s in sims:
            nr = s.get("net_r") or -999
            pf = s.get("profit_factor")
            if nr > best_tp_net_r:
                best_tp_net_r = nr
                best_tp_sim = s
                best_tp_pf = pf

        if best_tp_sim:
            tp_label = best_tp_sim.get("label", "unknown")
            tp_pf = best_tp_sim.get("profit_factor")
            tp_net = best_tp_sim.get("net_r")

            if tp_pf is not None and current_pf is not None:
                if tp_pf > current_pf:
                    interpretations.append(f"Single TP ({tp_label}) PF={tp_pf} vs current PF={current_pf} — single TP would IMPROVE PF.")
                elif tp_pf < current_pf:
                    interpretations.append(f"Single TP ({tp_label}) PF={tp_pf} vs current PF={current_pf} — single TP would WORSEN PF.")
                else:
                    interpretations.append(f"Single TP ({tp_label}) PF={tp_pf} equals current PF={current_pf} — no difference.")

            if tp_net is not None and current_net_r is not None:
                if tp_net > current_net_r:
                    interpretations.append(f"Single TP ({tp_label}) Net R={tp_net}R vs current Net R={current_net_r}R — single TP would IMPROVE Net R.")
                elif tp_net < current_net_r:
                    interpretations.append(f"Single TP ({tp_label}) Net R={tp_net}R vs current Net R={current_net_r}R — single TP would WORSEN Net R.")
                else:
                    interpretations.append(f"Single TP ({tp_label}) Net R={tp_net}R equals current Net R={current_net_r}R.")
        else:
            interpretations.append("No single TP simulations available for comparison.")

        # Analyze post-TP1 extension
        ext_buckets = _as_list(post_tp1_ext.get("buckets", []))
        only_tp1_count = 0
        extended_count = 0
        for b in ext_buckets:
            if b.get("bucket") == "reached_tp1_only":
                only_tp1_count = b.get("count", 0)
            else:
                extended_count += b.get("count", 0)

        if only_tp1_count > extended_count:
            interpretations.append(f"More signals only touch TP1 ({only_tp1_count}) than extend beyond ({extended_count}) — wider TP would likely hurt.")
        elif extended_count > only_tp1_count:
            interpretations.append(f"More signals extend beyond TP1 ({extended_count}) than only touch it ({only_tp1_count}) — wider TP may have merit.")
        else:
            interpretations.append("Equal split between TP1-only and extending signals — inconclusive.")

        # BE analysis
        be_sims = _as_list(delayed_be.get("simulations", []))
        saved_total = 0
        for bs in be_sims:
            saved_total += bs.get("saved_from_loss", 0)
        if saved_total > 0:
            interpretations.append(f"Delayed BE could save {saved_total} signals from loss across all thresholds.")
        else:
            interpretations.append("Delayed BE does not appear to save additional signals — current BE timing may be adequate or MFE data insufficient.")

        # Symbol analysis
        wider_friendly = _as_list(segments.get("by_symbol_top10_wider_friendly", []))
        wider_hostile = _as_list(segments.get("by_symbol_top10_wider_hostile", []))
        friendly_syms = [s.get("symbol") for s in wider_friendly[:3] if s.get("tolerates_wider") == "positive"]
        hostile_syms = [s.get("symbol") for s in wider_hostile[:3] if s.get("tolerates_wider") == "negative"]
        if friendly_syms:
            interpretations.append(f"Symbols that may tolerate wider TP: {', '.join(friendly_syms)}.")
        if hostile_syms:
            interpretations.append(f"Symbols that need fast TP: {', '.join(hostile_syms)}.")

        # Recommendation
        confidence = data_quality.get("confidence", "low")
        if confidence == "low" or sample < 15:
            recommendation = "observe_only"
        elif best_tp_sim and best_tp_pf is not None and current_pf is not None and best_tp_pf > current_pf * 1.1:
            recommendation = "simulate_more"
        elif only_tp1_count > extended_count * 1.5:
            recommendation = "keep_current_policy"
        else:
            recommendation = "observe_only"

    rec_map = {
        "observe_only": "OBSERVE_ONLY: Continue collecting data. No change recommended from this window.",
        "keep_current_policy": "KEEP_CURRENT_POLICY: Evidence suggests TP1-protected + runner + BE is better than wider single TP.",
        "simulate_more": "SIMULATE_MORE: Single TP shows potential improvement but sample is insufficient for runtime change.",
        "candidate_for_small_flagged_change": "CANDIDATE_FOR_SMALL_FLAGGED_CHANGE: Evidence is suggestive. Flag for human review. Do NOT auto-deploy.",
    }
    final_recommendation = rec_map.get(recommendation, rec_map["observe_only"])

    return {
        "interpretations": interpretations,
        "recommendation": final_recommendation,
        "recommendation_code": recommendation,
        "guardrails": [
            "Do NOT modify TP/SL from this simulation.",
            "Do NOT change BE timing from this simulation.",
            "Do NOT activate new engines.",
            "Do NOT recommend automatic changes.",
            "This is read-only observational simulation.",
            "MFE/MAE data may be incomplete — verify data quality before any decision.",
        ],
    }


# ---------------------------------------------------------------------------
# Main builder
# ---------------------------------------------------------------------------


def build_f5_t14_tp_policy_simulation(
    *,
    facts: list[dict[str, Any]],
    lifecycle: dict[str, Any] | None = None,
    window_start_text: str = "",
    window_end_text: str = "",
) -> dict[str, Any]:
    """Build the F5_T14 TP Policy Simulation digest.

    Args:
        facts: Unified facts list from build_daily_facts.
        lifecycle: Lifecycle metrics dict (optional).
        window_start_text: Report window start.
        window_end_text: Report window end.

    Returns:
        {"json": dict, "markdown": str}
    """
    all_signals = [f for f in facts if f.get("record_type") == "signal"]

    # Post-change partition
    post_signals = [f for f in all_signals if str(f.get("created_at") or "") >= POST_CHANGE_CUTOFF]
    has_post_change = len(post_signals) > 0

    if not has_post_change:
        digest = {
            "schema_version": F5_T14_SCHEMA_VERSION,
            "section": "f5_t14_tp_policy_simulation",
            "read_only": True,
            "mode": "shadow_observational_only",
            "purpose": "Simulate alternative TP policies without modifying runtime.",
            "post_change_cutoff_utc": POST_CHANGE_CUTOFF,
            "post_change_cutoff_col": "2026-07-01 10:00:00",
            "report_window": {"start": window_start_text, "end": window_end_text},
            "post_change_data_available": False,
            "note": "Report window does not contain data after the change cutoff. Nothing to simulate.",
            "guardrails": [
                "Do not recommend real trading or live operation changes from this digest.",
                "Do not propose automatic TP/SL or BE timing changes.",
                "Candidate snapshots are not official trades.",
            ],
        }
        return {"json": digest, "markdown": _render_md(digest)}

    # Build sections
    current_policy = _build_current_policy(post_signals)
    tp1_hit_analysis = _build_tp1_hit_analysis(post_signals)
    single_tp = _build_single_tp_simulations(post_signals)
    delayed_be = _build_delayed_be_simulations(post_signals)
    post_tp1_ext = _build_post_tp1_extension(post_signals)
    segments = _build_segment_breakdowns(post_signals)
    data_quality = _build_data_quality(post_signals)
    interpretation = _build_interpretation(
        current_policy, single_tp, delayed_be, post_tp1_ext, segments, data_quality,
    )

    # Denominator note
    sent_count = sum(1 for r in post_signals if _is_true(r.get("sent_to_telegram")))
    candidates_count = sum(1 for f in facts if f.get("record_type") == "candidate"
                          and str(f.get("created_at") or "") >= POST_CHANGE_CUTOFF)

    digest = {
        "schema_version": F5_T14_SCHEMA_VERSION,
        "generated_at": window_end_text or "unknown",
        "section": "f5_t14_tp_policy_simulation",
        "read_only": True,
        "mode": "shadow_observational_only",
        "purpose": "Simulate alternative TP policies using closed historical signals and MFE/MAE data.",
        "post_change_cutoff_col": "2026-07-01 10:00:00",
        "post_change_cutoff_utc": POST_CHANGE_CUTOFF,
        "denominator_note": (
            "sent_to_telegram is the primary denominator. "
            f"Post-change: {sent_count} sent, {candidates_count} candidates. "
            "Candidate snapshots are NOT trades. Only sent signals with closed_at are used for simulation."
        ),
        "data_quality": data_quality,
        "sections": {
            "A_current_policy": current_policy,
            "B_tp1_hit_analysis": tp1_hit_analysis,
            "C_single_tp_simulations": single_tp,
            "D_delayed_be_simulations": delayed_be,
            "E_post_tp1_extension_distribution": post_tp1_ext,
            "F_segment_breakdowns": segments,
            "G_interpretation": interpretation,
        },
        "guardrails": [
            "Do not recommend real trading or live operation changes from this digest.",
            "Do not propose automatic TP/SL, BE timing, or guard changes.",
            "Candidate snapshots and shadow diagnostics are not official trades.",
            "sent_to_telegram is the primary denominator.",
            "Single-window simulations are weak evidence. Require multi-window comparison.",
            "MFE/MAE data may be incomplete — check data_quality before making decisions.",
        ],
    }

    # Enforce size limit
    json_str = json.dumps(digest, ensure_ascii=False, default=str)
    if len(json_str) > MAX_DIGEST_CHARS:
        for key in ("C_single_tp_simulations", "D_delayed_be_simulations", "F_segment_breakdowns"):
            if key in digest.get("sections", {}):
                digest["sections"][key] = _limit(digest["sections"][key], max_items=5, depth=2)
        json_str = json.dumps(digest, ensure_ascii=False, default=str)

    if len(json_str) > MAX_DIGEST_CHARS:
        segs = _as_dict(digest.get("sections", {}).get("F_segment_breakdowns", {}))
        for sub_key in ("by_symbol_top10_wider_friendly", "by_symbol_top10_wider_hostile"):
            items = _as_list(segs.get(sub_key, []))
            if len(items) > 5:
                segs[sub_key] = items[:5]
        digest["sections"]["F_segment_breakdowns"] = segs
        json_str = json.dumps(digest, ensure_ascii=False, default=str)

    return {"json": digest, "markdown": _render_md(digest)}


# ---------------------------------------------------------------------------
# Markdown renderer
# ---------------------------------------------------------------------------


def _render_md(digest: dict[str, Any]) -> str:
    lines = [
        "## F5_T14 — TP Policy Simulation",
        "",
        "**Ventana:**",
        f"  2026-07-01 10:00 COL → {digest.get('report_window', {}).get('end', digest.get('generated_at', 'end of report window'))}",
        "",
        "**Modo:** read-only / AI-ready / shadow-observational-only",
        "",
    ]

    if not digest.get("post_change_data_available", True):
        lines.append("**⚠️ No post-change data available in this report window.**")
        lines.append("")
        lines.append(f"Cutoff: {digest.get('post_change_cutoff_utc', 'N/A')} UTC / {digest.get('post_change_cutoff_col', 'N/A')} COL.")
        lines.append("Nothing to simulate.")
        return "\n".join(lines)

    sections = _as_dict(digest.get("sections", {}))

    # Data quality
    dq = digest.get("data_quality", {})
    lines.append("### Data Quality")
    lines.append(f"- MFE known: {dq.get('mfe_known_count')}/{dq.get('total_closed')}")
    lines.append(f"- MAE known: {dq.get('mae_known_count')}/{dq.get('total_closed')}")
    lines.append(f"- TP1 geometry known: {dq.get('tp1_r_known_count')}/{dq.get('total_closed')}")
    lines.append(f"- Post-TP1 extension known: {dq.get('tp1_post_extension_known_count')}")
    lines.append(f"- Insufficient geometry: {dq.get('insufficient_geometry_count')}")
    lines.append(f"- Approximation used: {dq.get('approximation_used')}")
    lines.append(f"- Confidence: {dq.get('confidence')}")
    for lim in _as_list(dq.get("limitations", [])):
        lines.append(f"  - {lim}")
    lines.append("")

    # A: Current Policy
    current = _as_dict(sections.get("A_current_policy", {}))
    cm = current.get("metrics", {})
    lines.append("### A. Current Policy (TP1 Protected + Runner + BE)")
    lines.append(f"- Sent: {current.get('total_sent')} | Closed: {current.get('total_closed')} | Pending: {current.get('pending_or_active')}")
    lines.append(f"- Wins: {cm.get('wins')} | Losses: {cm.get('losses')} | BE: {cm.get('breakeven')}")
    lines.append(f"- Gross Profit: {cm.get('gross_profit_r')}R | Gross Loss: {cm.get('gross_loss_r')}R")
    lines.append(f"- Net R: {cm.get('net_r')}R | Avg R: {cm.get('avg_r')}")
    lines.append(f"- Profit Factor: {cm.get('profit_factor')} | Winrate: {cm.get('winrate')}")
    exit_counts = current.get("exit_reason_counts", {})
    lines.append(f"- Exit reasons: TP1={exit_counts.get('primary_tp_hit')} SL={exit_counts.get('real_stop_loss_hit')} BE={exit_counts.get('breakeven_stop_hit')} RunnerBE={exit_counts.get('runner_breakeven_stop_hit')} TimeStop={exit_counts.get('time_stop_exit')} NoProg={exit_counts.get('no_progress_exit')} MFEStall={exit_counts.get('mfe_stall_exit')}")
    lines.append("")

    # B: TP1 Hit Analysis
    tp1 = _as_dict(sections.get("B_tp1_hit_analysis", {}))
    lines.append("### B. TP1 Hit Analysis")
    if tp1.get("available"):
        lines.append(f"- TP1 hits: {tp1.get('count_tp1_hit')}")
        lines.append(f"- Avg TP1 R: {tp1.get('avg_tp1_r')}")
        lines.append(f"- Avg MFE R: {tp1.get('avg_mfe_r')} | Max MFE R: {tp1.get('max_mfe_r')}")
        lines.append(f"- Avg Final R: {tp1.get('avg_final_r')} | Net R: {tp1.get('final_net_r')}R")
        lines.append(f"- Only TP1 (no extension): {tp1.get('reached_tp1_only_no_extension')}")
        lines.append(f"- Extended beyond TP1: {tp1.get('reached_tp1_and_extended')}")
        lines.append(f"- Avg extension: {tp1.get('avg_extension_r_beyond_tp1')}R | Median: {tp1.get('median_extension_r_beyond_tp1')}R")
        lines.append(f"- Unknown extension: {tp1.get('extension_unknown')}")
    else:
        lines.append(f"- {tp1.get('note', 'No TP1 hits')}")
    lines.append("")

    # C: Single TP Simulations
    single = _as_dict(sections.get("C_single_tp_simulations", {}))
    lines.append("### C. Single TP Simulations (vs Current Policy)")
    sims = _as_list(single.get("simulations", []))
    if sims:
        current_pf = cm.get("profit_factor")
        current_nr = cm.get("net_r")
        lines.append(f"| Multiplier | PF | Net R | Avg R | Wins | Losses | BE | Would Hit Wider | Would Miss |")
        lines.append(f"|---|---:|---:|---:|---:|---:|---:|---:|---:|")
        lines.append(f"| **Current** | {current_pf} | {current_nr}R | {cm.get('avg_r')} | {cm.get('wins')} | {cm.get('losses')} | {cm.get('breakeven')} | — | — |")
        for s in sims:
            lines.append(
                f"| {s.get('multiplier')}x | {s.get('profit_factor')} | {s.get('net_r')}R | {s.get('avg_r')} | "
                f"{s.get('wins')} | {s.get('losses')} | {s.get('breakeven')} | "
                f"{s.get('tp1_hit_would_reach_wider_tp')} | {s.get('tp1_hit_would_miss_wider_tp')} |"
            )
        if single.get("approximation_note"):
            lines.append(f"")
            lines.append(f"⚠️ {single.get('approximation_note')}")
    else:
        lines.append("No simulations available.")
    lines.append("")

    # D: Delayed BE
    be_data = _as_dict(sections.get("D_delayed_be_simulations", {}))
    lines.append("### D. Delayed BE Simulations")
    be_sims = _as_list(be_data.get("simulations", []))
    if be_sims:
        lines.append(f"| Rule | PF | Net R | Avg R | Wins | Losses | BE | Saved | Flipped |")
        lines.append(f"|---|---:|---:|---:|---:|---:|---:|---:|")
        for s in be_sims:
            lines.append(
                f"| {s.get('label')} | {s.get('profit_factor')} | {s.get('net_r')}R | {s.get('avg_r')} | "
                f"{s.get('wins')} | {s.get('losses')} | {s.get('breakeven')} | "
                f"{s.get('saved_from_loss')} | {s.get('flipped_from_loss_to_profit')} |"
            )
    else:
        lines.append("No simulations available.")
    lines.append("")

    # E: Post-TP1 Extension
    ext = _as_dict(sections.get("E_post_tp1_extension_distribution", {}))
    lines.append("### E. Post-TP1 Extension Distribution")
    if ext.get("available"):
        lines.append(f"- TP1 hits with MFE known: {ext.get('mfe_known_for_extension')}/{ext.get('count_tp1_hit')}")
        for b in _as_list(ext.get("buckets", [])):
            lines.append(f"  - {b.get('bucket')} [{b.get('range_low_r')} — {b.get('range_high_r')}R]: {b.get('count')}")
    else:
        lines.append(f"- {ext.get('note', 'No TP1 hits')}")
    lines.append("")

    # F: Segment Breakdowns
    seg = _as_dict(sections.get("F_segment_breakdowns", {}))
    lines.append("### F. Segment Breakdowns")

    wider_friendly = _as_list(seg.get("by_symbol_top10_wider_friendly", []))[:5]
    if wider_friendly:
        lines.append("")
        lines.append("**Top 5 symbols where wider TP may help:**")
        for s in wider_friendly:
            lines.append(f"  - {s.get('symbol')}: tp1_hits={s.get('tp1_hits')} avg_ext={s.get('avg_extension_r')}R net_r={s.get('net_r')}R {'⚠️ WATCH' if s.get('watch') else ''}")

    wider_hostile = _as_list(seg.get("by_symbol_top10_wider_hostile", []))[:5]
    if wider_hostile:
        lines.append("")
        lines.append("**Top 5 symbols that need fast TP:**")
        for s in wider_hostile:
            lines.append(f"  - {s.get('symbol')}: tp1_hits={s.get('tp1_hits')} avg_ext={s.get('avg_extension_r')}R net_r={s.get('net_r')}R {'⚠️ WATCH' if s.get('watch') else ''}")

    direction = _as_dict(seg.get("by_direction", {}))
    if direction:
        lines.append("")
        lines.append("**By Direction:**")
        for side in ("LONG", "SHORT"):
            d = _as_dict(direction.get(side, {}))
            if d:
                lines.append(f"  - {side}: count={d.get('count')} net_r={d.get('net_r')}R PF={d.get('profit_factor')} avg_ext={d.get('avg_extension_r_beyond_tp1')}R tolerates_wider={d.get('tolerates_wider_tp_evidence')}")

    kz = _as_dict(seg.get("by_killzone", {}))
    if kz:
        lines.append("")
        lines.append("**By Killzone:**")
        for zone, zd in kz.items():
            zd_dict = _as_dict(zd)
            lines.append(f"  - {zone}: count={zd_dict.get('count')} net_r={zd_dict.get('net_r')}R PF={zd_dict.get('profit_factor')} avg_ext={zd_dict.get('avg_extension_r')}R")
    lines.append("")

    # G: Interpretation
    interp = _as_dict(sections.get("G_interpretation", {}))
    lines.append("### G. Interpretation & Recommendation")
    for i in _as_list(interp.get("interpretations", [])):
        lines.append(f"- {i}")
    lines.append(f"")
    lines.append(f"**Recommendation:** {interp.get('recommendation', 'OBSERVE_ONLY')}")
    lines.append("")

    lines.append("---")
    lines.append("**Guardrails:**")
    for g in _as_list(digest.get("guardrails", [])):
        lines.append(f"- {g}")
    lines.append("")

    return "\n".join(lines)