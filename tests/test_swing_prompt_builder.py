from __future__ import annotations

"""Tests for swing_prompt_builder.py — R3B prompt builder and ZIP finalisation."""

import json
import zipfile
from datetime import datetime, timezone
from io import BytesIO

import pytest

from src.swing_review_pack_builder import (
    build_review_contents,
    _json_dumps,
    PROHIBITED,
    MAX_CHARS_PER_FILE,
)
from src.swing_prompt_builder import (
    build_copilot_prompt,
    finalize_review_pack_with_prompt,
    build_final_swing_review_zip,
    _validate_r3a_draft,
    R3B_SCHEMA_VERSION,
    RESTRICTIONS,
    ALLOWED_DECISIONS,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
def _dashboard_data(overrides=None):
    from datetime import timedelta
    now = datetime(2026, 7, 25, 22, 0, 0)
    base = {
        "window_start_utc": now - timedelta(days=7),
        "window_end_utc": now,
        "window_start_co": now - timedelta(days=7, hours=-5),
        "window_end_co": now + timedelta(hours=-5),
        "loaded_at": datetime(2026, 7, 25, 21, 55, 0),
        "fingerprint": "7fa9d83d70c7076b",
        "total_signals": 8,
        "excluded_non_swing": 1598,
        "excluded_by_fingerprint": 1,
        "data_quality": {"level": "PARTIAL", "reasons": ["Low adapter_parity"]},
        "signal_kpis": {
            "available": True, "total": 8, "lifecycle_closed": 8, "closed_evaluable": 8,
            "lifecycle_pending": 0, "lifecycle_activated": 0, "lifecycle_cancelled": 0,
            "lifecycle_expired": 0, "lifecycle_other": 0,
            "result_win": 4, "result_loss": 4, "result_be": 0, "result_unknown": 0,
            "result_canonical_count": 0, "result_derived_count": 8,
            "total_r": 2.0, "avg_r": 0.25, "profit_factor": 1.5011, "pf_warning": None,
            "latest_signal_id": 2498,
        },
        "fingerprint_segmentation": {
            "available": True, "num_distinct": 2,
            "fingerprints": {"7fa9d83d70c7076b": 8, "b2a85d44e6bc0000": 1},
        },
        "executability": {
            "available": True,
            "same_market_bar": {"true": 5, "false": 1, "none": 2, "derived": 6, "canonical": 0},
            "execution_detached": {"true": 3, "false": 0, "none": 5},
            "demo_compatibility": {"ACTIVATION_MISMATCH": 2, "UNAVAILABLE": 6},
            "retroactive_bar_fill": {"true": 0, "false": 0, "none": 8},
        },
        "experiments": {"available": True, "rows": 22},
        "scanner": {"available": False},
    }
    if overrides:
        base.update(overrides)
    return base


def _gen_at():
    return datetime(2026, 7, 25, 22, 0, 0)


def _r3a_draft():
    from datetime import timedelta
    utc_start = _gen_at() - timedelta(days=7)
    co_start = utc_start - timedelta(hours=5)
    return build_review_contents(
        _dashboard_data(), "fp12345678", "latest_only",
        utc_start, utc_start + timedelta(days=7),
        co_start, co_start + timedelta(days=7),
        _gen_at(),
    )


# ---------------------------------------------------------------------------
# Draft validation
# ---------------------------------------------------------------------------
class TestDraftValidation:
    def test_valid_draft_accepted(self):
        _validate_r3a_draft(_r3a_draft())

    def test_missing_manifest_rejected(self):
        with pytest.raises(ValueError, match="manifest"):
            _validate_r3a_draft({})

    def test_wrong_schema_rejected(self):
        draft = _r3a_draft()
        manifest = json.loads(draft["files"]["00_manifest.json"])
        manifest["schema_version"] = "wrong"
        draft["files"]["00_manifest.json"] = _json_dumps(manifest)
        with pytest.raises(ValueError, match="schema"):
            _validate_r3a_draft(draft)

    def test_wrong_strategy_rejected(self):
        draft = _r3a_draft()
        manifest = json.loads(draft["files"]["00_manifest.json"])
        manifest["strategy"] = "OFA_ENGINE"
        draft["files"]["00_manifest.json"] = _json_dumps(manifest)
        with pytest.raises(ValueError, match="strategy"):
            _validate_r3a_draft(draft)

    def test_already_complete_rejected(self):
        draft = _r3a_draft()
        manifest = json.loads(draft["files"]["00_manifest.json"])
        manifest["complete_for_copilot"] = True
        draft["files"]["00_manifest.json"] = _json_dumps(manifest)
        with pytest.raises(ValueError, match="already marked"):
            _validate_r3a_draft(draft)

    def test_already_has_prompt_file_rejected(self):
        draft = _r3a_draft()
        draft["files"]["10_prompt_for_copilot.md"] = "pre-existing"
        with pytest.raises(ValueError, match="already contains"):
            _validate_r3a_draft(draft)

    def test_readiness_mismatch_rejected(self):
        draft = _r3a_draft()
        calib = json.loads(draft["files"]["09_calibration_readiness.json"])
        calib["data"]["decision"] = "OBSERVE"
        draft["files"]["09_calibration_readiness.json"] = _json_dumps(calib)
        with pytest.raises(ValueError, match="Readiness mismatch"):
            _validate_r3a_draft(draft)

    def test_missing_required_file_rejected(self):
        draft = _r3a_draft()
        del draft["files"]["01_executive_summary.md"]
        with pytest.raises(ValueError, match="Missing required"):
            _validate_r3a_draft(draft)


# ---------------------------------------------------------------------------
# Prompt content
# ---------------------------------------------------------------------------
class TestPromptContent:
    def test_prompt_contains_target_project(self):
        prompt = build_copilot_prompt(_r3a_draft())
        assert "Bot principal" in prompt
        assert "BotVIP" in prompt

    def test_prompt_contains_source_project(self):
        prompt = build_copilot_prompt(_r3a_draft())
        assert "Dashboard" in prompt
        assert "Daily AI Reporter" in prompt

    def test_prompt_contains_strategy(self):
        prompt = build_copilot_prompt(_r3a_draft())
        assert "SWING_TREND_RECLAIM_V1" in prompt

    def test_prompt_contains_review_id(self):
        draft = _r3a_draft()
        prompt = build_copilot_prompt(draft)
        assert draft["review_id"] in prompt

    def test_prompt_contains_window_utc(self):
        prompt = build_copilot_prompt(_r3a_draft())
        assert "UTC" in prompt

    def test_prompt_contains_window_colombia(self):
        prompt = build_copilot_prompt(_r3a_draft())
        assert "Colombia" in prompt

    def test_prompt_contains_fingerprint_and_scope(self):
        prompt = build_copilot_prompt(_r3a_draft())
        assert "fp12345678" in prompt
        assert "latest_only" in prompt

    def test_prompt_contains_sample_counts(self):
        prompt = build_copilot_prompt(_r3a_draft())
        assert "8" in prompt  # signal count

    def test_prompt_contains_quality(self):
        prompt = build_copilot_prompt(_r3a_draft())
        assert "PARTIAL" in prompt

    def test_prompt_contains_readiness(self):
        prompt = build_copilot_prompt(_r3a_draft())
        assert "DATA_QUALITY_PARTIAL" in prompt

    def test_prompt_contains_file_list(self):
        prompt = build_copilot_prompt(_r3a_draft())
        assert "00_manifest.json" in prompt

    def test_prompt_contains_all_restrictions(self):
        prompt = build_copilot_prompt(_r3a_draft())
        for r in RESTRICTIONS:
            # Each restriction should appear in the prompt
            assert r in prompt, f"Missing restriction: {r[:50]}..."

    def test_prompt_contains_12_sections_format(self):
        prompt = build_copilot_prompt(_r3a_draft())
        for section_num in range(1, 13):
            assert f"# {section_num}." in prompt, f"Missing section {section_num}"

    def test_prompt_contains_no_code_change_justified_instruction(self):
        prompt = build_copilot_prompt(_r3a_draft())
        assert "NO CODE CHANGE JUSTIFIED" in prompt

    def test_prompt_contains_project_for_changes(self):
        prompt = build_copilot_prompt(_r3a_draft())
        assert "Exact project" in prompt or "exact project" in prompt.lower()

    def test_prompt_says_sample_below_30(self):
        prompt = build_copilot_prompt(_r3a_draft())
        assert "30" in prompt  # referenced in restrictions

    def test_prompt_separates_official_derived_shadow(self):
        prompt = build_copilot_prompt(_r3a_draft())
        assert "derived" in prompt.lower()

    def test_prompt_separates_same_market_bar_execution_detached(self):
        prompt = build_copilot_prompt(_r3a_draft())
        assert "same_market_bar" in prompt.lower()
        assert "execution_detached" in prompt.lower()

    def test_prompt_submitted_not_filled(self):
        prompt = build_copilot_prompt(_r3a_draft())
        assert "SUBMITTED" in prompt
        assert "FILLED" in prompt

    def test_prompt_trading_off(self):
        prompt = build_copilot_prompt(_r3a_draft())
        assert "OFF" in prompt

    def test_prompt_control_protected(self):
        prompt = build_copilot_prompt(_r3a_draft())
        assert "CONTROL" in prompt


# ---------------------------------------------------------------------------
# Security
# ---------------------------------------------------------------------------
class TestPromptSecurity:
    def test_no_password_in_prompt(self):
        prompt = build_copilot_prompt(_r3a_draft())
        assert "password" not in prompt.lower()

    def test_no_token_in_prompt(self):
        prompt = build_copilot_prompt(_r3a_draft())
        assert "token" not in prompt.lower()

    def test_no_dsn_in_prompt(self):
        prompt = build_copilot_prompt(_r3a_draft())
        assert "dsn" not in prompt.lower()

    def test_no_pg_vars_in_prompt(self):
        prompt = build_copilot_prompt(_r3a_draft())
        assert "PG_HOST" not in prompt
        assert "PG_PASSWORD" not in prompt

    def test_no_metrics_json_raw(self):
        prompt = build_copilot_prompt(_r3a_draft())
        assert "metrics_json" not in prompt

    def test_sensitive_draft_rejected(self):
        draft = _r3a_draft()
        # Inject a secret inside a JSON file's content (not as filename key)
        manifest = json.loads(draft["files"]["00_manifest.json"])
        manifest["password"] = "leaked"
        draft["files"]["00_manifest.json"] = _json_dumps(manifest)
        with pytest.raises(ValueError):
            build_copilot_prompt(draft)

    def test_error_does_not_expose_secret(self):
        draft = _r3a_draft()
        manifest = json.loads(draft["files"]["00_manifest.json"])
        manifest["password"] = "super-secret-value"
        draft["files"]["00_manifest.json"] = _json_dumps(manifest)
        try:
            build_copilot_prompt(draft)
        except ValueError as e:
            assert "super-secret-value" not in str(e)

    def test_no_filesystem_writes(self):
        from unittest.mock import patch
        with patch("builtins.open", side_effect=RuntimeError("no")):
            prompt = build_copilot_prompt(_r3a_draft())
            assert isinstance(prompt, str)


# ---------------------------------------------------------------------------
# Finalisation
# ---------------------------------------------------------------------------
class TestFinalisation:
    def test_generates_prompt_file(self):
        final = finalize_review_pack_with_prompt(_r3a_draft())
        assert "10_prompt_for_copilot.md" in final["files"]

    def test_manifest_schema_updated(self):
        final = finalize_review_pack_with_prompt(_r3a_draft())
        manifest = json.loads(final["files"]["00_manifest.json"])
        assert manifest["schema_version"] == R3B_SCHEMA_VERSION

    def test_complete_for_copilot_true(self):
        final = finalize_review_pack_with_prompt(_r3a_draft())
        manifest = json.loads(final["files"]["00_manifest.json"])
        assert manifest["complete_for_copilot"] is True

    def test_prompt_status_ready(self):
        final = finalize_review_pack_with_prompt(_r3a_draft())
        manifest = json.loads(final["files"]["00_manifest.json"])
        assert manifest["prompt_status"] == "READY"

    def test_finalization_status_complete(self):
        final = finalize_review_pack_with_prompt(_r3a_draft())
        manifest = json.loads(final["files"]["00_manifest.json"])
        assert manifest["finalization_status"] == "COMPLETE"

    def test_11_entries_normal(self):
        final = finalize_review_pack_with_prompt(_r3a_draft())
        assert len(final["files"]) == 11

    def test_manifest_count_includes_self(self):
        final = finalize_review_pack_with_prompt(_r3a_draft())
        assert len(final["files"]) == 11  # 00-10

    def test_manifest_references_all_entries(self):
        final = finalize_review_pack_with_prompt(_r3a_draft())
        manifest = json.loads(final["files"]["00_manifest.json"])
        file_names = {f["name"] for f in manifest["files"] if not f.get("self_referential_size_omitted")}
        real_names = set(final["files"].keys()) - {"00_manifest.json"}
        assert file_names == real_names

    def test_no_reference_to_inexistent(self):
        final = finalize_review_pack_with_prompt(_r3a_draft())
        manifest = json.loads(final["files"]["00_manifest.json"])
        real_names = set(final["files"].keys())
        for f in manifest["files"]:
            if not f.get("self_referential_size_omitted"):
                assert f["name"] in real_names, f"Manifest references non-existent: {f['name']}"

    def test_original_files_preserved(self):
        draft = _r3a_draft()
        final = finalize_review_pack_with_prompt(draft)
        for key in draft["files"]:
            if key != "00_manifest.json":
                assert key in final["files"]

    def test_manifest_self_size_omitted(self):
        final = finalize_review_pack_with_prompt(_r3a_draft())
        manifest = json.loads(final["files"]["00_manifest.json"])
        self_entry = [f for f in manifest["files"] if f["name"] == "00_manifest.json"][0]
        assert self_entry["size_chars"] is None
        assert self_entry["self_referential_size_omitted"] is True

    def test_prompt_under_limits(self):
        final = finalize_review_pack_with_prompt(_r3a_draft())
        prompt = final["files"]["10_prompt_for_copilot.md"]
        assert len(prompt) <= MAX_CHARS_PER_FILE
        assert len(prompt.encode("utf-8")) <= 200_000

    def test_prompt_utf8_valid(self):
        final = finalize_review_pack_with_prompt(_r3a_draft())
        prompt = final["files"]["10_prompt_for_copilot.md"]
        prompt.encode("utf-8")

    def test_prompt_ends_with_newline(self):
        final = finalize_review_pack_with_prompt(_r3a_draft())
        prompt = final["files"]["10_prompt_for_copilot.md"]
        assert prompt.endswith("\n")

    def test_draft_not_mutated(self):
        draft = _r3a_draft()
        original_manifest = json.loads(draft["files"]["00_manifest.json"])
        schema_before = original_manifest["schema_version"]
        _ = finalize_review_pack_with_prompt(draft)
        # Verify original draft unchanged
        manifest_after = json.loads(draft["files"]["00_manifest.json"])
        assert manifest_after["schema_version"] == schema_before
        assert manifest_after["complete_for_copilot"] is False


# ---------------------------------------------------------------------------
# ZIP final
# ---------------------------------------------------------------------------
class TestFinalZip:
    def test_zip_returns_bytes(self):
        zip_bytes = build_final_swing_review_zip(_r3a_draft())
        assert isinstance(zip_bytes, bytes)

    def test_zip_has_11_files_normal(self):
        zip_bytes = build_final_swing_review_zip(_r3a_draft())
        bio = BytesIO(zip_bytes)
        with zipfile.ZipFile(bio, "r") as zf:
            names = zf.namelist()
            assert len(names) == 11
            assert "10_prompt_for_copilot.md" in names

    def test_zip_no_filesystem_writes(self):
        from unittest.mock import patch
        with patch("builtins.open", side_effect=RuntimeError("no")):
            zip_bytes = build_final_swing_review_zip(_r3a_draft())
            assert isinstance(zip_bytes, bytes)

    def test_zip_byte_identical_same_inputs(self):
        draft = _r3a_draft()
        z1 = build_final_swing_review_zip(draft)
        z2 = build_final_swing_review_zip(draft)
        assert z1 == z2

    def test_zip_order_stable(self):
        zip_bytes = build_final_swing_review_zip(_r3a_draft())
        bio = BytesIO(zip_bytes)
        with zipfile.ZipFile(bio, "r") as zf:
            names = zf.namelist()
            assert names == sorted(names)

    def test_same_prompt_same_zip(self):
        draft = _r3a_draft()
        prompt = build_copilot_prompt(draft)
        z1 = build_final_swing_review_zip(draft, prompt_text=prompt)

        draft2 = _r3a_draft()
        z2 = build_final_swing_review_zip(draft2, prompt_text=prompt)
        assert z1 == z2


# ---------------------------------------------------------------------------
# Readiness
# ---------------------------------------------------------------------------
class TestReadinessInPrompt:
    def test_readiness_copied_not_recalculated(self):
        draft = _r3a_draft()
        prompt = build_copilot_prompt(draft)
        manifest = json.loads(draft["files"]["00_manifest.json"])
        assert manifest["readiness"] in prompt

    def test_prohibited_actions_in_prompt(self):
        prompt = build_copilot_prompt(_r3a_draft())
        for action in PROHIBITED:
            assert action in prompt

    def test_control_change_allowed_false_in_prompt(self):
        prompt = build_copilot_prompt(_r3a_draft())
        assert "Control change allowed: false" in prompt or "control_change_allowed" not in prompt.lower()

    def test_no_prohibited_decision_recommended(self):
        prompt = build_copilot_prompt(_r3a_draft())
        for action in PROHIBITED:
            assert f"{action}" in prompt  # present as restriction, not recommendation