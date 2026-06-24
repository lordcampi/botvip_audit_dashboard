"""F5_T04i slim AI ZIP representation for F5_T03b sections.

Read-only/reporting-only helper. It keeps the full F5_T03b JSON generated on
server for audit/debugging, but provides a compact AI-ready summary for ZIP
review so Gemini/Copilot do not need to ingest many f5_t03b part files.
"""
from __future__ import annotations

import json
from typing import Any

SCHEMA_VERSION = "f5_t04i_slim_f5_t03b_sections_v1"
F5_T03B_SLIM_FILENAME = "f5_t03b_integration_sections_slim.json"
DEFAULT_MAX_SECTION_CHARS = 24000
DEFAULT_MAX_SAMPLE_ITEMS = 20
DEFAULT_MAX_DICT_KEYS = 40


def _json_size(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False, default=str))


def _safe_len(value: Any) -> int | None:
    try:
        return len(value)  # type: ignore[arg-type]
    except Exception:
        return None


def _limited_list(items: list[Any], *, max_items: int, max_depth: int) -> dict[str, Any]:
    sample = [
        _summarize_value(item, max_chars=DEFAULT_MAX_SECTION_CHARS, max_items=max_items, max_depth=max_depth - 1)
        for item in items[:max_items]
    ]
    return {
        "type": "list_slimmed",
        "original_count": len(items),
        "sample_count": len(sample),
        "sample": sample,
        "omitted_count": max(0, len(items) - len(sample)),
    }


def _limited_dict(data: dict[str, Any], *, max_keys: int, max_items: int, max_depth: int) -> dict[str, Any]:
    keys = sorted(data.keys(), key=lambda key: _json_size(data.get(key)), reverse=True)
    selected = keys[:max_keys]
    return {
        "type": "dict_slimmed",
        "original_key_count": len(data),
        "included_key_count": len(selected),
        "included_keys": selected,
        "omitted_key_count": max(0, len(data) - len(selected)),
        "items": {
            str(key): _summarize_value(data.get(key), max_chars=DEFAULT_MAX_SECTION_CHARS, max_items=max_items, max_depth=max_depth - 1)
            for key in selected
        },
    }


def _summarize_value(value: Any, *, max_chars: int, max_items: int, max_depth: int) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if max_depth <= 0:
        return {
            "type": type(value).__name__,
            "summary_only": True,
            "original_size_chars": _json_size(value),
            "len": _safe_len(value),
        }
    size = _json_size(value)
    if size <= max_chars:
        return value
    if isinstance(value, list):
        return _limited_list(value, max_items=max_items, max_depth=max_depth)
    if isinstance(value, dict):
        return _limited_dict(value, max_keys=DEFAULT_MAX_DICT_KEYS, max_items=max_items, max_depth=max_depth)
    return {
        "type": type(value).__name__,
        "summary_only": True,
        "original_size_chars": size,
        "len": _safe_len(value),
    }


def _summarize_data_quality_score(section: Any) -> dict[str, Any]:
    if not isinstance(section, dict):
        return {"type": type(section).__name__, "summary": _summarize_value(section, max_chars=12000, max_items=10, max_depth=2)}

    out: dict[str, Any] = {
        "original_size_chars": _json_size(section),
        "note": "Slimmed because this section dominated f5_t03b_integration_sections size. Use JSON 13-18 for detailed AI review.",
    }
    preferred_keys = [
        "score_counts",
        "reason_counts",
        "by_outcome",
        "by_zone",
        "by_symbol",
        "sample_bad",
        "rows",
        "signals",
        "items",
        "data",
    ]
    for key in preferred_keys:
        if key in section:
            value = section[key]
            if isinstance(value, list):
                out[key] = _limited_list(value, max_items=DEFAULT_MAX_SAMPLE_ITEMS, max_depth=3)
            elif isinstance(value, dict):
                out[key] = _summarize_value(value, max_chars=16000, max_items=DEFAULT_MAX_SAMPLE_ITEMS, max_depth=3)
            else:
                out[key] = value
    extra_keys = [key for key in section.keys() if key not in preferred_keys]
    if extra_keys:
        out["extra_keys"] = sorted(extra_keys)
    return out


def build_f5_t03b_slim_sections(full_sections: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(full_sections, dict):
        return {
            "schema_version": SCHEMA_VERSION,
            "source": "f5_t03b_integration_sections",
            "read_only": True,
            "error": "full_sections_not_dict",
            "full_type": type(full_sections).__name__,
        }

    top_sizes = {
        str(key): _json_size(value)
        for key, value in full_sections.items()
    }
    sorted_top_sizes = dict(sorted(top_sizes.items(), key=lambda item: item[1], reverse=True))

    slim: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "source": "f5_t03b_integration_sections_full_generated_on_server",
        "read_only": True,
        "full_original_size_chars_estimate": _json_size(full_sections),
        "top_level_size_chars": sorted_top_sizes,
        "full_file_policy": {
            "full_file_generated_on_server": True,
            "full_file_excluded_from_ai_zip": True,
            "ai_zip_contains_this_slim_file": True,
            "reason": "Full f5_t03b is high-volume derived evidence; specialized JSON 13-18 carry detailed AI review sections.",
        },
        "guardrails": [
            "Do not count this slim file as trades.",
            "Use 16_entity_scope_reconciliation.json for official denominator rules.",
            "Use JSON 13-18 for detailed no-progress, zone, entity, loss contribution, and AI summary diagnostics.",
            "Use full f5_t03b only if a human needs server-side audit/debug detail.",
        ],
        "sections": {},
    }

    keep_exact = {
        "schema_version",
        "source",
        "read_only",
        "double_counting_warning",
        "telegram_notified_consistency_check",
        "mfe_mae_recovery_summary",
        "near_miss_usability_summary",
        "source_diagnostics_references",
    }

    sections = slim["sections"]
    for key, value in full_sections.items():
        if key in {"schema_version", "source", "read_only"}:
            continue
        if key == "data_quality_score_by_signal":
            sections[key] = _summarize_data_quality_score(value)
        elif key in keep_exact:
            sections[key] = value
        else:
            sections[key] = _summarize_value(
                value,
                max_chars=DEFAULT_MAX_SECTION_CHARS,
                max_items=DEFAULT_MAX_SAMPLE_ITEMS,
                max_depth=4,
            )

    return slim
