from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _num(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except Exception:
        return None


def _evidence_strength(sample_size: int, effect: float | None = None) -> str:
    if sample_size < 10:
        return "weak_small_sample"
    if sample_size < 30:
        return "moderate" if effect is not None and abs(effect) >= 0.2 else "weak"
    return "strong" if effect is not None and abs(effect) >= 0.2 else "moderate"


def _hypothesis(hid: str, title: str, evidence: str, strength: str, suggested_action: str, metrics_to_monitor: list[str], status: str = "proposed") -> dict[str, Any]:
    return {
        "hypothesis_id": hid,
        "title": title,
        "evidence_summary": evidence,
        "evidence_strength": strength,
        "suggested_action": suggested_action,
        "metrics_to_monitor": metrics_to_monitor,
        "status": status,
        "approved": False,
        "mode": "shadow_observational_only",
    }


def build_strategy_hypotheses(
    lifecycle: dict[str, Any],
    blocked_summary: dict[str, Any],
    winners_losers_rows: list[dict[str, Any]],
    near_misses: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    hypotheses: list[dict[str, Any]] = []
    hid = 1

    def add(title: str, evidence: str, strength: str, action: str, metrics: list[str]) -> None:
        nonlocal hid
        hypotheses.append(_hypothesis(
            hid=f"HYP_{hid:03d}",
            title=title,
            evidence=evidence,
            strength=strength,
            suggested_action=action,
            metrics_to_monitor=metrics,
        ))
        hid += 1

    sent = int(lifecycle.get("sent_to_telegram") or 0)
    primary_tp = int(lifecycle.get("primary_tp_hit") or 0)
    real_sl = int(lifecycle.get("real_stop_loss_hit") or 0)
    no_progress = int(lifecycle.get("no_progress_exit") or 0)
    data_gaps = int(lifecycle.get("data_gap_events") or 0)
    near_count = int(blocked_summary.get("near_miss_total") or 0)
    would_send = int(blocked_summary.get("would_send_total") or 0)

    if sent > 0:
        wr = primary_tp / max(1, sent)
        add(
            "Official TP rate over sent signals needs monitoring",
            f"Sent={sent}, official primary TP={primary_tp}, real stop loss={real_sl}, primary_tp/sent={wr:.3f}.",
            _evidence_strength(sent, wr - 0.5),
            "Do not change thresholds automatically. Monitor 48-72h and compare by regime/side before proposing tuning.",
            ["sent_to_telegram", "primary_tp_hit", "real_stop_loss_hit", "official_win_rate_tp_vs_sl"],
        )

    if no_progress >= max(3, sent // 4):
        add(
            "No-progress exits may be a key lifecycle bottleneck",
            f"no_progress_exit={no_progress} versus sent={sent}.",
            _evidence_strength(sent, no_progress / max(1, sent)),
            "Review no-progress timing and pre-entry/early invalidation evidence in shadow only; do not tighten cancellation yet without 3-day confirmation.",
            ["no_progress_exit", "avg_time_to_close_minutes", "mfe", "mae"],
        )

    if data_gaps > 0:
        add(
            "Data gaps can contaminate outcome interpretation",
            f"data_gap_events={data_gaps}. Treat strategy conclusions as lower confidence when data gaps cluster around active signals.",
            "moderate" if data_gaps >= 50 else "weak",
            "Add data quality warnings to AI Review Pack and separate strategy losses from data-quality uncertainty.",
            ["data_gap_events", "signals_with_data_gap", "outcomes_after_data_gap"],
        )

    top_reasons = blocked_summary.get("top_blocked_reasons", {}) or {}
    for reason in ["rvol_low", "atr_extension_high", "ofa_shadow_reclaim_blocked", "ofa_shadow_sweep_blocked", "recent_stop_loss"]:
        count = int(top_reasons.get(reason) or 0)
        if count > 0:
            add(
                f"Blocked reason concentration: {reason}",
                f"{reason} blocked {count} candidates in the daily window.",
                _evidence_strength(count, 0.3),
                "Keep filter unchanged for now. Compare with near-miss MFE/MAE and would_send candidates before any threshold experiment.",
                ["blocked_reason_count", "near_miss_by_reason", "hypothetical_result", "mfe", "mae"],
            )

    interesting_features = {"estimated_cost", "primary_tp_distance", "sl_distance", "data_gap_events", "rvol", "time_to_entry_minutes"}
    for row in winners_losers_rows:
        feature = str(row.get("feature"))
        if feature not in interesting_features:
            continue
        diff = _num(row.get("difference_winner_minus_loser"))
        ws = int(row.get("winner_sample") or 0)
        ls = int(row.get("loser_sample") or 0)
        sample = ws + ls
        if diff is not None and sample > 0:
            add(
                f"Winners vs losers separation: {feature}",
                f"winner_value={row.get('winner_value')}, loser_value={row.get('loser_value')}, diff={diff}, samples W/L={ws}/{ls}.",
                _evidence_strength(sample, diff),
                "Use as diagnostic only until sample grows. If repeated for 3 days, consider a shadow-only calibration hypothesis.",
                [feature, "primary_tp_hit", "real_stop_loss_hit"],
            )

    if near_count > 0 or would_send > 0:
        add(
            "Near-miss pool is large enough for manual AI review",
            f"near_miss_total={near_count}, would_send_total={would_send}, selected_near_misses={len(near_misses)}.",
            _evidence_strength(near_count + would_send, 0.3),
            "Review top near-misses in AI pack. Do not unlock filters; use them to design small shadow experiments only.",
            ["near_miss_rank_score", "blocked_reason", "mfe", "mae", "would_send_signal"],
        )

    return hypotheses


def write_strategy_hypotheses(hypotheses: list[dict[str, Any]], path: str | Path) -> None:
    payload = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "proposed_only_no_auto_changes",
        "hypotheses": hypotheses,
    }
    selected = Path(path)
    selected.parent.mkdir(parents=True, exist_ok=True)
    selected.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
