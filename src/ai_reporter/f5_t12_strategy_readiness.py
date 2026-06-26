"""F5_T12 Strategy Change Readiness Digest.

Compact AI-ready digest that summarizes whether Bot F5_T12 changes are justified.
Read-only/dashboard-local analytics only. Does not read/write the BotVIP DB,
send Telegram, modify strategy, thresholds, scanner runtime, lifecycle runtime,
Telegram runtime, TP/SL, DB schema, allowlist, or real trading.

Reuses already-generated JSON sections 13-18 and 28 digest; does not recalculate
everything from scratch. All outputs are < 95,000 characters.

Inputs (already computed by daily_ai_report.py):
  - daily_facts / lifecycle (for denominators)
  - profit_factor_diagnostics (f5_t04bcd / t02)
  - loss_contribution (f5_t04e)
  - no_progress_root_cause_v3 (f5_t09bc)
  - guard_shadow_outcome_matrix (f5_t09dfghi)
  - lifecycle_reconciliation (f5_t09a)
  - f5_t09_super_digest (f5_t10)

Output:
  - 29_f5_t12_strategy_change_readiness.json (compact JSON, parseable)
  - 29_f5_t12_strategy_change_readiness.md (compact Markdown, human-readable)
  - Both < 95,000 characters
"""
from __future__ import annotations

import json
from typing import Any

F5_T12_READINESS_JSON_FILENAME = "29_f5_t12_strategy_change_readiness.json"
F5_T12_READINESS_MD_FILENAME = "29_f5_t12_strategy_change_readiness.md"
F5_T12_READINESS_SCHEMA_VERSION = "f5_t12_strategy_change_readiness_v1"
MAX_DIGEST_CHARS = 95000
MAX_TOP_LOSSES = 5
MAX_GUARDS = 10
MAX_SYMBOLS = 10


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


def _json_size(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False, default=str))


def _limit(value: Any, *, max_items: int = 8, depth: int = 2) -> Any:
    """Recursively limit list/dict sizes to keep output compact."""
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


# ---------------------------------------------------------------------------
# Section builders
# ---------------------------------------------------------------------------


