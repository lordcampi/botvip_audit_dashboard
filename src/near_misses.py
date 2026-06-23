from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

NEAR_MISS_REASONS = {
    "near_miss_chop",
    "ofa_shadow_reclaim_blocked",
    "ofa_shadow_sweep_blocked",
    "rvol_low",
    "adx_low",
    "live_guard:ofa_live_rvol_too_low",
}


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


def _near_miss_score(row: dict[str, Any]) -> float:
    score = 0.0
    if _is_true(row.get("near_miss")):
        score += 50.0
    if _is_true(row.get("would_send_signal")):
        score += 25.0
    reason = str(row.get("blocked_reason") or "")
    if reason in {"ofa_shadow_reclaim_blocked", "ofa_shadow_sweep_blocked"}:
        score += 15.0
    if reason == "near_miss_chop":
        score += 20.0
    mfe = _num(row.get("mfe"))
    mae = _num(row.get("mae"))
    if mfe is not None:
        score += min(20.0, max(0.0, mfe) * 2.0)
    if mae is not None:
        score -= min(10.0, max(0.0, mae) * 2.0)
    rvol = _num(row.get("rvol"))
    if rvol is not None and rvol >= 0.7:
        score += 5.0
    return round(score, 6)


def select_near_misses(facts: list[dict[str, Any]], limit: int = 200) -> list[dict[str, Any]]:
    candidates = [row for row in facts if row.get("record_type") == "candidate"]
    selected: list[dict[str, Any]] = []
    for row in candidates:
        reason = str(row.get("blocked_reason") or "")
        if _is_true(row.get("near_miss")) or _is_true(row.get("would_send_signal")) or reason in NEAR_MISS_REASONS:
            item = dict(row)
            item["near_miss_rank_score"] = _near_miss_score(row)
            selected.append(item)
    selected.sort(key=lambda x: (x.get("near_miss_rank_score") or 0, _num(x.get("mfe")) or 0), reverse=True)
    return selected[:limit]


def write_near_misses_csv(rows: list[dict[str, Any]], path: str | Path) -> None:
    fieldnames = [
        "near_miss_rank_score", "candidate_id", "created_at", "symbol", "side", "setup_type", "operating_mode", "market_regime",
        "blocked_reason", "reason_if_rejected", "score", "min_score", "score_margin", "adx", "rvol", "atr_extension",
        "near_miss", "would_send_signal", "hypothetical_result", "hypothetical_exit_reason", "mfe", "mae",
        "entry_price", "tp_price", "sl_price", "gross_rr", "net_rr", "estimated_cost",
        "liquidity_zone_ok", "sweep_ok", "reclaim_ok", "execution_ok", "risk_ok", "signal_complete",
    ]
    selected = Path(path)
    selected.parent.mkdir(parents=True, exist_ok=True)
    with selected.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key) for key in fieldnames})
