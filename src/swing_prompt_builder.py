from __future__ import annotations

"""
swing_prompt_builder.py — R3B Copilot prompt builder and ZIP finalisation.

Accepts an R3A draft, validates it, builds 10_prompt_for_copilot.md,
finalises the manifest, and produces a deterministic 11-entry ZIP.

No PostgreSQL, no filesystem writes, no Telegram.
Does NOT mutate input draft.
"""

import json
import zipfile
from datetime import datetime
from io import BytesIO
from typing import Any

from .swing_review_pack_builder import (
    MAX_CHARS_PER_FILE,
    MAX_BYTES_PER_FILE,
    STRATEGY,
    SCHEMA_VERSION as R3A_SCHEMA,
    PROHIBITED,
    _iso,
    _json_dumps,
    _compute_sha256,
    _make_zip_info,
    _scan_sensitive_content,
    _chunk_markdown,
    ZIP_FIXED_COMPRESSION,
)

R3B_SCHEMA_VERSION = "r3b_swing_review_pack_v1"

ALLOWED_DECISIONS = {"OBSERVE", "DATA_INSUFFICIENT", "DATA_QUALITY_PARTIAL", "DO_NOT_CHANGE_CONTROL"}

REQUIRED_FILES_R3A = {
    "01_executive_summary.md",
    "02_runtime_and_control.json",
    "03_data_quality.json",
    "04_official_performance.json",
    "05_lifecycle_and_results.json",
    "06_activation_realism.json",
    "07_demo_compatibility.json",
    "08_shadow_comparison.json",
    "09_calibration_readiness.json",
}

RESTRICTIONS = [
    "Do not invent data — use exclusively the files in this ZIP.",
    "Do not treat missing fields as zero.",
    "Separate official, derived, experimental, and diagnostic evidence.",
    "Do not mix fingerprints — each configuration is independent.",
    "Do not mix SWING signals with OFA, F4, F5, TRUE_SCALP, or other engines.",
    "Separate lifecycle status from official result — they are different dimensions.",
    "Separate same_market_bar from execution_detached — they are different concepts.",
    "Distinguish CANONICAL_FIELD from DERIVED_FROM_TIMESTAMPS.",
    "Do not treat HISTORICAL_FALLBACK_AMBIGUOUS as a confirmed boolean.",
    "Review Demo compatibility as secondary execution observability, not production.",
    "SUBMITTED does not equal FILLED — do not conflate them.",
    "Demo compatibility does not equal production execution.",
    "Identify ACTIVATION_MISMATCH and execution_detached signals explicitly.",
    "Do not consider PF stable with fewer than 30 closed evaluable signals.",
    "Do not ignore that some results were derived from gross_r — annotate derived results.",
    "Do not propose real trading — trading must remain OFF.",
    "Real trading is OFF and must remain OFF.",
    "Do not modify CONTROL automatically.",
    "Do not propose changes to multiple parameters simultaneously.",
    "Do not propose changes to TP/SL/BE/reclaim/Donchian/retest/time stop without sufficient evidence.",
    "Do not promote shadow experiments automatically — they are NOT OFFICIAL.",
    "Experiments are NOT OFFICIAL — do not use them for official decisions.",
    "Do not use scanner diagnostics as official evidence.",
    "Scanner status STALE/LOW/NON-OFFICIAL must not influence PF or readiness.",
    "Conclude OBSERVE or a conservative decision when evidence is insufficient.",
    "Propose at most one reversible experiment at a time.",
    "Every proposal must include need, impact, minimal alternative, measurement, and rollback.",
    "Indicate exactly which project any change applies to: Bot principal / BotVIP or Dashboard / Reporter.",
    "Do not suggest changes to the wrong project.",
    "Maintain the SWING plan order; explain any deviation with technical justification.",
    "Do not execute or apply changes — only analyse and recommend.",
    "Do not generate orders, signals, or Telegram/Binance actions.",
    "Mark any low-confidence conclusions explicitly.",
    "Declare what additional data would be needed to increase confidence.",
    "Use exclusively the files contained in this ZIP.",
]