def _build_denominators(
    lifecycle: dict[str, Any],
    facts: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Extract official denominators from lifecycle metrics."""
    signals_total = lifecycle.get("signals_total", 0)
    sent_to_telegram = lifecycle.get("sent_to_telegram", 0)
    candidates_total = lifecycle.get("candidates_total", 0)
    events_total = lifecycle.get("events_total", 0)
    facts_total = len(facts) if facts else lifecycle.get("facts_total", 0)

    return {
        "official_signals": signals_total,
        "sent_to_telegram": sent_to_telegram,
        "candidates": candidates_total,
        "events": events_total,
        "facts": facts_total,
        "note": "Denominators from lifecycle reconciliation. Do not double-count derived rows.",
    }


def _build_pf_core(t02_diagnostics: dict[str, Any]) -> dict[str, Any]:
    """Extract core Profit Factor metrics from T02 diagnostics."""
    pf_data = _as_dict(t02_diagnostics.get("profit_factor_diagnostics", t02_diagnostics))

    # Try to find PF stats at common paths
    sent_signals = _safe_get(pf_data, ["sent_signals", "profit_factor_stats"], {})
    all_signals = _safe_get(pf_data, ["all_signals", "profit_factor_stats"], {})

    return {
        "sent_only": {
            "count": sent_signals.get("count", 0),
            "gross_profit_r": sent_signals.get("gross_win_r", 0),
            "gross_loss_r": sent_signals.get("gross_loss_abs_r", 0),
            "avg_r": sent_signals.get("avg_r"),
            "profit_factor": sent_signals.get("profit_factor"),
            "confidence": sent_signals.get("confidence", "LOW"),
        },
        "all_signals": {
            "count": all_signals.get("count", 0),
            "gross_profit_r": all_signals.get("gross_win_r", 0),
            "gross_loss_r": all_signals.get("gross_loss_abs_r", 0),
            "avg_r": all_signals.get("avg_r"),
            "profit_factor": all_signals.get("profit_factor"),
            "confidence": all_signals.get("confidence", "LOW"),
        },
        "note": "PF core from T02 diagnostics. sent_only = signals sent to Telegram. all_signals = all official signals.",
    }


def _build_loss_top(loss_contribution: dict[str, Any]) -> dict[str, Any]:
    """Extract top 5 loss contributors by outcome, symbol, side, zone."""
    by_dimension = _as_dict(loss_contribution.get("by_dimension", {}))
    top_losses: dict[str, list[dict[str, Any]]] = {}

    for dim in ("outcome", "symbol", "side", "zone"):
        dim_data = _as_dict(by_dimension.get(dim, {}))
        top_segments = _as_list(dim_data.get("top_loss_segments", []))
        top_losses[dim] = [
            {
                "segment": seg.get("segment"),
                "count": seg.get("count"),
                "gross_loss_abs_r": seg.get("gross_loss_abs_r"),
                "avg_r": seg.get("avg_r"),
                "loss_contribution_pct": seg.get("loss_contribution_pct"),
            }
            for seg in top_segments[:MAX_TOP_LOSSES]
        ]

    return {
        "total_loss_abs_r": loss_contribution.get("total_loss_abs_r"),
        "total_net_r": loss_contribution.get("total_net_r"),
        "official_signal_denominator": loss_contribution.get("official_signal_denominator"),
        "top_by_dimension": top_losses,
        "note": "Top loss contributors by dimension. Use for diagnostic prioritization only.",
    }


def _build_no_progress_core(no_progress_v3: dict[str, Any]) -> dict[str, Any]:
    """Extract no-progress core metrics from F5_T09b no_progress_root_cause_v3."""
    segments = _as_dict(no_progress_v3.get("segments", {}))
    by_symbol = _as_dict(segments.get("by_symbol", {}))

    # Top symbols by count
    ranked_symbols = sorted(
        by_symbol.items(),
        key=lambda item: _safe_get(item[1], ["sample_size"], _safe_get(item[1], ["rows"], 0)) or 0,
        reverse=True,
    )

    return {
        "official_signal_denominator": no_progress_v3.get("official_signal_denominator"),
        "official_no_progress_count": no_progress_v3.get("official_no_progress_count"),
        "bucket_counts": _limit(no_progress_v3.get("bucket_counts", {}), max_items=12, depth=2),
        "avg_r": no_progress_v3.get("avg_r"),
        "top_symbols": [
            {
                "symbol": sym,
                "count": _safe_get(data, ["sample_size"], _safe_get(data, ["rows"], 0)),
                "avg_r": data.get("avg_r"),
                "net_sum_r": data.get("net_sum_r"),
            }
            for sym, data in ranked_symbols[:MAX_SYMBOLS]
        ],
        "note": "No-progress root cause core metrics. Full breakdown in 20_no_progress_root_cause_v3.json.",
    }


def _build_risk_context_candidates(
    guard_matrix: dict[str, Any],
    lifecycle_reconciliation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Count cases that would have been blocked by F5_T12 Risk Context Gate rules."""
    matched_guard_rows = guard_matrix.get("matched_guard_rows", 0)
    candidate_denominator = guard_matrix.get("candidate_shadow_denominator", 0)

    # Extract guard-level summaries
    matrix = _as_dict(guard_matrix.get("matrix_by_guard", {}))
    guard_summaries: list[dict[str, Any]] = []
    for guard_name, guard_data in sorted(matrix.items()):
        guard_d = _as_dict(guard_data)
        guard_summaries.append({
            "guard": guard_name,
            "rows": guard_d.get("rows", 0),
            "avoided_losses_r": guard_d.get("avoided_losses_r"),
            "missed_winners_r": guard_d.get("missed_winners_r"),
            "net_guard_value_r": guard_d.get("net_guard_value_r"),
        })

    return {
        "candidate_shadow_denominator": candidate_denominator,
        "matched_guard_rows": matched_guard_rows,
        "guard_summaries": guard_summaries[:MAX_GUARDS],
        "note": "Risk Context Gate candidates that would have been blocked. Observational only.",
    }


def _build_guard_value(guard_matrix: dict[str, Any]) -> dict[str, Any]:
    """Extract top guards by net_guard_value (positive = avoided losses > missed winners)."""
    matrix = _as_dict(guard_matrix.get("matrix_by_guard", {}))
    guard_values: list[dict[str, Any]] = []

    for guard_name, guard_data in matrix.items():
        guard_d = _as_dict(guard_data)
        net_value = _num(guard_d.get("net_guard_value_r"))
        if net_value is not None:
            guard_values.append({
                "guard": guard_name,
                "rows": guard_d.get("rows", 0),
                "avoided_losses_r": guard_d.get("avoided_losses_r"),
                "missed_winners_r": guard_d.get("missed_winners_r"),
                "net_guard_value_r": net_value,
            })

    # Sort: positive net value first (avoided losses > missed winners)
    guard_values.sort(key=lambda g: -(g["net_guard_value_r"]))
    positive = [g for g in guard_values if g["net_guard_value_r"] > 0][:MAX_GUARDS]
    negative = [g for g in guard_values if g["net_guard_value_r"] < 0][:MAX_GUARDS]

    return {
        "positive_net_value": positive,
        "negative_net_value": negative,
        "note": "Positive net_guard_value = avoided losses exceed missed winners. Negative = guard may be too restrictive.",
    }


def _build_data_quality(
    t02_diagnostics: dict[str, Any],
    no_progress_v3: dict[str, Any],
    loss_contribution: dict[str, Any],
) -> dict[str, Any]:
    """Extract data quality indicators: MFE/MAE known rates, data gaps, confidence warnings."""
    # MFE/MAE known from no_progress_v3
    mfe_known = no_progress_v3.get("mfe_known_count")
    mae_known = no_progress_v3.get("mae_known_count")
    official_count = no_progress_v3.get("official_signal_denominator", 0)

    # Data gaps from loss_contribution
    by_dim = _as_dict(loss_contribution.get("by_dimension", {}))
    data_gap_dim = _as_dict(by_dim.get("data_gap_bucket", {}))
    data_gap_segments = _as_list(data_gap_dim.get("segments", []))

    # Confidence from T02
    t02_confidence = t02_diagnostics.get("confidence", t02_diagnostics.get("data_quality_score_by_signal", {}))

    warnings: list[str] = []
    if official_count > 0 and mfe_known is not None and mfe_known < official_count * 0.5:
        warnings.append(f"MFE known rate is low ({mfe_known}/{official_count}). MFE-based analysis may be unreliable.")
    if official_count > 0 and mae_known is not None and mae_known < official_count * 0.5:
        warnings.append(f"MAE known rate is low ({mae_known}/{official_count}). MAE-based analysis may be unreliable.")

    return {
        "mfe_known": mfe_known,
        "mae_known": mae_known,
        "official_signal_denominator": official_count,
        "data_gap_buckets": _limit(data_gap_segments, max_items=6, depth=2),
        "confidence_warnings": warnings,
        "note": "Data quality indicators. Low MFE/MAE known rates reduce confidence in derived metrics.",
    }


def _build_human_checklist() -> dict[str, Any]:
    """Generate a human-readable checklist for deploy/flag change review."""
    return {
        "checklist": [
            {
                "id": "C01",
                "category": "denominators",
                "check": "Verify official signal count matches expected window volume.",
                "why": "Low volume may produce unreliable PF and loss contribution metrics.",
            },
            {
                "id": "C02",
                "category": "profit_factor",
                "check": "Confirm PF > 1.0 for sent signals. If PF < 1.0, review loss contribution first.",
                "why": "Negative PF indicates the current strategy is losing R over the window.",
            },
            {
                "id": "C03",
                "category": "loss_contribution",
                "check": "Review top loss contributors by outcome and symbol. Are losses concentrated?",
                "why": "Concentrated losses may indicate a systemic issue rather than random variance.",
            },
            {
                "id": "C04",
                "category": "no_progress",
                "check": "Check no-progress rate and top symbols. Is no-progress concentrated?",
                "why": "High no-progress rate may indicate entry timing or filter issues.",
            },
            {
                "id": "C05",
                "category": "risk_context_gate",
                "check": "Review guard net values. Are any guards causing more missed winners than avoided losses?",
                "why": "Negative net guard value suggests the guard may be too restrictive.",
            },
            {
                "id": "C06",
                "category": "data_quality",
                "check": "Check MFE/MAE known rates. Are they above 50%?",
                "why": "Low known rates reduce confidence in MFE/MAE-based diagnostics.",
            },
            {
                "id": "C07",
                "category": "data_quality",
                "check": "Review data gap buckets. Are there signals with >5 data gap events?",
                "why": "High data gaps may indicate connectivity or logging issues.",
            },
            {
                "id": "C08",
                "category": "deploy_readiness",
                "check": "Compare 24h before/after metrics if available. Is the change justified?",
                "why": "Single-window metrics are weak evidence. Multi-window comparison strengthens the case.",
            },
            {
                "id": "C09",
                "category": "deploy_readiness",
                "check": "Confirm all JSON files in ZIP parse correctly and are under 95,000 characters.",
                "why": "Oversized files may be truncated by Telegram or AI context limits.",
            },
            {
                "id": "C10",
                "category": "deploy_readiness",
                "check": "Verify dashboard is read-only. No bot DB writes, no runtime changes.",
                "why": "Dashboard must never modify bot runtime or strategy.",
            },
        ],
        "note": "Review this checklist before deploying F5_T12 changes or flipping flags. Not all items need to pass, but each should be consciously evaluated.",
    }


# ---------------------------------------------------------------------------
# Main builder
# ---------------------------------------------------------------------------


def build_f5_t12_strategy_readiness(
    *,
    lifecycle: dict[str, Any],
    facts: list[dict[str, Any]] | None = None,
    t02_diagnostics: dict[str, Any],
    loss_contribution: dict[str, Any],
    no_progress_v3: dict[str, Any],
    guard_matrix: dict[str, Any],
    lifecycle_reconciliation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the F5_T12 Strategy Change Readiness digest.

    Args:
        lifecycle: Lifecycle metrics dict (from compute_lifecycle_metrics).
        facts: Daily facts list (optional, for denominator count).
        t02_diagnostics: T02 diagnostics dict (for PF core).
        loss_contribution: Loss contribution dict (from f5_t04e).
        no_progress_v3: No-progress root cause v3 dict (from f5_t09bc).
        guard_matrix: Guard shadow outcome matrix dict (from f5_t09dfghi).
        lifecycle_reconciliation: Lifecycle reconciliation dict (from f5_t09a), optional.

    Returns:
        Dict with 'json' and 'markdown' keys.
    """
    digest = {
        "schema_version": F5_T12_READINESS_SCHEMA_VERSION,
        "section": "f5_t12_strategy_change_readiness",
        "read_only": True,
        "mode": "shadow_observational_only",
        "purpose": "Compact digest to determine if F5_T12 Bot changes are justified. Reuses already-generated JSON sections.",
        "sections": {
            "denominators": _build_denominators(lifecycle, facts),
            "pf_core": _build_pf_core(t02_diagnostics),
            "loss_top": _build_loss_top(loss_contribution),
            "no_progress_core": _build_no_progress_core(no_progress_v3),
            "risk_context_candidates": _build_risk_context_candidates(guard_matrix, lifecycle_reconciliation),
            "guard_value": _build_guard_value(guard_matrix),
            "data_quality": _build_data_quality(t02_diagnostics, no_progress_v3, loss_contribution),
            "human_checklist": _build_human_checklist(),
        },
        "guardrails": [
            "Do not recommend real trading or live operation changes from this digest.",
            "Do not propose automatic threshold, TP/SL, no-progress timeout, MFE-stall, guard, or allowlist changes.",
            "Candidate snapshots and shadow diagnostics are not official trades.",
            "Single-window metrics are weak evidence. Require multi-window comparison before deploying changes.",
            "Full JSON evidence remains on the server report folder; request only targeted full sections if needed.",
        ],
    }

    # Enforce size limit
    json_str = json.dumps(digest, ensure_ascii=False, default=str)
    if len(json_str) > MAX_DIGEST_CHARS:
        # Aggressively truncate large sections
        for section_key in ("loss_top", "no_progress_core", "guard_value", "data_quality"):
            if section_key in digest.get("sections", {}):
                digest["sections"][section_key] = _limit(
                    digest["sections"][section_key],
                    max_items=4,
                    depth=2,
                )
        json_str = json.dumps(digest, ensure_ascii=False, default=str)

    # If still too large, truncate the human_checklist
    if len(json_str) > MAX_DIGEST_CHARS:
        digest["sections"]["human_checklist"] = _limit(
            digest["sections"]["human_checklist"],
            max_items=5,
            depth=2,
        )
        json_str = json.dumps(digest, ensure_ascii=False, default=str)

    return {"json": digest, "markdown": _render_md(digest)}


# ---------------------------------------------------------------------------
# Markdown renderer
# ---------------------------------------------------------------------------


def _render_md(digest: dict[str, Any]) -> str:
    """Render the digest as a compact Markdown document."""
    lines = [
        "# F5_T12 Strategy Change Readiness",
        "",
        "Compact digest to determine if F5_T12 Bot changes are justified.",
        "Read-only observational analysis. Does not modify bot runtime.",
        "",
        "---",
        "",
        "## Denominators",
    ]

    denom = _safe_get(digest, ["sections", "denominators"], {})
    lines.append(f"- Official signals: {denom.get('official_signals')}")
    lines.append(f"- Sent to Telegram: {denom.get('sent_to_telegram')}")
    lines.append(f"- Candidates: {denom.get('candidates')}")
    lines.append(f"- Events: {denom.get('events')}")
    lines.append(f"- Facts: {denom.get('facts')}")

    lines.extend(["", "## PF Core (sent signals)"])
    pf = _safe_get(digest, ["sections", "pf_core", "sent_only"], {})
    lines.append(f"- Count: {pf.get('count')}")
    lines.append(f"- Gross profit R: {pf.get('gross_profit_r')}")
    lines.append(f"- Gross loss R: {pf.get('gross_loss_r')}")
    lines.append(f"- Avg R: {pf.get('avg_r')}")
    lines.append(f"- Profit Factor: {pf.get('profit_factor')}")
    lines.append(f"- Confidence: {pf.get('confidence')}")

    lines.extend(["", "## Loss Top (top 5 by dimension)"])
    loss_top = _safe_get(digest, ["sections", "loss_top", "top_by_dimension"], {})
    for dim in ("outcome", "symbol", "side", "zone"):
        segments = loss_top.get(dim, [])
        if segments:
            lines.append(f"### By {dim}")
            for seg in segments[:3]:
                lines.append(
                    f"- {seg.get('segment')}: count={seg.get('count')}, "
                    f"loss={seg.get('gross_loss_abs_r')}R, "
                    f"contrib={seg.get('loss_contribution_pct')}"
                )

    lines.extend(["", "## No-Progress Core"])
    np = _safe_get(digest, ["sections", "no_progress_core"], {})
    lines.append(f"- Official no-progress count: {np.get('official_no_progress_count')}")
    lines.append(f"- Bucket counts: `{json.dumps(np.get('bucket_counts', {}), ensure_ascii=False)}`")
    top_syms = np.get("top_symbols", [])
    if top_syms:
        lines.append("- Top symbols:")
        for sym in top_syms[:5]:
            lines.append(f"  - {sym.get('symbol')}: count={sym.get('count')}, avg_r={sym.get('avg_r')}")

    lines.extend(["", "## Risk Context Candidates"])
    rc = _safe_get(digest, ["sections", "risk_context_candidates"], {})
    lines.append(f"- Candidate shadow denominator: {rc.get('candidate_shadow_denominator')}")
    lines.append(f"- Matched guard rows: {rc.get('matched_guard_rows')}")
    guards = rc.get("guard_summaries", [])
    if guards:
        lines.append("- Guard summaries:")
        for g in guards[:5]:
            lines.append(
                f"  - {g.get('guard')}: rows={g.get('rows')}, "
                f"avoided_losses={g.get('avoided_losses_r')}R, "
                f"missed_winners={g.get('missed_winners_r')}R, "
                f"net={g.get('net_guard_value_r')}R"
            )

    lines.extend(["", "## Guard Value (top positive/negative)"])
    gv = _safe_get(digest, ["sections", "guard_value"], {})
    pos = gv.get("positive_net_value", [])
    neg = gv.get("negative_net_value", [])
    if pos:
        lines.append("### Positive net value (avoided losses > missed winners)")
        for g in pos[:3]:
            lines.append(f"  - {g.get('guard')}: net={g.get('net_guard_value_r')}R")
    if neg:
        lines.append("### Negative net value (guard may be too restrictive)")
        for g in neg[:3]:
            lines.append(f"  - {g.get('guard')}: net={g.get('net_guard_value_r')}R")

    lines.extend(["", "## Data Quality"])
    dq = _safe_get(digest, ["sections", "data_quality"], {})
    lines.append(f"- MFE known: {dq.get('mfe_known')} / {dq.get('official_signal_denominator')}")
    lines.append(f"- MAE known: {dq.get('mae_known')} / {dq.get('official_signal_denominator')}")
    warnings = dq.get("confidence_warnings", [])
    if warnings:
        lines.append("- Warnings:")
        for w in warnings:
            lines.append(f"  - {w}")

    lines.extend(["", "## Human Checklist"])
    checklist = _safe_get(digest, ["sections", "human_checklist", "checklist"], [])
    for item in checklist:
        lines.append(f"- [{item.get('id')}] {item.get('category').upper()}: {item.get('check')}")

    lines.extend([
        "",
        "---",
        "",
        "## Guardrails",
        "- Do not recommend real trading or live operation changes from this digest.",
        "- Do not propose automatic threshold, TP/SL, no-progress timeout, MFE-stall, guard, or allowlist changes.",
        "- Candidate snapshots and shadow diagnostics are not official trades.",
        "- Single-window metrics are weak evidence. Require multi-window comparison before deploying changes.",
        "- Full JSON evidence remains on the server report folder; request only targeted full sections if needed.",
        "",
    ])

    return "\n".join(lines)
