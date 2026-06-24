"""F5_T04e Loss Contribution and AI Insight Summary.

Read-only/dashboard-local analytics only. This module does not read/write the
BotVIP DB directly, send Telegram, change strategy, thresholds, scanner runtime,
lifecycle runtime, Telegram runtime, DB schema, or introduce real trading.
"""
from __future__ import annotations

import math
from collections import Counter, defaultdict
from typing import Any, Iterable

SCHEMA_VERSION = "f5_t04e_loss_contribution_ai_insight_v1"
LOSS_CONTRIBUTION_FILENAME = "17_loss_contribution.json"
AI_INSIGHT_SUMMARY_FILENAME = "18_ai_insight_summary.json"


def _norm(value: Any, default: str = "unknown") -> str:
    if value is None:
        return default
    text = str(value).strip()
    return text if text else default


def _lower(value: Any, default: str = "unknown") -> str:
    return _norm(value, default).lower()


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
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(parsed) or math.isinf(parsed):
        return None
    return parsed


def _avg(values: Iterable[Any]) -> float | None:
    clean = [v for v in (_num(item) for item in values) if v is not None]
    return None if not clean else round(sum(clean) / len(clean), 6)


def _outcome(row: dict[str, Any]) -> str:
    if _is_true(row.get("primary_tp_hit")):
        return "primary_tp_hit"
    if _is_true(row.get("real_stop_loss_hit")):
        return "real_stop_loss_hit"
    if _is_true(row.get("no_progress_exit")):
        return "no_progress_exit"
    if _is_true(row.get("mfe_stall_exit")):
        return "mfe_stall_exit"
    if _is_true(row.get("time_stop_exit")):
        return "time_stop_exit"
    if _is_true(row.get("breakeven_stop_hit")):
        return "breakeven_stop_hit"
    if _is_true(row.get("runner_breakeven_stop_hit")):
        return "runner_breakeven_stop_hit"
    if _is_true(row.get("cancelled")):
        return "cancelled_or_expired"
    return _lower(row.get("exit_reason"), "other_or_open")


def _resolved_zone(row: dict[str, Any]) -> str:
    for key in ("resolved_zone", "zone"):
        value = row.get(key)
        if value not in {None, ""}:
            return _lower(value)
    weekend = _is_true(row.get("weekend"))
    if weekend:
        return "weekend"
    killzone = row.get("killzone")
    if _is_true(killzone):
        return "killzone"
    if killzone is False or str(killzone).strip().lower() in {"0", "false", "no"}:
        return "outside_killzone"
    mode = _lower(row.get("operating_mode"), "")
    if "killzone" in mode or "institucional activa" in mode:
        return "killzone"
    if mode and mode not in {"unknown", "none", "null"}:
        return "outside_killzone"
    return "unknown_zone"


def _r_value(row: dict[str, Any]) -> float | None:
    for key in ("net_r", "pnl_r", "gross_r"):
        value = _num(row.get(key))
        if value is not None:
            return value
    if _is_true(row.get("real_stop_loss_hit")):
        return -1.0
    if _is_true(row.get("primary_tp_hit")):
        gross_rr = _num(row.get("gross_rr"))
        return gross_rr if gross_rr is not None else 1.0
    return None


def _segment_key(row: dict[str, Any], dimension: str) -> str:
    if dimension == "zone":
        return _resolved_zone(row)
    if dimension == "outcome":
        return _outcome(row)
    if dimension == "data_gap_bucket":
        gaps = _num(row.get("data_gap_events")) or 0
        if gaps <= 0:
            return "data_gap_0"
        if gaps <= 2:
            return "data_gap_1_to_2"
        if gaps <= 5:
            return "data_gap_3_to_5"
        return "data_gap_gt_5"
    return _norm(row.get(dimension))


def _segment_stats(rows: list[dict[str, Any]], total_loss_abs: float) -> dict[str, Any]:
    r_values = [_r_value(row) for row in rows]
    clean = [value for value in r_values if value is not None]
    losses = [value for value in clean if value < 0]
    wins = [value for value in clean if value > 0]
    loss_abs = abs(sum(losses))
    win_sum = sum(wins)
    net_sum = sum(clean)
    return {
        "count": len(rows),
        "r_values_count": len(clean),
        "loss_count": len(losses),
        "win_count": len(wins),
        "gross_loss_abs_r": round(loss_abs, 6),
        "gross_win_r": round(win_sum, 6),
        "net_sum_r": round(net_sum, 6),
        "loss_contribution_pct": round(loss_abs / total_loss_abs, 6) if total_loss_abs > 0 else None,
        "avg_r": _avg(clean),
        "avg_mfe": _avg(row.get("mfe") for row in rows),
        "avg_mae": _avg(row.get("mae") for row in rows),
        "avg_data_gap_events": _avg(row.get("data_gap_events") for row in rows),
    }


