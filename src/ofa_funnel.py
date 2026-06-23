from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

FUNNEL_STAGES = [
    "liquidity_zone_ok",
    "sweep_ok",
    "reclaim_ok",
    "execution_ok",
    "risk_ok",
    "signal_complete",
    "telegram_notified",
    "lifecycle_started",
]


def _bool_state(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None or value == "":
        return "unknown"
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y"}:
        return "true"
    if text in {"0", "false", "no", "n"}:
        return "false"
    return "unknown"


def compute_filter_funnel(facts: list[dict[str, Any]], record_type: str | None = None) -> list[dict[str, Any]]:
    rows = facts
    if record_type:
        rows = [row for row in facts if row.get("record_type") == record_type]
    total = len(rows)
    output: list[dict[str, Any]] = []
    previous_true = total
    for stage in FUNNEL_STAGES:
        true_count = 0
        false_count = 0
        unknown_count = 0
        for row in rows:
            state = _bool_state(row.get(stage))
            if state == "true":
                true_count += 1
            elif state == "false":
                false_count += 1
            else:
                unknown_count += 1
        known = true_count + false_count
        pass_rate_known = round(true_count / max(1, known), 6)
        pass_rate_total = round(true_count / max(1, total), 6)
        drop_from_previous_true = previous_true - true_count if previous_true is not None else None
        output.append({
            "record_type": record_type or "all",
            "stage": stage,
            "total_rows": total,
            "true_count": true_count,
            "false_count": false_count,
            "unknown_count": unknown_count,
            "pass_rate_known": pass_rate_known,
            "pass_rate_total": pass_rate_total,
            "drop_from_previous_true": drop_from_previous_true,
        })
        previous_true = true_count
    return output


def write_filter_funnel_csv(rows: list[dict[str, Any]], path: str | Path) -> None:
    fieldnames = ["record_type", "stage", "total_rows", "true_count", "false_count", "unknown_count", "pass_rate_known", "pass_rate_total", "drop_from_previous_true"]
    selected = Path(path)
    selected.parent.mkdir(parents=True, exist_ok=True)
    with selected.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key) for key in fieldnames})
