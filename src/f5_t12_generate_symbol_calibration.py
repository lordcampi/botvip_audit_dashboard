"""
F5_T12 — Batch 6: Symbol Calibration Table Generator.

Generates 04_symbol_calibration_table.json from signal_records.
Classifies symbols into ALLOW/WATCH/RESTRICT/BLOCK_TEMPORARY by real performance.

Usage:
    python src/f5_t12_generate_symbol_calibration.py [--db data/trading_bot.db] [--output data/04_symbol_calibration_table.json]
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
    compute_symbol_calibration,
    _classify_symbol_decision,
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
# Symbol Calibration builder
# ---------------------------------------------------------------------------

def build_symbol_calibration(
    rows: List[dict],
    min_count: int = 5,
) -> List[dict]:
    """Build symbol calibration table.

    Returns a list of dicts, one per symbol, with performance metrics and decision.
    """
    enriched = [enrich_row(r) for r in rows]
    results: List[dict] = []

    # Group by symbol
    symbol_groups = segment_rows(enriched, "symbol")
    for sym, sym_rows in sorted(symbol_groups.items(), key=lambda x: -len(x[1])):
        stats = compute_symbol_calibration(sym_rows, str(sym))

        # Compute MFE/MAE averages
        mfe_values: List[float] = []
        mae_values: List[float] = []
        for r in sym_rows:
            meta = parse_json_safe(r.get("metrics_json"))
            mfe = safe_float(meta.get("mfe") or meta.get("max_mfe"))
            if mfe is not None and math.isfinite(mfe):
                mfe_values.append(mfe)
            mae = safe_float(meta.get("mae") or meta.get("max_mae"))
            if mae is not None and math.isfinite(mae):
                mae_values.append(mae)

        avg_mfe = (sum(mfe_values) / len(mfe_values)) if mfe_values else None
        avg_mae = (sum(mae_values) / len(mae_values)) if mae_values else None

        # Weekend PF
        weekend_rows = [r for r in sym_rows if r.get("is_weekend")]
        weekday_rows = [r for r in sym_rows if not r.get("is_weekend")]
        weekend_stats = compute_symbol_calibration(weekend_rows, str(sym)) if weekend_rows else {}
        weekday_stats = compute_symbol_calibration(weekday_rows, str(sym)) if weekday_rows else {}

        results.append({
            "symbol": str(sym),
            "count": stats["count"],
            "directional_count": stats["directional_count"],
            "managed_count": stats["managed_count"],
            "tp_count": stats["tp_count"],
            "sl_count": stats["sl_count"],
            "no_progress_count": stats["no_progress_count"],
            "gross_win_r": stats["gross_win_r"],
            "gross_loss_abs_r": stats["gross_loss_abs_r"],
            "net_r": stats["net_r"],
            "profit_factor": stats["profit_factor"],
            "avg_mfe_r": round(avg_mfe, 4) if avg_mfe is not None else None,
            "avg_mae_r": round(avg_mae, 4) if avg_mae is not None else None,
            "weekday_pf": weekday_stats.get("profit_factor"),
            "weekend_pf": weekend_stats.get("profit_factor"),
            "managed_direct_ratio": stats["managed_direct_ratio"],
            "decision": stats["decision"],
        })

    # Sort by net_r descending
    results.sort(key=lambda x: (x["net_r"] or -9999), reverse=True)
    return results


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Generate Symbol Calibration Table JSON")
    parser.add_argument("--db", default="data/trading_bot.db", help="Path to SQLite DB")
    parser.add_argument("--output", default="data/04_symbol_calibration_table.json", help="Output JSON path")
    parser.add_argument("--min-count", type=int, default=5, help="Minimum count per symbol for decision")
    args = parser.parse_args()

    print(f"[F5_T12 Batch 6] Loading signal_records from {args.db}...")
    rows = load_signal_records(args.db)
    print(f"[F5_T12 Batch 6] Loaded {len(rows)} signal records")

    # Build symbol calibration
    print("[F5_T12 Batch 6] Computing symbol calibration...")
    calibration = build_symbol_calibration(rows, min_count=args.min_count)

    # Build output
    output = {
        "meta": {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "total_signal_records": len(rows),
            "unique_symbols": len(calibration),
            "min_count_for_decision": args.min_count,
            "description": "Symbol Calibration Table — F5_T12 Batch 6",
        },
        "rows": calibration,
    }

    # Write compact JSON
    filepath = write_compact_json(output, args.output)
    print(f"[F5_T12 Batch 6] Written {len(calibration)} symbols to {filepath}")

    # Summary
    allow = [r for r in calibration if r["decision"] == "ALLOW"]
    watch = [r for r in calibration if r["decision"] in ("ALLOW_WITH_CONTEXT", "WATCH", "WATCH_POSITIVE")]
    restrict = [r for r in calibration if r["decision"] == "RESTRICT"]
    block = [r for r in calibration if r["decision"] == "BLOCK_TEMPORARY"]
    print(f"[F5_T12 Batch 6] ALLOW: {len(allow)}, WATCH: {len(watch)}, RESTRICT: {len(restrict)}, BLOCK: {len(block)}")
    print("[F5_T12 Batch 6] Done.")


if __name__ == "__main__":
    main()
