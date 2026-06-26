"""
F5_T12 — Batch 2: PF Decomposition Compact Generator.

Generates 01_pf_decomposition_compact.json from signal_records.
Descomposes Profit Factor by dimension: ALL, exit_family, symbol, signal_type,
market_regime, weekend, session_bucket, setup, engine_name, signal_tier.

Usage:
    python src/f5_t12_generate_pf_decomposition.py [--db data/trading_bot.db] [--output data/01_pf_decomposition_compact.json]
"""

import argparse
import json
import math
import os
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

# Add src to path
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
    """Add computed fields (exit_family, session_bucket, is_weekend) to a row."""
    enriched = dict(row)
    enriched["exit_family"] = bucket_exit_family(row)
    enriched["session_bucket"] = normalize_session(row.get("created_at"))
    enriched["is_weekend"] = compute_weekend_flag(row)
    return enriched


# ---------------------------------------------------------------------------
# PF Decomposition builder
# ---------------------------------------------------------------------------

DIMENSIONS = [
    "ALL",
    "exit_family",
    "symbol",
    "signal_type",
    "market_regime",
    "weekend",
    "session_bucket",
    "setup",
    "engine_name",
    "signal_tier",
]


def build_pf_decomposition(rows: List[dict], min_count: int = 10) -> List[dict]:
    """Build PF decomposition table across all dimensions.

    Returns a list of dicts, one per segment, sorted by net_r descending.
    """
    enriched = [enrich_row(r) for r in rows]
    results: List[dict] = []

    for dimension in DIMENSIONS:
        segments = segment_rows(enriched, dimension)
        for segment_name, segment_rows_list in segments.items():
            stats = compute_profit_factor_stats(segment_rows_list, min_count=min_count)
            results.append({
                "dimension": dimension,
                "segment": str(segment_name),
                "count": stats["count"],
                "r_values_count": stats["r_values_count"],
                "wins": stats["wins"],
                "losses": stats["losses"],
                "gross_win_r": stats["gross_win_r"],
                "gross_loss_abs_r": stats["gross_loss_abs_r"],
                "net_r": stats["net_r"],
                "avg_r": stats["avg_r"],
                "avg_win_r": stats["avg_win_r"],
                "avg_loss_r": stats["avg_loss_r"],
                "profit_factor": stats["profit_factor"],
                "decision": stats["decision"],
                "confidence": "HIGH" if stats["r_values_count"] >= 30 else (
                    "MEDIUM" if stats["r_values_count"] >= 10 else "LOW"
                ),
            })

    # Sort by net_r descending
    results.sort(key=lambda x: (x["net_r"] or -9999), reverse=True)
    return results


def add_directional_managed_separators(rows: List[dict], min_count: int = 10) -> List[dict]:
    """Add directional and managed aggregate rows to the decomposition."""
    enriched = [enrich_row(r) for r in rows]
    extra: List[dict] = []

    # Directional (TP + SL)
    directional = [r for r in enriched if is_directional_exit(r.get("exit_family", ""))]
    if directional:
        stats = compute_profit_factor_stats(directional, min_count=min_count)
        extra.append({
            "dimension": "exit_family",
            "segment": "directional",
            "count": stats["count"],
            "r_values_count": stats["r_values_count"],
            "wins": stats["wins"],
            "losses": stats["losses"],
            "gross_win_r": stats["gross_win_r"],
            "gross_loss_abs_r": stats["gross_loss_abs_r"],
            "net_r": stats["net_r"],
            "avg_r": stats["avg_r"],
            "avg_win_r": stats["avg_win_r"],
            "avg_loss_r": stats["avg_loss_r"],
            "profit_factor": stats["profit_factor"],
            "decision": stats["decision"],
            "confidence": "HIGH" if stats["r_values_count"] >= 30 else (
                "MEDIUM" if stats["r_values_count"] >= 10 else "LOW"
            ),
        })

    # Managed (all non-TP/SL)
    managed = [r for r in enriched if is_managed_exit(r.get("exit_family", ""))]
    if managed:
        stats = compute_profit_factor_stats(managed, min_count=min_count)
        extra.append({
            "dimension": "exit_family",
            "segment": "managed",
            "count": stats["count"],
            "r_values_count": stats["r_values_count"],
            "wins": stats["wins"],
            "losses": stats["losses"],
            "gross_win_r": stats["gross_win_r"],
            "gross_loss_abs_r": stats["gross_loss_abs_r"],
            "net_r": stats["net_r"],
            "avg_r": stats["avg_r"],
            "avg_win_r": stats["avg_win_r"],
            "avg_loss_r": stats["avg_loss_r"],
            "profit_factor": stats["profit_factor"],
            "decision": stats["decision"],
            "confidence": "HIGH" if stats["r_values_count"] >= 30 else (
                "MEDIUM" if stats["r_values_count"] >= 10 else "LOW"
            ),
        })

    return extra


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Generate PF Decomposition Compact JSON")
    parser.add_argument("--db", default="data/trading_bot.db", help="Path to SQLite DB")
    parser.add_argument("--output", default="data/01_pf_decomposition_compact.json", help="Output JSON path")
    parser.add_argument("--min-count", type=int, default=10, help="Minimum R values for decision")
    args = parser.parse_args()

    print(f"[F5_T12 Batch 2] Loading signal_records from {args.db}...")
    rows = load_signal_records(args.db)
    print(f"[F5_T12 Batch 2] Loaded {len(rows)} signal records")

    # Build decomposition
    print("[F5_T12 Batch 2] Computing PF decomposition...")
    decomposition = build_pf_decomposition(rows, min_count=args.min_count)

    # Add directional/managed separators
    extra = add_directional_managed_separators(rows, min_count=args.min_count)
    decomposition.extend(extra)

    # Re-sort
    decomposition.sort(key=lambda x: (x["net_r"] or -9999), reverse=True)

    # Build output
    output = {
        "meta": {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "total_signal_records": len(rows),
            "min_count_for_decision": args.min_count,
            "description": "PF Decomposition Compact — F5_T12 Batch 2",
        },
        "dimensions_analyzed": DIMENSIONS,
        "rows": decomposition,
    }

    # Write compact JSON
    filepath = write_compact_json(output, args.output)
    print(f"[F5_T12 Batch 2] Written {len(decomposition)} rows to {filepath}")

    # Summary
    total_pf = next((r for r in decomposition if r["dimension"] == "ALL" and r["segment"] == "ALL"), None)
    if total_pf:
        print(f"[F5_T12 Batch 2] Total PF: {total_pf['profit_factor']} (net_r={total_pf['net_r']}, count={total_pf['count']})")

    directional_pf = next((r for r in decomposition if r["segment"] == "directional"), None)
    if directional_pf:
        print(f"[F5_T12 Batch 2] Directional PF: {directional_pf['profit_factor']} (net_r={directional_pf['net_r']}, count={directional_pf['count']})")

    managed_pf = next((r for r in decomposition if r["segment"] == "managed"), None)
    if managed_pf:
        print(f"[F5_T12 Batch 2] Managed PF: {managed_pf['profit_factor']} (net_r={managed_pf['net_r']}, count={managed_pf['count']})")

    print("[F5_T12 Batch 2] Done.")


if __name__ == "__main__":
    main()
