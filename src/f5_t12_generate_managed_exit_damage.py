"""
F5_T12 — Batch 4: Managed Exit Damage Table Generator.

Generates 05_managed_exit_damage_table.json from signal_records.
Measures damage/value of BE/time/no-progress/mfe-stall exits.

Usage:
    python src/f5_t12_generate_managed_exit_damage.py [--db data/trading_bot.db] [--output data/05_managed_exit_damage_table.json]
"""

import argparse
import json
import math
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.f5_t12_calibration_core import (
    safe_float,
    parse_json_safe,
    normalize_session,
    compute_weekend_flag,
    bucket_exit_family,
    is_managed_exit,
    is_directional_exit,
    extract_r_values,
    compute_profit_factor_stats,
    classify_decision,
    segment_rows,
    compute_managed_exit_stats,
    _classify_managed_exit_decision,
    compact_json_serialize,
    write_compact_json,
)
from src.db_readonly import connect_readonly


# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------

def load_signal_records(db_path: str) -> List[dict]:
    """Load all signal_records from the database as dicts."""
    conn = connect_readonly(db_path)
    cursor = conn.execute("SELECT * FROM signal_records ORDER BY created_at")
    columns = [desc[0] for desc in cursor.description]
    rows = cursor.fetchall()
    conn.close()
    return [dict(zip(columns, row)) for row in rows]


def enrich_row(row: dict) -> dict:
    """Add computed fields to a row."""
    enriched = dict(row)
    enriched["exit_family"] = bucket_exit_family(row)
    enriched["session_bucket"] = normalize_session(row.get("created_at"))
    enriched["is_weekend"] = compute_weekend_flag(row)
    return enriched


# ---------------------------------------------------------------------------
# Managed Exit Damage builder
# ---------------------------------------------------------------------------

MANAGED_EXIT_FAMILIES = [
    "no_progress",
    "mfe_stall",
    "time_stop",
    "runner_breakeven_stop",
    "breakeven",
    "runner_tp_hit",
    "expired_pending",
    "unknown_or_open",
]

CONTEXTS = [
    "ALL",
    "weekend",
    "weekday",
    "session_bucket",
    "market_regime",
    "symbol",
    "signal_type",
]