def build_loss_contribution(*, facts: list[dict[str, Any]], f5_t04bcd_sections: dict[str, Any] | None = None) -> dict[str, Any]:
    signals = [dict(row) for row in facts if row.get("record_type") == "signal"]
    for row in signals:
        row["outcome"] = _outcome(row)
        row["zone"] = _resolved_zone(row)
        row["r_value"] = _r_value(row)

    total_loss_abs = abs(sum(value for value in (row.get("r_value") for row in signals) if isinstance(value, (int, float)) and value < 0))
    dimensions = ["outcome", "exit_reason", "setup_type", "symbol", "side", "zone", "data_gap_bucket"]
    by_dimension: dict[str, dict[str, Any]] = {}

    for dimension in dimensions:
        buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in signals:
            buckets[_segment_key(row, dimension)].append(row)
        ranked = []
        for segment, rows in buckets.items():
            stats = _segment_stats(rows, total_loss_abs)
            ranked.append({"segment": segment, **stats})
        ranked.sort(key=lambda item: (item.get("gross_loss_abs_r") or 0, item.get("count") or 0), reverse=True)
        by_dimension[dimension] = {
            "segments": ranked[:50],
            "top_loss_segments": [item for item in ranked if (item.get("gross_loss_abs_r") or 0) > 0][:20],
        }

    losing_rows = [row for row in signals if isinstance(row.get("r_value"), (int, float)) and row.get("r_value") < 0]
    top_individual_losses = sorted(losing_rows, key=lambda row: abs(row.get("r_value") or 0), reverse=True)[:100]

    return {
        "schema_version": SCHEMA_VERSION,
        "section": "F5_T04e_loss_contribution",
        "read_only": True,
        "official_signal_denominator": len(signals),
        "r_value_source_order": ["net_r", "pnl_r", "gross_r", "fallback_real_sl_minus_1", "fallback_primary_tp_rr_or_1"],
        "total_loss_abs_r": round(total_loss_abs, 6),
        "total_net_r": round(sum(row.get("r_value") for row in signals if isinstance(row.get("r_value"), (int, float))), 6),
        "by_dimension": by_dimension,
        "top_individual_losses": [
            {
                "signal_id": row.get("signal_id"),
                "symbol": row.get("symbol"),
                "side": row.get("side"),
                "setup_type": row.get("setup_type"),
                "zone": row.get("zone"),
                "outcome": row.get("outcome"),
                "exit_reason": row.get("exit_reason"),
                "r_value": row.get("r_value"),
                "mfe": row.get("mfe"),
                "mae": row.get("mae"),
                "data_gap_events": row.get("data_gap_events"),
            }
            for row in top_individual_losses
        ],
        "references": {
            "batch2_sections_available": bool(f5_t04bcd_sections),
            "entity_scope_rule": "Only official signal rows are used as the trade denominator.",
        },
        "guardrails": [
            "Loss contribution is descriptive only.",
            "Do not tune thresholds from a single-day sample.",
            "Do not count candidate snapshots or diagnostic rows as trades.",
        ],
    }


def _section_counts(loss_contribution: dict[str, Any]) -> list[str]:
    highlights: list[str] = []
    by_dim = loss_contribution.get("by_dimension", {}) if isinstance(loss_contribution, dict) else {}
    for dim in ("outcome", "zone", "setup_type", "symbol"):
        data = by_dim.get(dim, {}) if isinstance(by_dim, dict) else {}
        top = (data.get("top_loss_segments") or [])[:3]
        if top:
            rendered = ", ".join(f"{item.get('segment')}={item.get('gross_loss_abs_r')}R" for item in top)
            highlights.append(f"Top loss contribution by {dim}: {rendered}.")
    return highlights


