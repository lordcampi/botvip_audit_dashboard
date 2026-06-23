from __future__ import annotations

import csv
from collections import Counter
from pathlib import Path
from typing import Any


def _is_true(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


def candidate_facts(facts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [row for row in facts if row.get("record_type") == "candidate"]


def blocked_candidates(facts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [row for row in candidate_facts(facts) if _is_true(row.get("blocked"))]


def compute_blocked_analysis(facts: list[dict[str, Any]], top_n: int = 30) -> dict[str, Any]:
    candidates = candidate_facts(facts)
    blocked = blocked_candidates(facts)
    near_misses = [row for row in candidates if _is_true(row.get("near_miss"))]
    would_send = [row for row in candidates if _is_true(row.get("would_send_signal"))]

    def counts(key: str) -> dict[str, int]:
        counter = Counter(str(row.get(key) if row.get(key) not in {None, ""} else "unknown") for row in blocked)
        return dict(counter.most_common(top_n))

    return {
        "candidates_total": len(candidates),
        "blocked_total": len(blocked),
        "blocked_rate": round(len(blocked) / max(1, len(candidates)), 6),
        "near_miss_total": len(near_misses),
        "would_send_total": len(would_send),
        "top_blocked_reasons": counts("blocked_reason"),
        "blocked_by_symbol": counts("symbol"),
        "blocked_by_side": counts("side"),
        "blocked_by_setup_type": counts("setup_type"),
        "blocked_by_operating_mode": counts("operating_mode"),
        "near_miss_by_reason": dict(Counter(str(row.get("blocked_reason") or "unknown") for row in near_misses).most_common(top_n)),
    }


def write_blocked_candidates_csv(facts: list[dict[str, Any]], path: str | Path) -> None:
    rows = blocked_candidates(facts)
    fieldnames = [
        "candidate_id", "created_at", "symbol", "side", "setup_type", "operating_mode", "market_regime",
        "blocked_reason", "reason_if_rejected", "score", "min_score", "score_margin", "adx", "rvol", "atr_extension",
        "passed_adx_18", "passed_adx_20", "passed_adx_22", "passed_rvol_1_0", "passed_rvol_1_1", "passed_rvol_1_2",
        "near_miss", "would_send_signal", "hypothetical_result", "hypothetical_exit_reason", "mfe", "mae",
        "liquidity_zone_ok", "sweep_ok", "reclaim_ok", "execution_ok", "risk_ok", "signal_complete",
    ]
    selected = Path(path)
    selected.parent.mkdir(parents=True, exist_ok=True)
    with selected.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key) for key in fieldnames})