def build_managed_exit_damage(
    all_rows: List[dict],
    managed_rows: List[dict],
    min_count: int = 5,
) -> List[dict]:
    """Build managed exit damage table.

    Returns a list of dicts, one per exit_family + context combination.
    """
    enriched_all = [enrich_row(r) for r in all_rows]
    enriched_managed = [enrich_row(r) for r in managed_rows]
    results: List[dict] = []

    # --- By exit_family (overall) ---
    for family in MANAGED_EXIT_FAMILIES:
        family_rows = [r for r in enriched_managed if r.get("exit_family") == family]
        if not family_rows:
            continue
        stats = compute_managed_exit_stats(family_rows)
        # Compute MFE/MAE before exit
        mfe_values: List[float] = []
        mae_values: List[float] = []
        for r in family_rows:
            meta = parse_json_safe(r.get("metrics_json"))
            mfe = safe_float(meta.get("mfe") or meta.get("max_mfe"))
            if mfe is not None and math.isfinite(mfe):
                mfe_values.append(mfe)
            mae = safe_float(meta.get("mae") or meta.get("max_mae"))
            if mae is not None and math.isfinite(mae):
                mae_values.append(mae)

        avg_mfe = (sum(mfe_values) / len(mfe_values)) if mfe_values else None
        avg_mae = (sum(mae_values) / len(mae_values)) if mae_values else None

        # Duration
        durations: List[float] = []
        for r in family_rows:
            meta = parse_json_safe(r.get("metrics_json"))
            dur = safe_float(meta.get("duration_minutes") or meta.get("duration"))
            if dur is not None and math.isfinite(dur):
                durations.append(dur)
        avg_duration = (sum(durations) / len(durations)) if durations else None

        # Managed/direct ratio
        directional_count = len([r for r in enriched_all if is_directional_exit(r.get("exit_family", ""))])
        managed_direct_ratio = len(managed_rows) / max(1, directional_count)

        results.append({
            "exit_family": family,
            "context": "ALL",
            "count": stats["count"],
            "r_values_count": stats["r_values_count"],
            "avg_r": stats["avg_r"],
            "net_r": stats["net_r"],
            "gross_win_r": stats["gross_win_r"],
            "gross_loss_abs_r": stats["gross_loss_abs_r"],
            "profit_factor": stats["profit_factor"],
            "avg_mfe_before_exit": round(avg_mfe, 4) if avg_mfe is not None else None,
            "avg_mae_before_exit": round(avg_mae, 4) if avg_mae is not None else None,
            "avg_duration_min": round(avg_duration, 1) if avg_duration is not None else None,
            "managed_direct_ratio": round(managed_direct_ratio, 4),
            "decision": stats["decision"],
        })

    # --- By exit_family + weekend ---
    for family in MANAGED_EXIT_FAMILIES:
        family_rows = [r for r in enriched_managed if r.get("exit_family") == family]
        if not family_rows:
            continue
        wk_segments = segment_rows(family_rows, "weekend")
        for wk, wk_rows in wk_segments.items():
            if len(wk_rows) < min_count:
                continue
            stats = compute_managed_exit_stats(wk_rows)
            results.append({
                "exit_family": family,
                "context": str(wk),
                "count": stats["count"],
                "r_values_count": stats["r_values_count"],
                "avg_r": stats["avg_r"],
                "net_r": stats["net_r"],
                "gross_win_r": stats["gross_win_r"],
                "gross_loss_abs_r": stats["gross_loss_abs_r"],
                "profit_factor": stats["profit_factor"],
                "avg_mfe_before_exit": None,
                "avg_mae_before_exit": None,
                "avg_duration_min": None,
                "managed_direct_ratio": None,
                "decision": stats["decision"],
            })

    # --- By exit_family + session_bucket ---
    for family in MANAGED_EXIT_FAMILIES:
        family_rows = [r for r in enriched_managed if r.get("exit_family") == family]
        if not family_rows:
            continue
        sb_segments = segment_rows(family_rows, "session_bucket")
        for sb, sb_rows in sb_segments.items():
            if len(sb_rows) < min_count:
                continue
            stats = compute_managed_exit_stats(sb_rows)
            results.append({
                "exit_family": family,
                "context": str(sb),
                "count": stats["count"],
                "r_values_count": stats["r_values_count"],
                "avg_r": stats["avg_r"],
                "net_r": stats["net_r"],
                "gross_win_r": stats["gross_win_r"],
                "gross_loss_abs_r": stats["gross_loss_abs_r"],
                "profit_factor": stats["profit_factor"],
                "avg_mfe_before_exit": None,
                "avg_mae_before_exit": None,
                "avg_duration_min": None,
                "managed_direct_ratio": None,
                "decision": stats["decision"],
            })

    # --- By exit_family + market_regime ---
    for family in MANAGED_EXIT_FAMILIES:
        family_rows = [r for r in enriched_managed if r.get("exit_family") == family]
        if not family_rows:
            continue
        mr_segments = segment_rows(family_rows, "market_regime")
        for mr, mr_rows in mr_segments.items():
            if len(mr_rows) < min_count:
                continue
            stats = compute_managed_exit_stats(mr_rows)
            results.append({
                "exit_family": family,
                "context": str(mr),
                "count": stats["count"],
                "r_values_count": stats["r_values_count"],
                "avg_r": stats["avg_r"],
                "net_r": stats["net_r"],
                "gross_win_r": stats["gross_win_r"],
                "gross_loss_abs_r": stats["gross_loss_abs_r"],
                "profit_factor": stats["profit_factor"],
                "avg_mfe_before_exit": None,
                "avg_mae_before_exit": None,
                "avg_duration_min": None,
                "managed_direct_ratio": None,
                "decision": stats["decision"],
            })

    # Sort by net_r ascending (worst first)
    results.sort(key=lambda x: (x["net_r"] or 9999))
    return results


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Generate Managed Exit Damage Table JSON")
    parser.add_argument("--db", default="data/trading_bot.db", help="Path to SQLite DB")
    parser.add_argument("--output", default="data/05_managed_exit_damage_table.json", help="Output JSON path")
    parser.add_argument("--min-count", type=int, default=5, help="Minimum count per segment")
    args = parser.parse_args()

    print(f"[F5_T12 Batch 4] Loading signal_records from {args.db}...")
    rows = load_signal_records(args.db)
    print(f"[F5_T12 Batch 4] Loaded {len(rows)} signal records")

    # Identify managed exit rows
    managed_rows = [r for r in rows if is_managed_exit(bucket_exit_family(r))]
    directional_rows = [r for r in rows if is_directional_exit(bucket_exit_family(r))]
    print(f"[F5_T12 Batch 4] Managed exits: {len(managed_rows)}, Directional: {len(directional_rows)}")

    # Build damage table
    print("[F5_T12 Batch 4] Computing managed exit damage...")
    damage_table = build_managed_exit_damage(rows, managed_rows, min_count=args.min_count)

    # Build output
    output = {
        "meta": {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "total_signal_records": len(rows),
            "managed_exit_count": len(managed_rows),
            "directional_count": len(directional_rows),
            "managed_direct_ratio": round(len(managed_rows) / max(1, len(directional_rows)), 4),
            "min_count_per_segment": args.min_count,
            "description": "Managed Exit Damage Table — F5_T12 Batch 4",
        },
        "rows": damage_table,
    }

    # Write compact JSON
    filepath = write_compact_json(output, args.output)
    print(f"[F5_T12 Batch 4] Written {len(damage_table)} rows to {filepath}")
    print("[F5_T12 Batch 4] Done.")


if __name__ == "__main__":
    main()
