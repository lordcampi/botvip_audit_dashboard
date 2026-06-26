from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

from src.ai_pack_builder import build_ai_prompt, build_ai_review_pack, build_executive_summary
from src.daily_facts import build_daily_facts, write_facts_csv
from src.db_readonly import assert_readonly, connect_readonly, get_db_path
from src.f5_t03b_sections import FILENAME as F5_T03B_SECTIONS_FILENAME
from src.f5_t03b_sections import build_f5_t03b_integration_sections
from src.f5_t04i_slim_sections import F5_T03B_SLIM_FILENAME, build_f5_t03b_slim_sections
from src.f5_t09a_lifecycle_reconciliation import F5_T09A_LIFECYCLE_RECONCILIATION_FILENAME, build_telegram_lifecycle_reconciliation_v2
from src.f5_t09bc_no_progress_mfe import (
    F5_T09B_NO_PROGRESS_ROOT_CAUSE_V3_FILENAME,
    F5_T09C_MFE_CAPTURE_EFFICIENCY_FILENAME,
    build_f5_t09bc_no_progress_mfe_outputs,
)
from src.f5_t09dfghi_guard_segments import (
    F5_T09D_GUARD_SHADOW_OUTCOME_MATRIX_FILENAME,
    F5_T09F_LOW_VOL_WINNERS_LOSERS_FILENAME,
    F5_T09G_COPYABILITY_BUCKET_OUTCOME_FILENAME,
    F5_T09H_ATR_EXTENSION_OUTCOMES_FILENAME,
    F5_T09I_BTC_BIAS_RECLAIM_QUALITY_FILENAME,
    build_f5_t09dfghi_guard_filter_outputs,
)
from src.f5_t09e_symbol_alpha import F5_T09E_SYMBOL_SHADOW_ALPHA_FILENAME, build_symbol_not_allowed_shadow_alpha
from src.f5_t10_super_digest import F5_T10_DIGEST_JSON_FILENAME, F5_T10_DIGEST_MD_FILENAME, build_f5_t09_super_digest
from src.ai_reporter.f5_t12_strategy_readiness import (
    F5_T12_READINESS_JSON_FILENAME,
    F5_T12_READINESS_MD_FILENAME,
    build_f5_t12_strategy_readiness,
)
from src.f5_t04bcd_diagnostics import (
    ENTITY_SCOPE_RECONCILIATION_FILENAME,
    NO_PROGRESS_ROOT_CAUSE_FILENAME,
    ZONE_DIAGNOSTICS_V2_FILENAME,
    ZONE_MAPPING_QUALITY_FILENAME,
    build_f5_t04bcd_diagnostics,
)
from src.f5_t04e_insights import (
    AI_INSIGHT_SUMMARY_FILENAME,
    LOSS_CONTRIBUTION_FILENAME,
    build_f5_t04e_outputs,
)
from src.deep_diagnostics import compute_deep_diagnostics, write_deep_diagnostics
from src.f4_t11a_audit import audit_f4_t11a_semantics
from src.hypothesis_builder import build_strategy_hypotheses, write_strategy_hypotheses
from src.lifecycle_metrics import compute_lifecycle_metrics
from src.loaders import load_candidate_snapshots, load_events, load_signals
from src.near_misses import select_near_misses, write_near_misses_csv
from src.ofa_funnel import compute_filter_funnel, write_filter_funnel_csv
from src.rejection_analysis import compute_blocked_analysis, write_blocked_candidates_csv
from src.report_writer import create_zip, write_json, write_rows_csv, write_text
from src.safe_zip_chunking import DEFAULT_SAFE_TARGET_CHARS, DEFAULT_ZIP_CHAR_LIMIT, prepare_zip_files_for_char_limit
from src.schema_mapper import SchemaMap
from src.text_splitter import write_split_text
from src.t02_diagnostics import compute_t02_diagnostics, write_t02_diagnostics
from src.time_windows import parse_window
from src.winners_losers import compare_winners_losers, write_winners_losers_csv