def _validate_r3a_draft(draft: dict) -> None:
    """Validate an R3A draft is complete, consistent, and ready for prompt."""
    manifest = draft.get("files", {}).get("00_manifest.json")
    if not manifest:
        raise ValueError("Draft missing manifest")

    if isinstance(manifest, str):
        manifest = json.loads(manifest)

    schema = manifest.get("schema_version")
    if schema != R3A_SCHEMA:
        raise ValueError(f"Expected schema {R3A_SCHEMA}, got {schema}")

    strategy = manifest.get("strategy")
    if strategy != STRATEGY:
        raise ValueError(f"Expected strategy {STRATEGY}, got {strategy}")

    if not manifest.get("review_id"):
        raise ValueError("Manifest missing review_id")

    if not manifest.get("generated_at_utc"):
        raise ValueError("Manifest missing generated_at_utc")

    scope = manifest.get("fingerprint_scope")
    if scope not in ("latest_only", "all_mixed"):
        raise ValueError(f"Invalid fingerprint_scope: {scope}")

    if scope == "latest_only" and not manifest.get("selected_fingerprint"):
        raise ValueError("latest_only scope requires non-empty selected_fingerprint")

    if manifest.get("complete_for_copilot") is not False:
        raise ValueError("Draft already marked complete_for_copilot — expected false")

    if manifest.get("prompt_status") != "R3B_PENDING":
        raise ValueError(f"Expected prompt_status R3B_PENDING, got {manifest.get('prompt_status')}")

    readiness_decision = manifest.get("readiness")
    if readiness_decision not in ALLOWED_DECISIONS:
        raise ValueError(f"Invalid readiness decision in manifest: {readiness_decision}")

    # Check required files 01-09 are present
    draft_files = set(draft.get("files", {}).keys())
    missing = REQUIRED_FILES_R3A - draft_files
    if missing:
        raise ValueError(f"Missing required files: {sorted(missing)}")

    if "10_prompt_for_copilot.md" in draft_files:
        raise ValueError("Draft already contains 10_prompt_for_copilot.md")

    # Cross-check readiness consistency
    calib = draft.get("files", {}).get("09_calibration_readiness.json")
    if calib:
        if isinstance(calib, str):
            calib = json.loads(calib)
        calib_data = calib.get("data", {})
        calib_decision = calib_data.get("decision")
        if calib_decision and calib_decision != readiness_decision:
            raise ValueError(
                f"Readiness mismatch: manifest={readiness_decision}, calibration={calib_decision}"
            )

    # Check for credential leaks in draft
    for name, content in draft.get("files", {}).items():
        if name.endswith(".json") and isinstance(content, str):
            _scan_sensitive_content(json.loads(content))
        elif isinstance(content, str):
            _scan_sensitive_content(content)


