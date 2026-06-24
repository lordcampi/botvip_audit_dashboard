from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

from src.ai_pack_builder import build_ai_prompt, build_ai_review_pack, build_executive_summary
from src.daily_facts import build_daily_facts, write_facts_csv
from src.db_readonly import assert_readonly, connect_readonly, get_db_path
from src.deep_diagnostics import compute_deep_diagnostics, write_deep_diagnostics
from src.f4_t11a_audit import audit_f4_t11a_semantics
from src.hypothesis_builder import build_strategy_hypotheses, write_strategy_hypotheses
from src.lifecycle_metrics import compute_lifecycle_metrics
from src.loaders import load_candidate_snapshots, load_events, load_signals
from src.near_misses import select_near_misses, write_near_misses_csv
from src.ofa_funnel import compute_filter_funnel, write_filter_funnel_csv
from src.rejection_analysis import compute_blocked_analysis, write_blocked_candidates_csv
from src.report_writer import create_zip, write_json, write_rows_csv, write_text
from src.schema_mapper import SchemaMap
from src.text_splitter import write_split_text
from src.t02_diagnostics import compute_t02_diagnostics, write_t02_diagnostics
from src.time_windows import parse_window
from src.winners_losers import compare_winners_losers, write_winners_losers_csv


def report_date_from_window_end(end_text: str) -> str:
    try:
        return datetime.strptime(end_text[:19], "%Y-%m-%d %H:%M:%S").strftime("%Y-%m-%d")
    except Exception:
        return datetime.now().strftime("%Y-%m-%d")


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate BotVIP Daily AI Reporter pack in read-only mode.")
    parser.add_argument("--window", default="daily", help="daily, 24h, 12h, 7d. Default: daily 5am Colombia window")
    parser.add_argument("--output", default="reports", help="Output directory. Default: reports")
    parser.add_argument("--db-path", default=None, help="Optional DB path override")
    parser.add_argument("--max-ai-chars", type=int, default=120000, help="Max characters per AI review pack txt part")
    parser.add_argument("--dry-run", action="store_true", help="Validate and print summary without writing report files")
    args = parser.parse_args()

    schema = SchemaMap.load("config/schema_map.json")
    window = parse_window(args.window)
    db_path = get_db_path(args.db_path)

    with connect_readonly(db_path) as conn:
        assert_readonly(conn)
        validation = schema.validate_against_db(conn)
        if not validation.get("ok"):
            raise SystemExit("Schema validation failed: " + json.dumps(validation, ensure_ascii=False, default=str))
        events = load_events(conn, schema, window, limit=500000)
        signals = load_signals(conn, schema, window, limit=500000)
        candidates = load_candidate_snapshots(conn, schema, window, limit=500000)

    facts = build_daily_facts(events, signals, candidates)
    lifecycle = compute_lifecycle_metrics(facts)
    audit = audit_f4_t11a_semantics(facts)
    blocked_summary = compute_blocked_analysis(facts)
    winners_losers_rows = compare_winners_losers(facts)
    funnel_rows: list[dict] = []
    funnel_rows.extend(compute_filter_funnel(facts, record_type="signal"))
    funnel_rows.extend(compute_filter_funnel(facts, record_type="candidate"))
    near_misses = select_near_misses(facts, limit=200)
    hypotheses = build_strategy_hypotheses(lifecycle, blocked_summary, winners_losers_rows, near_misses)
    diagnostics = compute_deep_diagnostics(facts)
    t02_diagnostics = compute_t02_diagnostics(facts)

    report_date = report_date_from_window_end(window.end_text)
    report_dir = Path(args.output) / report_date

    executive_summary = build_executive_summary(window.label, window.start_text, window.end_text, lifecycle, audit, blocked_summary, hypotheses)
    ai_prompt = build_ai_prompt()
    ai_pack = build_ai_review_pack(
        window.label,
        window.start_text,
        window.end_text,
        executive_summary,
        lifecycle,
        audit,
        blocked_summary,
        winners_losers_rows,
        funnel_rows,
        near_misses,
        hypotheses,
    )

    ai_pack += "\n## 13. Deep diagnostics\n```json\n" + json.dumps(diagnostics, indent=2, ensure_ascii=False, default=str) + "\n```\n"

    ai_pack += "\n## 14. T02 diagnostics\n```json\n" + json.dumps(t02_diagnostics, indent=2, ensure_ascii=False, default=str) + "\n```\n"

    summary = {
        "window": {"label": window.label, "start": window.start_text, "end": window.end_text},
        "rows": {"events": len(events), "signals": len(signals), "candidates": len(candidates), "facts": len(facts)},
        "lifecycle": lifecycle,
        "audit": audit,
        "blocked_summary": blocked_summary,
        "hypotheses_count": len(hypotheses),
        "t02_diagnostics": t02_diagnostics,
        "deep_diagnostics": diagnostics,
    }

    if args.dry_run:
        print(json.dumps(summary, indent=2, ensure_ascii=False, default=str))
        print("OK: dry-run completed. No files written.")
        return 0

    written: list[Path] = []
    written.append(write_text(executive_summary, report_dir / "01_executive_summary.md"))
    write_facts_csv(facts, report_dir / "02_daily_signal_facts.csv")
    written.append(report_dir / "02_daily_signal_facts.csv")
    write_blocked_candidates_csv(facts, report_dir / "03_blocked_candidates.csv")
    written.append(report_dir / "03_blocked_candidates.csv")
    write_winners_losers_csv(winners_losers_rows, report_dir / "04_winners_vs_losers.csv")
    written.append(report_dir / "04_winners_vs_losers.csv")
    write_filter_funnel_csv(funnel_rows, report_dir / "05_filter_funnel.csv")
    written.append(report_dir / "05_filter_funnel.csv")
    write_near_misses_csv(near_misses, report_dir / "06_near_misses.csv")
    written.append(report_dir / "06_near_misses.csv")
    written.append(write_rows_csv(events, report_dir / "07_lifecycle_events.csv"))
    write_strategy_hypotheses(hypotheses, report_dir / "08_strategy_hypotheses.json")
    written.append(report_dir / "08_strategy_hypotheses.json")
    write_deep_diagnostics(diagnostics, report_dir / "11_deep_diagnostics.json")
    written.append(report_dir / "11_deep_diagnostics.json")
    write_t02_diagnostics(t02_diagnostics, report_dir / "12_t02_no_progress_reclaim_zone_pf.json")
    written.append(report_dir / "12_t02_no_progress_reclaim_zone_pf.json")
    written.append(write_text(ai_prompt, report_dir / "09_ai_prompt.md"))
    ai_parts = write_split_text(ai_pack, report_dir, "10_ai_review_pack", max_chars=args.max_ai_chars)
    written.extend(ai_parts)
    ai_readme = '# BotVIP AI Review Pack - READ ME FIRST\n\nThis ZIP is optimized for Copilot/GPT deep review.\n\nUpload these files to the AI:\n\n1. 10_ai_review_pack_part_01.txt\n2. 10_ai_review_pack_part_02.txt, if present\n3. 11_deep_diagnostics.json\n4. 08_strategy_hypotheses.json\n5. report_manifest.json\n6. 01_executive_summary.md, optional but useful\n\nDo NOT upload CSV files unless the AI explicitly asks for them.\nThe CSV files are generated in the server report folder for audit/debugging,\nbut they are intentionally excluded from this AI ZIP because they are too large\nfor practical Copilot/GPT review.\n\nAnalysis rules:\n- Do not recommend real trading.\n- Do not propose automatic threshold changes.\n- Treat single-day samples as weak or preliminary evidence.\n- PRIMARY_TP_HIT is the official WIN.\n- Breakeven is not a real STOP_LOSS.\n- Runner/TP2 cannot invalidate an official WIN.\n- Use near-miss and no-progress diagnostics only for shadow hypotheses.\n'
    ai_readme_path = write_text(ai_readme, report_dir / "00_README_FOR_AI.md")
    manifest_path = write_json(summary, report_dir / "report_manifest.json")
    written.append(manifest_path)

    ai_zip_files = [
        ai_readme_path,
        report_dir / "01_executive_summary.md",
        report_dir / "08_strategy_hypotheses.json",
        report_dir / "09_ai_prompt.md",
        report_dir / "11_deep_diagnostics.json",
        report_dir / "12_t02_no_progress_reclaim_zone_pf.json",
        manifest_path,
    ]
    ai_zip_files.extend(ai_parts)
    ai_zip_files = [p for p in ai_zip_files if Path(p).exists()]

    zip_path = report_dir / f"AI_REVIEW_{report_date}.zip"
    create_zip(ai_zip_files, zip_path, base_dir=report_dir)

    print(json.dumps({
        "report_dir": str(report_dir),
        "zip_path": str(zip_path),
        "files_written": [str(p) for p in written] + [str(ai_readme_path)] + [str(zip_path)],
        "ai_zip_files": [str(p) for p in ai_zip_files],
        "summary": summary,
    }, indent=2, ensure_ascii=False, default=str))
    print("OK: Daily AI Reporter pack generated")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