AI_REVIEW_ZIP_ENTRY_SAFE_LIMIT = 95000


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
    parser.add_argument("--max-ai-chars", type=int, default=DEFAULT_SAFE_TARGET_CHARS, help="Max characters per AI review pack txt part")
    parser.add_argument("--dry-run", action="store_true", help="Validate and print summary without writing report files")
    args = parser.parse_args()
    args.max_ai_chars = min(args.max_ai_chars, AI_REVIEW_ZIP_ENTRY_SAFE_LIMIT)

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
    f5_t03b_sections = build_f5_t03b_integration_sections(
        facts=facts,
        events=events,
        signals=signals,
        candidates=candidates,
        lifecycle=lifecycle,
        diagnostics=diagnostics,
        t02_diagnostics=t02_diagnostics,
    )
    f5_t03b_slim_sections = build_f5_t03b_slim_sections(f5_t03b_sections)
    f5_t04bcd_sections = build_f5_t04bcd_diagnostics(
        facts=facts,
        events=events,
        signals=signals,
        candidates=candidates,
    )
    f5_t04e_outputs = build_f5_t04e_outputs(
        facts=facts,
        lifecycle=lifecycle,
        blocked_summary=blocked_summary,
        t02_diagnostics=t02_diagnostics,
        f5_t04bcd_sections=f5_t04bcd_sections,
    )
    f5_t09a_lifecycle_reconciliation = build_telegram_lifecycle_reconciliation_v2(
        facts=facts,
        events=events,
        signals=signals,
    )

    f5_t09bc_outputs = build_f5_t09bc_no_progress_mfe_outputs(
        facts=facts,
        events=events,
        signals=signals,
        candidates=candidates,
    )

    f5_t09dfghi_outputs = build_f5_t09dfghi_guard_filter_outputs(
        facts=facts,
        events=events,
        signals=signals,
        candidates=candidates,
    )

    f5_t09e_symbol_alpha = build_symbol_not_allowed_shadow_alpha(
        facts=facts,
        events=events,
        signals=signals,
        candidates=candidates,
    )

    f5_t09_super_digest = build_f5_t09_super_digest(
        lifecycle_reconciliation=f5_t09a_lifecycle_reconciliation,
        no_progress_v3=f5_t09bc_outputs["no_progress_root_cause_v3"],
        mfe_capture=f5_t09bc_outputs["mfe_capture_efficiency_by_exit_reason"],
        guard_matrix=f5_t09dfghi_outputs["guard_shadow_outcome_matrix"],
        low_vol=f5_t09dfghi_outputs["low_vol_winners_vs_losers"],
        copyability=f5_t09dfghi_outputs["copyability_score_bucket_outcome"],
        atr_extension=f5_t09dfghi_outputs["atr_extension_shadow_outcomes"],
        btc_bias=f5_t09dfghi_outputs["btc_bias_conflict_reclaim_quality"],
        symbol_alpha=f5_t09e_symbol_alpha,
    )

    # F5_T12 Strategy Change Readiness digest (compact, < 95,000 chars)
    f5_t12_readiness = build_f5_t12_strategy_readiness(
        lifecycle=lifecycle,
        facts=facts,
        t02_diagnostics=t02_diagnostics,
        loss_contribution=f5_t04e_outputs["loss_contribution"],
        no_progress_v3=f5_t09bc_outputs["no_progress_root_cause_v3"],
        guard_matrix=f5_t09dfghi_outputs["guard_shadow_outcome_matrix"],
        lifecycle_reconciliation=f5_t09a_lifecycle_reconciliation,
    )

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
        "f5_t03b_integration_sections": {
            "file": F5_T03B_SECTIONS_FILENAME,
            "schema_version": f5_t03b_sections.get("schema_version"),
            "source": f5_t03b_sections.get("source"),
        },
        "f5_t03b_slim_sections": {
            "file": F5_T03B_SLIM_FILENAME,
            "source_full_file": F5_T03B_SECTIONS_FILENAME,
            "schema_version": f5_t03b_slim_sections.get("schema_version"),
            "full_file_excluded_from_ai_zip": True,
            "read_only": True,
        },
        "f5_t04bcd_batch2_sections": {
            "schema_version": "f5_t04bcd_batch2_diagnostics_v1",
            "files": [
                NO_PROGRESS_ROOT_CAUSE_FILENAME,
                ZONE_DIAGNOSTICS_V2_FILENAME,
                ZONE_MAPPING_QUALITY_FILENAME,
                ENTITY_SCOPE_RECONCILIATION_FILENAME,
            ],
            "read_only": True,
        },
        "f5_t04e_batch3_sections": {
            "schema_version": "f5_t04e_loss_contribution_ai_insight_v1",
            "files": [
                LOSS_CONTRIBUTION_FILENAME,
                AI_INSIGHT_SUMMARY_FILENAME,
            ],
            "read_only": True,
        },
        "f5_t09a_lifecycle_reconciliation_v2": {
            "schema_version": f5_t09a_lifecycle_reconciliation.get("schema_version"),
            "file": F5_T09A_LIFECYCLE_RECONCILIATION_FILENAME,
            "read_only": True,
            "purpose": "Separate official WIN protected outcome from later runner closure.",
        },
    }

    summary["f5_t09bc_no_progress_mfe_capture"] = {
        "schema_version": f5_t09bc_outputs["no_progress_root_cause_v3"].get("schema_version"),
        "files": [F5_T09B_NO_PROGRESS_ROOT_CAUSE_V3_FILENAME, F5_T09C_MFE_CAPTURE_EFFICIENCY_FILENAME],
        "read_only": True,
        "purpose": "Diagnose no-progress root causes and MFE capture efficiency by exit reason.",
    }

    summary["f5_t09dfghi_guard_filter_segmentation"] = {
        "schema_version": f5_t09dfghi_outputs["guard_shadow_outcome_matrix"].get("schema_version"),
        "files": [
            F5_T09D_GUARD_SHADOW_OUTCOME_MATRIX_FILENAME,
            F5_T09F_LOW_VOL_WINNERS_LOSERS_FILENAME,
            F5_T09G_COPYABILITY_BUCKET_OUTCOME_FILENAME,
            F5_T09H_ATR_EXTENSION_OUTCOMES_FILENAME,
            F5_T09I_BTC_BIAS_RECLAIM_QUALITY_FILENAME,
        ],
        "read_only": True,
        "purpose": "Measure guard/filter context outcomes without changing runtime behavior.",
    }

    summary["f5_t09e_symbol_not_allowed_shadow_alpha"] = {
        "schema_version": f5_t09e_symbol_alpha.get("schema_version"),
        "file": F5_T09E_SYMBOL_SHADOW_ALPHA_FILENAME,
        "read_only": True,
        "purpose": "Measure alpha in symbol-not-allowed shadow candidates without opening allowlist.",
    }

    summary["f5_t10_f5_t09_ai_super_digest"] = {
        "schema_version": f5_t09_super_digest["json"].get("schema_version"),
        "files": [F5_T10_DIGEST_JSON_FILENAME, F5_T10_DIGEST_MD_FILENAME],
        "read_only": True,
        "purpose": "Compact AI-ready F5_T09 digest; full F5_T09 JSONs remain server-side only.",
    }

    summary["f5_t12_strategy_change_readiness"] = {
        "schema_version": f5_t12_readiness["json"].get("schema_version"),
        "files": [F5_T12_READINESS_JSON_FILENAME, F5_T12_READINESS_MD_FILENAME],
        "read_only": True,
        "purpose": "Compact F5_T12 strategy change readiness digest with top findings and deploy checklist.",
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
    f5_t03b_sections_path = write_json(f5_t03b_sections, report_dir / F5_T03B_SLIM_FILENAME)
    written.append(f5_t03b_sections_path)
    f5_t03b_slim_sections_path = write_json(f5_t03b_slim_sections, report_dir / F5_T03B_SLIM_FILENAME)
    written.append(f5_t03b_slim_sections_path)
    no_progress_root_cause_path = write_json(f5_t04bcd_sections["no_progress_root_cause_diagnostics"], report_dir / NO_PROGRESS_ROOT_CAUSE_FILENAME)
    written.append(no_progress_root_cause_path)
    zone_diagnostics_v2_path = write_json(f5_t04bcd_sections["zone_diagnostics_v2"], report_dir / ZONE_DIAGNOSTICS_V2_FILENAME)
    written.append(zone_diagnostics_v2_path)
    zone_mapping_quality_path = write_json(f5_t04bcd_sections["zone_mapping_quality"], report_dir / ZONE_MAPPING_QUALITY_FILENAME)
    written.append(zone_mapping_quality_path)
    entity_scope_reconciliation_path = write_json(f5_t04bcd_sections["entity_scope_reconciliation"], report_dir / ENTITY_SCOPE_RECONCILIATION_FILENAME)
    written.append(entity_scope_reconciliation_path)
    loss_contribution_path = write_json(f5_t04e_outputs["loss_contribution"], report_dir / LOSS_CONTRIBUTION_FILENAME)
    written.append(loss_contribution_path)
    ai_insight_summary_path = write_json(f5_t04e_outputs["ai_insight_summary"], report_dir / AI_INSIGHT_SUMMARY_FILENAME)
    written.append(ai_insight_summary_path)
    lifecycle_reconciliation_v2_path = write_json(f5_t09a_lifecycle_reconciliation, report_dir / F5_T09A_LIFECYCLE_RECONCILIATION_FILENAME)
    written.append(lifecycle_reconciliation_v2_path)
    no_progress_v3_path = write_json(f5_t09bc_outputs["no_progress_root_cause_v3"], report_dir / F5_T09B_NO_PROGRESS_ROOT_CAUSE_V3_FILENAME)
    written.append(no_progress_v3_path)
    mfe_capture_efficiency_path = write_json(f5_t09bc_outputs["mfe_capture_efficiency_by_exit_reason"], report_dir / F5_T09C_MFE_CAPTURE_EFFICIENCY_FILENAME)
    written.append(mfe_capture_efficiency_path)
    guard_shadow_matrix_path = write_json(f5_t09dfghi_outputs["guard_shadow_outcome_matrix"], report_dir / F5_T09D_GUARD_SHADOW_OUTCOME_MATRIX_FILENAME)
    written.append(guard_shadow_matrix_path)
    low_vol_winners_losers_path = write_json(f5_t09dfghi_outputs["low_vol_winners_vs_losers"], report_dir / F5_T09F_LOW_VOL_WINNERS_LOSERS_FILENAME)
    written.append(low_vol_winners_losers_path)
    copyability_bucket_outcome_path = write_json(f5_t09dfghi_outputs["copyability_score_bucket_outcome"], report_dir / F5_T09G_COPYABILITY_BUCKET_OUTCOME_FILENAME)
    written.append(copyability_bucket_outcome_path)
    atr_extension_outcomes_path = write_json(f5_t09dfghi_outputs["atr_extension_shadow_outcomes"], report_dir / F5_T09H_ATR_EXTENSION_OUTCOMES_FILENAME)
    written.append(atr_extension_outcomes_path)
    btc_bias_reclaim_quality_path = write_json(f5_t09dfghi_outputs["btc_bias_conflict_reclaim_quality"], report_dir / F5_T09I_BTC_BIAS_RECLAIM_QUALITY_FILENAME)
    written.append(btc_bias_reclaim_quality_path)
    symbol_alpha_path = write_json(f5_t09e_symbol_alpha, report_dir / F5_T09E_SYMBOL_SHADOW_ALPHA_FILENAME)
    written.append(symbol_alpha_path)
    f5_t09_digest_json_path = write_json(f5_t09_super_digest["json"], report_dir / F5_T10_DIGEST_JSON_FILENAME)
    written.append(f5_t09_digest_json_path)
    f5_t09_digest_md_path = write_text(f5_t09_super_digest["markdown"], report_dir / F5_T10_DIGEST_MD_FILENAME)
    written.append(f5_t09_digest_md_path)
    f5_t12_readiness_json_path = write_json(f5_t12_readiness["json"], report_dir / F5_T12_READINESS_JSON_FILENAME)
    written.append(f5_t12_readiness_json_path)
    f5_t12_readiness_md_path = write_text(f5_t12_readiness["markdown"], report_dir / F5_T12_READINESS_MD_FILENAME)
    written.append(f5_t12_readiness_md_path)
    written.append(write_text(ai_prompt, report_dir / "09_ai_prompt.md"))
    ai_parts = write_split_text(ai_pack, report_dir, "10_ai_review_pack", max_chars=args.max_ai_chars)
    written.extend(ai_parts)
    ai_readme = '# BotVIP AI Review Pack - READ ME FIRST\n\nThis ZIP is optimized for Copilot/GPT deep review.\n\nUpload these files to the AI:\n\n1. 10_ai_review_pack_part_01.txt\n2. 10_ai_review_pack_part_02.txt, if present\n3. 11_deep_diagnostics.json\n4. 08_strategy_hypotheses.json\n5. report_manifest.json\n6. 01_executive_summary.md, optional but useful\n7. f5_t03b_integration_sections_slim.json, compact derived diagnostics summary. Full f5_t03b is generated on server but excluded from this AI ZIP\n8. 13_no_progress_root_cause_diagnostics.json, no-progress evidence classifier\n9. 14_zone_diagnostics_v2.json and 15_zone_mapping_quality.json, zone repair diagnostics\n10. 16_entity_scope_reconciliation.json, official-vs-derived row scope\n\nDo NOT upload CSV files unless the AI explicitly asks for them.\nThe CSV files and the full f5_t03b_integration_sections.json are generated in the server report folder for audit/debugging,\nbut they are intentionally excluded from this AI ZIP because they are too large\nfor practical Copilot/GPT review.\n\nAnalysis rules:\n- Do not recommend real trading.\n- Do not propose automatic threshold changes.\n- Treat single-day samples as weak or preliminary evidence.\n- PRIMARY_TP_HIT is the official WIN.\n- Breakeven is not a real STOP_LOSS.\n- Runner/TP2 cannot invalidate an official WIN.\n- Use near-miss and no-progress diagnostics only for shadow hypotheses.\n- Do not double-count CSV rows, dashboard-derived rows, candidates, or diagnostic rows as independent trades.\n- Use entity_scope_reconciliation.json to distinguish official signals from derived analytical rows.\n- Treat zone diagnostics as reporting-only; they do not change runtime strategy decisions.\n'
    ai_readme += (
        "\nAdditional AI review file added by F5_T09a:\n"
        "- 19_telegram_lifecycle_reconciliation_v2.json, official WIN protected vs runner closure reconciliation\n"
    )
    ai_readme += (
        "\nAdditional AI review files added by F5_T09b/F5_T09c:\n"
        "- 20_no_progress_root_cause_v3.json, no-progress root-cause classifier v3\n"
        "- 21_mfe_capture_efficiency_by_exit_reason.json, MFE capture efficiency by exit reason\n"
    )
    ai_readme += (
        "\nAdditional AI review files added by F5_T09d/F5_T09f/F5_T09g/F5_T09h/F5_T09i:\n"
        "- 22_guard_shadow_outcome_matrix.json, guard blocked shadow outcome value matrix\n"
        "- 23_low_vol_winners_vs_losers.json, LOW_VOL winners vs losers separation\n"
        "- 24_copyability_score_bucket_outcome.json, copyability bucket outcomes\n"
        "- 25_atr_extension_shadow_outcomes.json, ATR extension shadow outcomes\n"
        "- 26_btc_bias_conflict_reclaim_quality.json, BTC bias conflict reclaim quality\n"
    )
    ai_readme += (
        "\nAdditional AI review file added by F5_T09e:\n"
        "- 27_symbol_not_allowed_shadow_alpha.json, symbol-not-allowed shadow alpha ranking without allowlist changes\n"
    )
    ai_readme += (
        "\nF5_T10 AI ZIP slimming policy:\n"
        "- 28_f5_t09_ai_super_digest.json and .md summarize F5_T09 sections 19-27 for AI review.\n"
        "- Full F5_T09 JSON files 20-27 are generated in the server report folder but excluded from this AI ZIP to avoid excessive chunking.\n"
        "- Ask for a full server-side JSON only when a specific section needs deep evidence review.\n"
    )
    ai_readme += (
        "\nF5_T12 Strategy Change Readiness digest:\n"
        "- 29_f5_t12_strategy_change_readiness.json and .md compact digest for F5_T12 change validation.\n"
        "- Summarizes denominators, PF core, loss top, no-progress, risk context, guard value, data quality, and human checklist.\n"
        "- Both files are < 95,000 characters; full evidence remains server-side.\n"
    )
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
        report_dir / F5_T03B_SLIM_FILENAME,
        report_dir / NO_PROGRESS_ROOT_CAUSE_FILENAME,
        report_dir / ZONE_DIAGNOSTICS_V2_FILENAME,
        report_dir / ZONE_MAPPING_QUALITY_FILENAME,
        report_dir / ENTITY_SCOPE_RECONCILIATION_FILENAME,
        report_dir / LOSS_CONTRIBUTION_FILENAME,
        report_dir / AI_INSIGHT_SUMMARY_FILENAME,
        report_dir / F5_T10_DIGEST_JSON_FILENAME,
        report_dir / F5_T10_DIGEST_MD_FILENAME,
        report_dir / F5_T09A_LIFECYCLE_RECONCILIATION_FILENAME,
        report_dir / F5_T12_READINESS_JSON_FILENAME,
        report_dir / F5_T12_READINESS_MD_FILENAME,
        manifest_path,
    ]
    ai_zip_files.extend(ai_parts)
    ai_zip_files = [p for p in ai_zip_files if Path(p).exists()]
    ai_zip_files = prepare_zip_files_for_char_limit(
        ai_zip_files,
        report_dir=report_dir,
        manifest_path=manifest_path,
        readme_path=ai_readme_path,
        max_chars=min(DEFAULT_ZIP_CHAR_LIMIT, AI_REVIEW_ZIP_ENTRY_SAFE_LIMIT),
    )

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