def build_copilot_prompt(draft: dict) -> str:
    """Build the 10_prompt_for_copilot.md from a validated R3A draft.

    Returns a Markdown string. Never mutates draft.
    """
    _validate_r3a_draft(draft)

    manifest_str = draft["files"]["00_manifest.json"]
    manifest = json.loads(manifest_str) if isinstance(manifest_str, str) else manifest_str
    summary = draft["files"]["01_executive_summary.md"]
    exec_dict = draft.get("executability", {})

    review_id = manifest["review_id"]
    gen_at = manifest["generated_at_utc"]
    window = manifest["window"]
    fp = manifest["selected_fingerprint"]
    scope = manifest["fingerprint_scope"]
    counts = manifest["counts"]
    quality = manifest["quality"]
    readiness = manifest["readiness"]
    warnings = manifest.get("warnings", [])
    scope_val = manifest.get("scope_validation", {})
    file_names = sorted(draft["files"].keys())

    lines = [
        "# SWING Strategy Review — Prompt for Copilot",
        "",
        "## Project context",
        "",
        "**Target project:** Bot principal / BotVIP",
        "**Source project:** Dashboard / Daily AI Reporter (separado)",
        "**Strategy:** SWING_TREND_RECLAIM_V1",
        "**Data source:** Swing Strategy Review Center — PostgreSQL strictly read-only",
        "",
        f"**Review ID:** {review_id}",
        f"**Generated:** {gen_at}",
        f"**Window (UTC):** {window['start_utc']} → {window['end_utc']}",
        f"**Window (Colombia):** {window['start_colombia']} → {window['end_colombia']}",
        f"**Fingerprint:** {fp}",
        f"**Fingerprint scope:** {scope}",
        "",
        "## Sample",
        f"- Signals included: {counts['signal_count']}",
        f"- Closed: {counts['closed_count']}",
        f"- Non-SWING excluded: {scope_val.get('non_swing_excluded_count', 0)}",
        f"- Excluded by fingerprint: {scope_val.get('fingerprint_excluded_count', 0)}",
        "",
        "## Quality",
        f"- Level: {quality['level']}",
    ]
    for r in quality.get("reasons", []):
        lines.append(f"  - {r}")

    lines.extend([
        "",
        "## Readiness",
        f"- Decision: **{readiness}**",
        f"- Control change allowed: false",
    ])
    if warnings:
        lines.append("- Warnings:")
        for w in warnings:
            lines.append(f"  - {w}")

    lines.extend([
        "",
        "## Files in this ZIP",
    ])
    for fn in file_names:
        lines.append(f"- {fn}")

    lines.extend([
        "",
        "## Restrictions",
        "",
        "You MUST follow these restrictions when analysing this review:",
        "",
    ])
    for i, r in enumerate(RESTRICTIONS, 1):
        lines.append(f"{i}. {r}")

    lines.extend([
        "",
        "## Requested response format",
        "",
        "Please respond with exactly these sections:",
        "",
        "# 1. Package validation",
        "- manifest completeness",
        "- files present",
        "- sizes and integrity",
        "- scope and fingerprint",
        "- warnings",
        "",
        "# 2. Data quality",
        "- level and reasons",
        "- missing or unavailable fields",
        "- derived vs canonical coverage",
        "- impact on conclusions",
        "",
        "# 3. Scope and configuration",
        "- strategy and fingerprint",
        "- excluded signals",
        "- mixed config / comparability",
        "",
        "# 4. Official performance",
        "- totals (closed, evaluable, WIN/LOSS/BE/Unknown)",
        "- PF, gross profit, gross loss, net R, avg R",
        "- result sources (canonical vs derived)",
        "- sample limitations",
        "",
        "# 5. Lifecycle and results",
        "- reconciliation and double-counting checks",
        "- lifecycle status vs official result separation",
        "",
        "# 6. Activation realism",
        "- same_market_bar (canonical vs derived)",
        "- retroactive fill and historical ambiguity",
        "- execution_detached as a separate concept",
        "",
        "# 7. Demo compatibility",
        "- REQUESTED / SUBMITTED / FILLED / CANCELLED / ACTIVATION_MISMATCH / UNAVAILABLE",
        "- SUBMITTED ≠ FILLED clarification",
        "- Demo-to-production gap",
        "",
        "# 8. Shadow experiments",
        "- variants and statuses",
        "- comparability",
        "- NOT OFFICIAL — do not compute unofficial PF",
        "",
        "# 9. Overfitting risk",
        "- sample size",
        "- fingerprint coverage",
        "- derived result dominance",
        "- executability gaps",
        "",
        "# 10. Conclusion",
        "Choose exactly one from: OBSERVE / DATA_INSUFFICIENT / DATA_QUALITY_PARTIAL / DO_NOT_CHANGE_CONTROL",
        "Never output: AUTO_CHANGE_PARAMETERS / PROMOTE_AUTOMATICALLY / ENABLE_REAL_TRADING",
        "",
        "# 11. Next minimal action",
        "- observational action suggested",
        "- measurement needed",
        "- suggested window",
        "- success criterion",
        "- rollback if experiment proposed",
        "",
        "# 12. Prompt for VS Code agent",
        "Only include if evidence justifies a specific code change. Format:",
        "- Exact project (Bot principal / BotVIP or Dashboard / Reporter)",
        "- Expected files",
        "- Limits",
        "- Tests required",
        "- No commit/push/deploy automatically",
        "- Rollback plan",
        "- Return point to SWING plan",
        "",
        "If no code change is justified, write exactly: NO CODE CHANGE JUSTIFIED",
    ])

    return "\n".join(lines) + "\n"