def build_ai_insight_summary(
    *,
    lifecycle: dict[str, Any],
    blocked_summary: dict[str, Any],
    t02_diagnostics: dict[str, Any],
    f5_t04bcd_sections: dict[str, Any],
    loss_contribution: dict[str, Any],
) -> dict[str, Any]:
    no_progress = f5_t04bcd_sections.get("no_progress_root_cause_diagnostics", {}) if isinstance(f5_t04bcd_sections, dict) else {}
    zone_quality = f5_t04bcd_sections.get("zone_mapping_quality", {}) if isinstance(f5_t04bcd_sections, dict) else {}
    entity_scope = f5_t04bcd_sections.get("entity_scope_reconciliation", {}) if isinstance(f5_t04bcd_sections, dict) else {}

    observations = [
        f"Official signals observed: {lifecycle.get('signals_total', 0)}.",
        f"Signals sent to Telegram: {lifecycle.get('sent_to_telegram', 0)}.",
        f"Primary TP hits: {lifecycle.get('primary_tp_hit', 0)}; real stop losses: {lifecycle.get('real_stop_loss_hit', 0)}.",
        f"No-progress exits: {lifecycle.get('no_progress_exit', 0)}.",
        f"Candidates observed: {lifecycle.get('candidates_total', 0)}; near-miss candidates: {lifecycle.get('near_miss_candidates', 0)}.",
        f"Zone unknown rate: {zone_quality.get('unknown_zone_rate')}.",
        f"No-progress classifier counts: {no_progress.get('classifier_counts', {})}.",
    ]
    observations.extend(_section_counts(loss_contribution))

    data_quality_notes: list[str] = []
    tq = t02_diagnostics.get("data_quality_score_by_signal", {}) if isinstance(t02_diagnostics, dict) else {}
    if tq:
        data_quality_notes.append(f"Data quality score counts: {tq.get('score_counts', {})}.")
    if no_progress.get("confidence_counts"):
        data_quality_notes.append(f"No-progress confidence counts: {no_progress.get('confidence_counts')}.")

    suggested_hypotheses = []
    if lifecycle.get("no_progress_exit", 0):
        suggested_hypotheses.append({
            "title": "No-progress exits require root-cause review before timing changes",
            "evidence": no_progress.get("classifier_counts", {}),
            "action": "Review classifier evidence for 48-72h before proposing any shadow-only timing hypothesis.",
            "approved": False,
        })
    if loss_contribution.get("total_loss_abs_r", 0):
        suggested_hypotheses.append({
            "title": "Loss contribution should be monitored by segment",
            "evidence": _section_counts(loss_contribution),
            "action": "Monitor top loss segments across multiple daily packs; do not tune from one day.",
            "approved": False,
        })
    if zone_quality.get("unknown_zone_rate", 0) and zone_quality.get("unknown_zone_rate", 0) > 0.25:
        suggested_hypotheses.append({
            "title": "Zone mapping quality may reduce confidence",
            "evidence": {"unknown_zone_rate": zone_quality.get("unknown_zone_rate")},
            "action": "Improve reporting-only zone evidence before interpreting zone performance.",
            "approved": False,
        })

    return {
        "schema_version": SCHEMA_VERSION,
        "section": "F5_T04e_ai_insight_summary",
        "read_only": True,
        "mode": "shadow_observational_only",
        "executive_observations": observations,
        "data_quality_notes": data_quality_notes,
        "entity_scope_summary": {
            "official_signals": entity_scope.get("entities", {}).get("official_signals", {}) if isinstance(entity_scope, dict) else {},
            "do_not_double_count": entity_scope.get("do_not_double_count", []) if isinstance(entity_scope, dict) else [],
        },
        "suggested_hypotheses": suggested_hypotheses,
        "human_review_checklist": [
            "Confirm official signal denominator before comparing performance.",
            "Separate real stop losses from breakeven, time stop, and no-progress outcomes.",
            "Use loss contribution only as diagnostic prioritization, not as auto-tuning.",
            "Check whether data gaps or missing MFE/MAE lower confidence.",
            "If deploying to Vultr, validate with real DB rows and F5_T04a ZIP char guard.",
        ],
        "guardrails": [
            "No automatic changes.",
            "No real trading.",
            "No strategy, threshold, scanner, lifecycle, Telegram runtime, or schema changes.",
            "Any future calibration must be small, reversible, measurable, and human approved.",
        ],
    }


def build_f5_t04e_outputs(
    *,
    facts: list[dict[str, Any]],
    lifecycle: dict[str, Any],
    blocked_summary: dict[str, Any],
    t02_diagnostics: dict[str, Any],
    f5_t04bcd_sections: dict[str, Any],
) -> dict[str, Any]:
    loss = build_loss_contribution(facts=facts, f5_t04bcd_sections=f5_t04bcd_sections)
    summary = build_ai_insight_summary(
        lifecycle=lifecycle,
        blocked_summary=blocked_summary,
        t02_diagnostics=t02_diagnostics,
        f5_t04bcd_sections=f5_t04bcd_sections,
        loss_contribution=loss,
    )
    return {
        "loss_contribution": loss,
        "ai_insight_summary": summary,
    }
