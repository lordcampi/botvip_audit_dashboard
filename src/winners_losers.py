from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

NUMERIC_FEATURES = [
    "score", "min_score", "score_margin", "rvol", "adx", "atr_extension", "gross_rr", "net_rr",
    "estimated_cost", "primary_tp_distance", "sl_distance", "time_to_entry_minutes", "time_to_close_minutes",
    "data_gap_events",
]

BOOLEAN_FEATURES = [
    "liquidity_zone_ok", "sweep_ok", "reclaim_ok", "execution_ok", "risk_ok", "signal_complete",
    "telegram_notified", "lifecycle_started", "sent_to_telegram", "breakeven_stop_hit", "time_stop_exit",
    "no_progress_exit", "mfe_stall_exit", "runner_breakeven_stop_hit",
]

CATEGORICAL_FEATURES = ["symbol", "side", "setup_type", "market_regime", "engine_name", "exit_reason", "status"]


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


def _rate(rows: list[dict[str, Any]], feature: str) -> float | None:
    if not rows:
        return None
    return round(sum(1 for row in rows if _is_true(row.get(feature))) / len(rows), 6)


def compare_winners_losers(facts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    signals = [row for row in facts if row.get("record_type") == "signal"]
    winners = [row for row in signals if _is_true(row.get("primary_tp_hit"))]
    losers = [row for row in signals if _is_true(row.get("real_stop_loss_hit"))]
    output: list[dict[str, Any]] = []

    for feature in NUMERIC_FEATURES:
        win_values = [_num(row.get(feature)) for row in winners]
        lose_values = [_num(row.get(feature)) for row in losers]
        win_values = [x for x in win_values if x is not None]
        lose_values = [x for x in lose_values if x is not None]
        wavg = _avg(win_values)
        lavg = _avg(lose_values)
        output.append({
            "feature": feature,
            "type": "numeric_avg",
            "winner_value": wavg,
            "loser_value": lavg,
            "difference_winner_minus_loser": None if wavg is None or lavg is None else round(wavg - lavg, 6),
            "winner_sample": len(win_values),
            "loser_sample": len(lose_values),
        })

    for feature in BOOLEAN_FEATURES:
        wr = _rate(winners, feature)
        lr = _rate(losers, feature)
        output.append({
            "feature": feature,
            "type": "boolean_rate",
            "winner_value": wr,
            "loser_value": lr,
            "difference_winner_minus_loser": None if wr is None or lr is None else round(wr - lr, 6),
            "winner_sample": len(winners),
            "loser_sample": len(losers),
        })

    for feature in CATEGORICAL_FEATURES:
        winner_counts: dict[str, int] = {}
        loser_counts: dict[str, int] = {}
        for row in winners:
            key = str(row.get(feature) if row.get(feature) not in {None, ""} else "unknown")
            winner_counts[key] = winner_counts.get(key, 0) + 1
        for row in losers:
            key = str(row.get(feature) if row.get(feature) not in {None, ""} else "unknown")
            loser_counts[key] = loser_counts.get(key, 0) + 1
        values = sorted(set(winner_counts) | set(loser_counts))
        for value in values:
            wc = winner_counts.get(value, 0)
            lc = loser_counts.get(value, 0)
            output.append({
                "feature": feature + "=" + value,
                "type": "category_count",
                "winner_value": wc,
                "loser_value": lc,
                "difference_winner_minus_loser": wc - lc,
                "winner_sample": len(winners),
                "loser_sample": len(losers),
            })
    return output


def write_winners_losers_csv(rows: list[dict[str, Any]], path: str | Path) -> None:
    fieldnames = ["feature", "type", "winner_value", "loser_value", "difference_winner_minus_loser", "winner_sample", "loser_sample"]
    selected = Path(path)
    selected.parent.mkdir(parents=True, exist_ok=True)
    with selected.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key) for key in fieldnames})