def finalize_review_pack_with_prompt(r3a_draft: dict, prompt_text: str | None = None) -> dict:
    """Add the Copilot prompt to the R3A draft and update the manifest.

    Returns a NEW dict (does not mutate r3a_draft).
    Validates the prompt text, builds 10_prompt_for_copilot.md,
    updates manifest to R3B schema, sets complete_for_copilot=true.
    """
    _validate_r3a_draft(r3a_draft)

    if prompt_text is None:
        prompt_text = build_copilot_prompt(r3a_draft)

    # Validate prompt
    _scan_sensitive_content(prompt_text)
    if len(prompt_text) > MAX_CHARS_PER_FILE:
        raise ValueError(
            f"Prompt exceeds {MAX_CHARS_PER_FILE} chars ({len(prompt_text)}). "
            "Use chunking via finalize with chunking support."
        )

    # Copy files from R3A draft
    new_files: dict[str, str] = dict(r3a_draft.get("files", {}))
    new_files["10_prompt_for_copilot.md"] = prompt_text

    # Update manifest
    manifest_str = r3a_draft["files"]["00_manifest.json"]
    manifest = json.loads(manifest_str)

    manifest["schema_version"] = R3B_SCHEMA_VERSION
    manifest["complete_for_copilot"] = True
    manifest["prompt_status"] = "READY"
    manifest["finalization_status"] = "COMPLETE"

    # Rebuild file list
    file_list = [{
        "name": "00_manifest.json",
        "size_chars": None,
        "size_bytes": None,
        "sha256": None,
        "self_referential_size_omitted": True,
        "chunked": False,
        "content_type": "application/json",
    }]
    for name, content in new_files.items():
        if name == "00_manifest.json":
            continue
        encoded = content.encode("utf-8")
        file_list.append({
            "name": name,
            "size_chars": len(content),
            "size_bytes": len(encoded),
            "sha256": _compute_sha256(content),
            "self_referential_size_omitted": False,
            "chunked": False,
            "content_type": "text/markdown" if name.endswith(".md") else "application/json",
        })
    manifest["files"] = file_list

    new_files["00_manifest.json"] = _json_dumps(manifest)

    return {
        "review_id": r3a_draft.get("review_id"),
        "generated_at_utc": r3a_draft.get("generated_at_utc"),
        "readiness": r3a_draft.get("readiness"),
        "files": new_files,
        "warnings": r3a_draft.get("warnings", []),
        "selected_fingerprint": r3a_draft.get("selected_fingerprint"),
        "fingerprint_scope": r3a_draft.get("fingerprint_scope"),
        "signal_count": r3a_draft.get("signal_count", 0),
        "closed_count": r3a_draft.get("closed_count", 0),
        "experimental_count": r3a_draft.get("experimental_count", 0),
    }


def build_final_swing_review_zip(r3a_draft: dict, prompt_text: str | None = None) -> bytes:
    """Build the final 11-entry deterministic ZIP in memory.

    Includes manifest, 01-09 analytical files, and 10_prompt_for_copilot.md.
    """
    final_draft = finalize_review_pack_with_prompt(r3a_draft, prompt_text)

    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=ZIP_FIXED_COMPRESSION) as zf:
        for filename in sorted(final_draft["files"].keys()):
            content = final_draft["files"][filename]
            content_bytes = content.encode("utf-8")
            if len(content) > MAX_CHARS_PER_FILE or len(content_bytes) > MAX_BYTES_PER_FILE:
                raise ValueError(
                    f"File {filename} exceeds limits: chars={len(content)}, bytes={len(content_bytes)}"
                )
            zinfo = _make_zip_info(filename)
            zf.writestr(zinfo, content_bytes)

    return buffer.getvalue()


def build_swing_review_pack_for_download(
    dashboard_data: dict,
    selected_fingerprint: str,
    fingerprint_scope: str,
    window_start_utc: Any,
    window_end_utc: Any,
    window_start_colombia: Any,
    window_end_colombia: Any,
    generated_at_utc: Any,
) -> bytes:
    """Build a complete R3B Swing Review Pack ZIP from dashboard data.

    Thin composition of R3A build_review_contents() + R3B build_final_swing_review_zip().
    Returns deterministic ZIP bytes in memory. Never writes to disk.
    Intended as the single call-site for the Streamlit download button.
    """
    from .swing_review_pack_builder import build_review_contents

    r3a_draft = build_review_contents(
        dashboard_data,
        selected_fingerprint,
        fingerprint_scope,
        window_start_utc,
        window_end_utc,
        window_start_colombia,
        window_end_colombia,
        generated_at_utc,
    )
    return build_final_swing_review_zip(r3a_draft)
