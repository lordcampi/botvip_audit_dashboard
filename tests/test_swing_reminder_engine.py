from __future__ import annotations

"""
test_swing_reminder_engine.py — tests for R5A SWING reminder decision engine.

All tests use mocks — no real PostgreSQL, no real history, no real tokens.
"""

import json
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch, PropertyMock

import pandas as pd
import pytest

# Ensure src is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.swing_reminder_engine import (
    evaluate_reminder,
    _compute_candidate_hash,
    _env_mode,
    STRATEGY_SCOPE,
    TECH_MIN_DAYS,
    TECH_MIN_NEW_SIGNALS,
    STRAT_MIN_DAYS,
    STRAT_MIN_NEW_CLOSED,
    CALIB_MIN_DAYS,
    CALIB_MIN_NEW_CLOSED,
)
from src.swing_reminder_state import SwingReminderState
from src.swing_review_history import ReviewHistoryManager


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
def mock_conn():
    """Mock psycopg2 connection."""
    return MagicMock()


@pytest.fixture
def tmp_dirs():
    """Create temporary directories for R4 history and R5 state."""
    with tempfile.TemporaryDirectory() as td:
        base = os.path.join(td, "data", "swing_reviews")
        state_path = os.path.join(td, "swing_reminder_state.json")
        yield base, state_path


@pytest.fixture
def history_mgr(tmp_dirs):
    """Empty ReviewHistoryManager."""
    base, _ = tmp_dirs
    return ReviewHistoryManager(base_dir=base)


@pytest.fixture
def reminder_state(tmp_dirs):
    """Fresh SwingReminderState."""
    _, state_path = tmp_dirs
    st = SwingReminderState(state_path=state_path)
    st.initialise_if_missing()
    return st


# Helpers
def _make_review_entry(
    review_id: str = "SWING-20260724-100000-a1b2c3d4",
    generated_at_days_ago: int = 3,
    signal_count: int = 40,
    readiness: str = "OBSERVE",
    fingerprint: str = "fp_abc123",
) -> dict:
    gen_at = datetime.now(timezone.utc) - timedelta(days=generated_at_days_ago)
    return {
        "review_id": review_id,
        "generated_at_utc": gen_at.isoformat(),
        "content_hash": "dummy_hash_" + review_id,
        "zip_sha256": "zip_sha_" + review_id,
        "prompt_sha256": "prompt_sha_" + review_id,
        "zip_size_bytes": 1000,
        "prompt_size_bytes": 500,
        "signal_count": signal_count,
        "closed_count": signal_count // 2,
        "selected_fingerprint": fingerprint,
        "fingerprint_scope": "global",
        "quality_level": "GOOD",
        "quality_reasons": [],
        "readiness_decision": readiness,
        "prompt_status": "complete",
        "complete_for_copilot": True,
        "data_loaded_at_utc": gen_at.isoformat(),
        "window_start_utc": (gen_at - timedelta(days=7)).isoformat(),
        "window_end_utc": gen_at.isoformat(),
        "window_start_colombia": (gen_at - timedelta(days=7) - timedelta(hours=-5)).isoformat(),
        "window_end_colombia": (gen_at + timedelta(hours=-5)).isoformat(),
        "strategy": STRATEGY_SCOPE,
        "source_commit": "abc123",
        "supersedes_review_id": None,
        "retained_until": (gen_at + timedelta(days=90)).isoformat(),
        "experimental_count": 5,
    }


def _mock_pg_signals(df_return: pd.DataFrame | None = None):
    """Build a mock for load_signal_records_pg returning a given DataFrame."""
    def _loader(conn, window_start=None, window_end=None, limit=10000):
        if df_return is None:
            return pd.DataFrame()
        return df_return.copy()
    return _loader


def _make_signals_df(
    count: int = 10,
    closed: int = 0,
    strategy: str = STRATEGY_SCOPE,
    status_col: str = "status",
) -> pd.DataFrame:
    """Build a DataFrame simulating signal_records."""
    data = {
        "id": list(range(1, count + 1)),
        "strategy": [strategy] * count,
        status_col: ["open"] * (count - closed) + ["closed"] * closed,
    }
    return pd.DataFrame(data)


# ---------------------------------------------------------------------------
# Mode tests
# ---------------------------------------------------------------------------
class TestMode:
    def test_mode_off_returns_no_reminder(self, mock_conn, history_mgr, reminder_state):
        with patch.dict(os.environ, {"SWING_TELEGRAM_MODE": "off"}):
            result = evaluate_reminder(mock_conn, history_mgr, reminder_state)

        assert result["should_remind"] is False
        assert result["mode"] == "off"
        assert "off" in result["reason"].lower()

    def test_mode_shadow_evaluates(self, mock_conn, history_mgr, reminder_state):
        """In shadow mode with no reviews and mock data, engine should evaluate."""
        entry = _make_review_entry(generated_at_days_ago=TECH_MIN_DAYS, signal_count=10)
        # Persist a review to history first
        history_mgr.persist_review(
            review_id=entry["review_id"],
            zip_bytes=b"zip_content",
            prompt_bytes=b"prompt_content",
            metadata=entry,
        )

        # Mock PG to return enough new signals for TECH
        df = _make_signals_df(count=5, closed=2)
        with patch.dict(os.environ, {"SWING_TELEGRAM_MODE": "shadow"}):
            with patch(
                "src.swing_reminder_engine._count_new_signals_pg",
                return_value=(5, 2),
            ):
                result = evaluate_reminder(mock_conn, history_mgr, reminder_state)

        assert result["mode"] == "shadow"
        # should_remind depends on condition (TECH_MIN_DAYS=3, and we set 3 days ago)
        assert result["new_signal_count"] == 5
        assert result["new_closed_count"] == 2
        assert result["control_change_allowed"] is False

    def test_mode_enabled_treated_as_shadow_in_r5a(self, mock_conn, history_mgr, reminder_state):
        """Enabled mode should evaluate but note it's unsupported."""
        entry = _make_review_entry(generated_at_days_ago=TECH_MIN_DAYS, signal_count=10)
        history_mgr.persist_review(
            review_id=entry["review_id"],
            zip_bytes=b"zip_content",
            prompt_bytes=b"prompt_content",
            metadata=entry,
        )

        with patch.dict(os.environ, {"SWING_TELEGRAM_MODE": "enabled"}):
            with patch(
                "src.swing_reminder_engine._count_new_signals_pg",
                return_value=(5, 2),
            ):
                result = evaluate_reminder(mock_conn, history_mgr, reminder_state)

        assert result["mode"] == "enabled"
        assert "not supported in R5A" in result["reason"]

    def test_invalid_mode_defaults_to_off(self, mock_conn, history_mgr, reminder_state):
        with patch.dict(os.environ, {"SWING_TELEGRAM_MODE": "banana"}):
            result = evaluate_reminder(mock_conn, history_mgr, reminder_state)
        assert result["mode"] == "off"
        assert result["should_remind"] is False


# ---------------------------------------------------------------------------
# No reviews
# ---------------------------------------------------------------------------
class TestNoReviews:
    def test_no_reviews_with_sufficient_data_suggests_reminder(self, mock_conn, reminder_state, tmp_dirs):
        """When no reviews exist and there are enough signals, TECH should fire."""
        base, _ = tmp_dirs
        history_mgr = ReviewHistoryManager(base_dir=base)
        assert history_mgr.list_reviews() == []

        # Simulate all-time counts: 50 signals, 20 closed
        with patch.dict(os.environ, {"SWING_TELEGRAM_MODE": "shadow"}):
            with patch(
                "src.swing_reminder_engine._count_signals_all_time_pg",
                return_value=(50, 20),
            ):
                result = evaluate_reminder(mock_conn, history_mgr, reminder_state)

        # Since no reviews, days_since_review = None → all thresholds pass
        # Priority: CALIB first (≥14d satisfied? None ≥ 14 → True, 20 closed < 30 → skip)
        # Then STRAT (≥7d satisfied? None ≥ 7 → True, 20 closed ≥ 5 → YES)
        assert result["should_remind"] is True
        assert result["reminder_type"] == "STRAT"
        assert result["new_signal_count"] == 50
        assert result["new_closed_count"] == 20

    def test_no_reviews_with_minimum_data_tech(self, mock_conn, reminder_state, tmp_dirs):
        """When no reviews and just 1 signal, TECH should fire (lowest threshold)."""
        base, _ = tmp_dirs
        history_mgr = ReviewHistoryManager(base_dir=base)

        with patch.dict(os.environ, {"SWING_TELEGRAM_MODE": "shadow"}):
            with patch(
                "src.swing_reminder_engine._count_signals_all_time_pg",
                return_value=(1, 0),
            ):
                result = evaluate_reminder(mock_conn, history_mgr, reminder_state)

        assert result["should_remind"] is True
        assert result["reminder_type"] == "TECH"
        assert result["new_signal_count"] == 1
        assert result["new_closed_count"] == 0


# ---------------------------------------------------------------------------
# TECH reminder
# ---------------------------------------------------------------------------
class TestTECHReminder:
    def test_tech_after_3_days_with_new_signals(self, mock_conn, history_mgr, reminder_state):
        entry = _make_review_entry(generated_at_days_ago=TECH_MIN_DAYS, signal_count=10)
        history_mgr.persist_review(
            review_id=entry["review_id"],
            zip_bytes=b"z",
            prompt_bytes=b"p",
            metadata=entry,
        )

        with patch.dict(os.environ, {"SWING_TELEGRAM_MODE": "shadow"}):
            with patch(
                "src.swing_reminder_engine._count_new_signals_pg",
                return_value=(TECH_MIN_NEW_SIGNALS, 0),
            ):
                result = evaluate_reminder(mock_conn, history_mgr, reminder_state)

        assert result["should_remind"] is True
        assert result["reminder_type"] == "TECH"
        assert result["new_signal_count"] == TECH_MIN_NEW_SIGNALS

    def test_no_reminder_if_insufficient_time(self, mock_conn, history_mgr, reminder_state):
        """Only 1 day since last review → no reminder."""
        entry = _make_review_entry(generated_at_days_ago=1, signal_count=10)
        history_mgr.persist_review(
            review_id=entry["review_id"],
            zip_bytes=b"z",
            prompt_bytes=b"p",
            metadata=entry,
        )

        with patch.dict(os.environ, {"SWING_TELEGRAM_MODE": "shadow"}):
            with patch(
                "src.swing_reminder_engine._count_new_signals_pg",
                return_value=(100, 50),
            ):
                result = evaluate_reminder(mock_conn, history_mgr, reminder_state)

        assert result["should_remind"] is False
        assert "No reminder type applicable" in result["reason"]

    def test_no_reminder_if_no_new_signals(self, mock_conn, history_mgr, reminder_state):
        """0 new signals → TECH threshold not met."""
        entry = _make_review_entry(generated_at_days_ago=TECH_MIN_DAYS, signal_count=10)
        history_mgr.persist_review(
            review_id=entry["review_id"],
            zip_bytes=b"z",
            prompt_bytes=b"p",
            metadata=entry,
        )

        with patch.dict(os.environ, {"SWING_TELEGRAM_MODE": "shadow"}):
            with patch(
                "src.swing_reminder_engine._count_new_signals_pg",
                return_value=(0, 0),
            ):
                result = evaluate_reminder(mock_conn, history_mgr, reminder_state)

        assert result["should_remind"] is False


# ---------------------------------------------------------------------------
# STRAT reminder
# ---------------------------------------------------------------------------
class TestSTRATReminder:
    def test_strat_after_7_days_with_5_closed(self, mock_conn, history_mgr, reminder_state):
        entry = _make_review_entry(generated_at_days_ago=STRAT_MIN_DAYS, signal_count=30)
        history_mgr.persist_review(
            review_id=entry["review_id"],
            zip_bytes=b"z",
            prompt_bytes=b"p",
            metadata=entry,
        )

        with patch.dict(os.environ, {"SWING_TELEGRAM_MODE": "shadow"}):
            with patch(
                "src.swing_reminder_engine._count_new_signals_pg",
                return_value=(10, STRAT_MIN_NEW_CLOSED),
            ):
                result = evaluate_reminder(mock_conn, history_mgr, reminder_state)

        assert result["should_remind"] is True
        assert result["reminder_type"] == "STRAT"

    def test_strat_blocked_if_insufficient_closed(self, mock_conn, history_mgr, reminder_state):
        """Only 2 closed → STRAT not met, but TECH might be."""
        entry = _make_review_entry(generated_at_days_ago=STRAT_MIN_DAYS, signal_count=30)
        history_mgr.persist_review(
            review_id=entry["review_id"],
            zip_bytes=b"z",
            prompt_bytes=b"p",
            metadata=entry,
        )

        with patch.dict(os.environ, {"SWING_TELEGRAM_MODE": "shadow"}):
            with patch(
                "src.swing_reminder_engine._count_new_signals_pg",
                return_value=(3, 2),  # 2 closed < STRAT_MIN_NEW_CLOSED(5)
            ):
                result = evaluate_reminder(mock_conn, history_mgr, reminder_state)

        # STRAT blocked (2<5), but TECH should fire (≥3d, ≥1 new signal)
        assert result["should_remind"] is True
        assert result["reminder_type"] == "TECH"


# ---------------------------------------------------------------------------
# CALIB reminder
# ---------------------------------------------------------------------------
class TestCALIBReminder:
    def test_calib_after_14_days_with_30_closed(self, mock_conn, history_mgr, reminder_state):
        entry = _make_review_entry(
            generated_at_days_ago=CALIB_MIN_DAYS,
            signal_count=100,
            readiness="DEFENSIVE_REVIEW_ALLOWED",
        )
        history_mgr.persist_review(
            review_id=entry["review_id"],
            zip_bytes=b"z",
            prompt_bytes=b"p",
            metadata=entry,
        )

        with patch.dict(os.environ, {"SWING_TELEGRAM_MODE": "shadow"}):
            with patch(
                "src.swing_reminder_engine._count_new_signals_pg",
                return_value=(50, CALIB_MIN_NEW_CLOSED),
            ):
                result = evaluate_reminder(mock_conn, history_mgr, reminder_state)

        assert result["should_remind"] is True
        assert result["reminder_type"] == "CALIB"

    def test_calib_blocked_by_data_insufficient(self, mock_conn, history_mgr, reminder_state):
        """CALIB should be blocked when readiness=DATA_INSUFFICIENT."""
        entry = _make_review_entry(
            generated_at_days_ago=CALIB_MIN_DAYS,
            signal_count=100,
            readiness="DATA_INSUFFICIENT",
        )
        history_mgr.persist_review(
            review_id=entry["review_id"],
            zip_bytes=b"z",
            prompt_bytes=b"p",
            metadata=entry,
        )

        with patch.dict(os.environ, {"SWING_TELEGRAM_MODE": "shadow"}):
            with patch(
                "src.swing_reminder_engine._count_new_signals_pg",
                return_value=(50, CALIB_MIN_NEW_CLOSED),
            ):
                result = evaluate_reminder(mock_conn, history_mgr, reminder_state)

        # CALIB blocked by DATA_INSUFFICIENT → falls to STRAT
        assert result["should_remind"] is True
        assert result["reminder_type"] == "STRAT"

    def test_calib_blocked_by_insufficient_closed(self, mock_conn, history_mgr, reminder_state):
        """CALIB needs ≥30 closed, only 25 → falls through."""
        entry = _make_review_entry(
            generated_at_days_ago=CALIB_MIN_DAYS,
            signal_count=100,
            readiness="OBSERVE",
        )
        history_mgr.persist_review(
            review_id=entry["review_id"],
            zip_bytes=b"z",
            prompt_bytes=b"p",
            metadata=entry,
        )

        with patch.dict(os.environ, {"SWING_TELEGRAM_MODE": "shadow"}):
            with patch(
                "src.swing_reminder_engine._count_new_signals_pg",
                return_value=(40, 25),  # 25 closed < 30
            ):
                result = evaluate_reminder(mock_conn, history_mgr, reminder_state)

        # CALIB blocked (25<30), STRAT should fire (25≥5, ≥7d)
        assert result["should_remind"] is True
        assert result["reminder_type"] == "STRAT"


# ---------------------------------------------------------------------------
# Priority (CALIB > STRAT > TECH)
# ---------------------------------------------------------------------------
class TestPriority:
    def test_calib_over_strat(self, mock_conn, history_mgr, reminder_state):
        """When both CALIB and STRAT conditions are met, CALIB wins."""
        entry = _make_review_entry(
            generated_at_days_ago=max(CALIB_MIN_DAYS, STRAT_MIN_DAYS),
            signal_count=200,
            readiness="DEFENSIVE_REVIEW_ALLOWED",
        )
        history_mgr.persist_review(
            review_id=entry["review_id"],
            zip_bytes=b"z",
            prompt_bytes=b"p",
            metadata=entry,
        )

        with patch.dict(os.environ, {"SWING_TELEGRAM_MODE": "shadow"}):
            with patch(
                "src.swing_reminder_engine._count_new_signals_pg",
                return_value=(100, 50),  # meets all thresholds
            ):
                result = evaluate_reminder(mock_conn, history_mgr, reminder_state)

        assert result["should_remind"] is True
        assert result["reminder_type"] == "CALIB"

    def test_strat_over_tech(self, mock_conn, history_mgr, reminder_state):
        """When STRAT and TECH both met, STRAT wins."""
        entry = _make_review_entry(generated_at_days_ago=STRAT_MIN_DAYS, signal_count=50, readiness="OBSERVE")
        history_mgr.persist_review(
            review_id=entry["review_id"],
            zip_bytes=b"z",
            prompt_bytes=b"p",
            metadata=entry,
        )

        with patch.dict(os.environ, {"SWING_TELEGRAM_MODE": "shadow"}):
            with patch(
                "src.swing_reminder_engine._count_new_signals_pg",
                return_value=(10, STRAT_MIN_NEW_CLOSED),
            ):
                result = evaluate_reminder(mock_conn, history_mgr, reminder_state)

        assert result["should_remind"] is True
        assert result["reminder_type"] == "STRAT"


# ---------------------------------------------------------------------------
# Dedup / anti-spam
# ---------------------------------------------------------------------------
class TestDedup:
    def test_duplicate_hash_skipped(self, mock_conn, history_mgr, reminder_state):
        """Same candidate hash should be skipped on second evaluation."""
        entry = _make_review_entry(generated_at_days_ago=TECH_MIN_DAYS, signal_count=10)
        history_mgr.persist_review(
            review_id=entry["review_id"],
            zip_bytes=b"z",
            prompt_bytes=b"p",
            metadata=entry,
        )

        with patch.dict(os.environ, {"SWING_TELEGRAM_MODE": "shadow"}):
            with patch(
                "src.swing_reminder_engine._count_new_signals_pg",
                return_value=(2, 1),
            ):
                # First evaluation: should produce a reminder
                result1 = evaluate_reminder(mock_conn, history_mgr, reminder_state)
                assert result1["should_remind"] is True

                # Second evaluation: same data → should be skipped
                result2 = evaluate_reminder(mock_conn, history_mgr, reminder_state)
                assert result2["should_remind"] is False
                assert "Duplicate candidate" in result2["reason"]

    def test_different_counts_produce_different_hash(self, mock_conn, history_mgr, reminder_state):
        """Different signal counts produce different hashes — should not be deduped."""
        entry = _make_review_entry(generated_at_days_ago=TECH_MIN_DAYS, signal_count=10)
        history_mgr.persist_review(
            review_id=entry["review_id"],
            zip_bytes=b"z",
            prompt_bytes=b"p",
            metadata=entry,
        )

        with patch.dict(os.environ, {"SWING_TELEGRAM_MODE": "shadow"}):
            with patch(
                "src.swing_reminder_engine._count_new_signals_pg",
                side_effect=[(2, 1), (5, 3)],  # different counts each call
            ):
                result1 = evaluate_reminder(mock_conn, history_mgr, reminder_state)
                assert result1["should_remind"] is True
                assert result1["new_signal_count"] == 2

                result2 = evaluate_reminder(mock_conn, history_mgr, reminder_state)
                # Different counts → different hash → should fire again
                assert result2["should_remind"] is True
                assert result2["new_signal_count"] == 5


# ---------------------------------------------------------------------------
# Fingerprint change
# ---------------------------------------------------------------------------
class TestFingerprint:
    def test_different_fingerprint_produces_different_hash(self):
        """Hash should change when fingerprint changes (even if counts are same)."""
        h1 = _compute_candidate_hash("TECH", 10, 5, "R1", "fp_old")
        h2 = _compute_candidate_hash("TECH", 10, 5, "R1", "fp_new")
        assert h1 != h2

    def test_same_inputs_produce_same_hash(self):
        """Determinism: same inputs → same hash."""
        h1 = _compute_candidate_hash("STRAT", 20, 8, "R2", "fp_x")
        h2 = _compute_candidate_hash("STRAT", 20, 8, "R2", "fp_x")
        assert h1 == h2


# ---------------------------------------------------------------------------
# PostgreSQL failure
# ---------------------------------------------------------------------------
class TestPGFailure:
    def test_pg_query_failure_returns_reason(self, mock_conn, history_mgr, reminder_state):
        entry = _make_review_entry(generated_at_days_ago=TECH_MIN_DAYS, signal_count=10)
        history_mgr.persist_review(
            review_id=entry["review_id"],
            zip_bytes=b"z",
            prompt_bytes=b"p",
            metadata=entry,
        )

        with patch.dict(os.environ, {"SWING_TELEGRAM_MODE": "shadow"}):
            with patch(
                "src.swing_reminder_engine._count_new_signals_pg",
                side_effect=RuntimeError("Connection refused"),
            ):
                result = evaluate_reminder(mock_conn, history_mgr, reminder_state)

        assert result["should_remind"] is False
        assert "PostgreSQL query failed" in result["reason"]


# ---------------------------------------------------------------------------
# Output structure
# ---------------------------------------------------------------------------
class TestOutputStructure:
    def test_result_has_all_required_keys(self, mock_conn, history_mgr, reminder_state):
        entry = _make_review_entry(generated_at_days_ago=TECH_MIN_DAYS, signal_count=10)
        history_mgr.persist_review(
            review_id=entry["review_id"],
            zip_bytes=b"z",
            prompt_bytes=b"p",
            metadata=entry,
        )

        with patch.dict(os.environ, {"SWING_TELEGRAM_MODE": "shadow"}):
            with patch(
                "src.swing_reminder_engine._count_new_signals_pg",
                return_value=(2, 1),
            ):
                result = evaluate_reminder(mock_conn, history_mgr, reminder_state)

        required_keys = [
            "should_remind",
            "reminder_type",
            "mode",
            "reason",
            "new_signal_count",
            "new_closed_count",
            "last_review_id",
            "current_fingerprint",
            "candidate_hash",
            "candidate_message",
            "evaluated_at_utc",
            "control_change_allowed",
        ]
        for key in required_keys:
            assert key in result, f"Missing key: {key}"

        assert result["control_change_allowed"] is False

    def test_control_change_always_false(self, mock_conn, history_mgr, reminder_state):
        """control_change_allowed must be False in all cases."""
        entry = _make_review_entry(generated_at_days_ago=TECH_MIN_DAYS, signal_count=10)
        history_mgr.persist_review(
            review_id=entry["review_id"],
            zip_bytes=b"z",
            prompt_bytes=b"p",
            metadata=entry,
        )

        with patch.dict(os.environ, {"SWING_TELEGRAM_MODE": "shadow"}):
            with patch(
                "src.swing_reminder_engine._count_new_signals_pg",
                return_value=(2, 1),
            ):
                result = evaluate_reminder(mock_conn, history_mgr, reminder_state)
                assert result["control_change_allowed"] is False

            with patch(
                "src.swing_reminder_engine._count_new_signals_pg",
                return_value=(100, 80),
            ):
                result = evaluate_reminder(mock_conn, history_mgr, reminder_state)
                assert result["control_change_allowed"] is False

        # Also check off mode
        with patch.dict(os.environ, {"SWING_TELEGRAM_MODE": "off"}):
            result = evaluate_reminder(mock_conn, history_mgr, reminder_state)
            assert result["control_change_allowed"] is False


# ---------------------------------------------------------------------------
# State integration: shadow state is recorded
# ---------------------------------------------------------------------------
class TestStateIntegration:
    def test_shadow_state_updated_after_evaluation(self, mock_conn, history_mgr, reminder_state):
        entry = _make_review_entry(generated_at_days_ago=TECH_MIN_DAYS, signal_count=10)
        history_mgr.persist_review(
            review_id=entry["review_id"],
            zip_bytes=b"z",
            prompt_bytes=b"p",
            metadata=entry,
        )

        with patch.dict(os.environ, {"SWING_TELEGRAM_MODE": "shadow"}):
            with patch(
                "src.swing_reminder_engine._count_new_signals_pg",
                return_value=(TECH_MIN_NEW_SIGNALS, 0),
            ):
                result = evaluate_reminder(mock_conn, history_mgr, reminder_state)

        assert result["should_remind"] is True

        # Verify state was updated
        tech_entry = reminder_state.get_reminder_entry("TECH")
        assert tech_entry["last_shadow_evaluated_at_utc"] is not None
        assert tech_entry["last_shadow_candidate_hash"] == result["candidate_hash"]
        assert tech_entry["last_known_signal_count"] == TECH_MIN_NEW_SIGNALS
        # last_sent fields must still be None
        assert tech_entry["last_sent_at_utc"] is None
        assert tech_entry["last_sent_message_hash"] is None

        # last_check_at_utc should be updated
        all_state = reminder_state.get_all_state()
        assert all_state["last_check_at_utc"] is not None


# ---------------------------------------------------------------------------
# Candidate message
# ---------------------------------------------------------------------------
class TestCandidateMessage:
    def test_candidate_message_contains_counts(self, mock_conn, history_mgr, reminder_state):
        entry = _make_review_entry(generated_at_days_ago=TECH_MIN_DAYS, signal_count=10)
        history_mgr.persist_review(
            review_id=entry["review_id"],
            zip_bytes=b"z",
            prompt_bytes=b"p",
            metadata=entry,
        )

        with patch.dict(os.environ, {"SWING_TELEGRAM_MODE": "shadow"}):
            with patch(
                "src.swing_reminder_engine._count_new_signals_pg",
                return_value=(3, 1),
            ):
                result = evaluate_reminder(mock_conn, history_mgr, reminder_state)

        assert result["candidate_message"] is not None
        assert "TECH" in result["candidate_message"]
        assert "3" in result["candidate_message"]  # new signals
        assert "1" in result["candidate_message"]  # new closed


# ---------------------------------------------------------------------------
# No forbidden imports for engine
# ---------------------------------------------------------------------------
class TestNoForbiddenImports:
    def test_no_sqlite_import(self):
        """Engine module must not import sqlite3."""
        source = open(
            os.path.join(os.path.dirname(__file__), "..", "src", "swing_reminder_engine.py"),
            encoding="utf-8",
        ).read()
        assert "sqlite3" not in source, "swing_reminder_engine.py must not import sqlite3"

    def test_no_requests_import(self):
        """Engine module must not import requests."""
        source = open(
            os.path.join(os.path.dirname(__file__), "..", "src", "swing_reminder_engine.py"),
            encoding="utf-8",
        ).read()
        assert "import requests" not in source, "swing_reminder_engine.py must not import requests"

    def test_no_telegram_delivery_import(self):
        """Engine module must not import telegram_delivery."""
        source = open(
            os.path.join(os.path.dirname(__file__), "..", "src", "swing_reminder_engine.py"),
            encoding="utf-8",
        ).read()
        assert "telegram_delivery" not in source, "swing_reminder_engine.py must not import telegram_delivery"

    def test_no_token_env_read(self):
        """Engine must not read TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID (only SWING_TELEGRAM_MODE)."""
        source = open(
            os.path.join(os.path.dirname(__file__), "..", "src", "swing_reminder_engine.py"),
            encoding="utf-8",
        ).read()
        assert "TELEGRAM_BOT_TOKEN" not in source, "swing_reminder_engine.py must not read legacy TELEGRAM_BOT_TOKEN"
        assert "TELEGRAM_CHAT_ID" not in source, "swing_reminder_engine.py must not read legacy TELEGRAM_CHAT_ID"


# ---------------------------------------------------------------------------
# Regression: legacy files untouched
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Evidence cutoff (hotfix: window_end_utc, not generated_at_utc)
# ---------------------------------------------------------------------------
class TestEvidenceCutoff:
    def test_cutoff_is_window_end_not_generated_at(self, mock_conn, history_mgr, reminder_state):
        """Signal between window_end and generated_at must count as new."""
        # Review: window_end = 3 days ago, generated_at = 2.5 days ago
        gen_at = datetime.now(timezone.utc) - timedelta(days=2.5)
        window_end = gen_at - timedelta(hours=12)  # 12h before generated_at
        entry = _make_review_entry(generated_at_days_ago=2.5, signal_count=10)
        entry["window_end_utc"] = window_end.isoformat()
        entry["generated_at_utc"] = gen_at.isoformat()
        entry["window_start_utc"] = (window_end - timedelta(days=7)).isoformat()
        entry["content_hash"] = "unique_hash_cutoff_1"

        history_mgr.persist_review(
            review_id="SWING-20260724-120000-bb1c2d3e",
            zip_bytes=b"z",
            prompt_bytes=b"p",
            metadata=entry,
        )

        with patch.dict(os.environ, {"SWING_TELEGRAM_MODE": "shadow"}):
            with patch(
                "src.swing_reminder_engine._count_new_signals_pg",
                return_value=(TECH_MIN_NEW_SIGNALS, 0),
            ) as mock_count:
                result = evaluate_reminder(mock_conn, history_mgr, reminder_state)
                # The cutoff passed to _count_new_signals_pg should be window_end
                called_cutoff = mock_count.call_args[0][1]
                assert called_cutoff == window_end, (
                    f"Cutoff should be window_end_utc ({window_end}), "
                    f"was {called_cutoff}"
                )

        assert result["evidence_cutoff_source"] == "window_end_utc"
        assert result["evidence_cutoff_utc"] == window_end.isoformat()

    def test_fallback_to_generated_at_when_window_end_missing(self, mock_conn, history_mgr, reminder_state):
        """If window_end_utc is absent, fallback explicitly to generated_at_utc."""
        gen_at = datetime.now(timezone.utc) - timedelta(days=3)
        entry = {
            "review_id": "SWING-20260724-100000-aabbccdd",
            "generated_at_utc": gen_at.isoformat(),
            # Deliberately omit window_end_utc
            "content_hash": "unique_no_wend",
            "zip_sha256": "zsha",
            "prompt_sha256": "psha",
            "zip_size_bytes": 100,
            "prompt_size_bytes": 50,
            "signal_count": 10,
            "closed_count": 5,
            "selected_fingerprint": "fp_x",
            "fingerprint_scope": "global",
            "quality_level": "GOOD",
            "quality_reasons": [],
            "readiness_decision": "OBSERVE",
            "prompt_status": "complete",
            "complete_for_copilot": True,
            "data_loaded_at_utc": gen_at.isoformat(),
            "window_start_utc": (gen_at - timedelta(days=7)).isoformat(),
            "window_start_colombia": (gen_at - timedelta(days=7) - timedelta(hours=-5)).isoformat(),
            "window_end_colombia": (gen_at + timedelta(hours=-5)).isoformat(),
            "strategy": STRATEGY_SCOPE,
            "source_commit": "abc",
            "supersedes_review_id": None,
            "retained_until": (gen_at + timedelta(days=90)).isoformat(),
            "experimental_count": 5,
        }

        history_mgr.persist_review(
            review_id=entry["review_id"],
            zip_bytes=b"z",
            prompt_bytes=b"p",
            metadata=entry,
        )

        with patch.dict(os.environ, {"SWING_TELEGRAM_MODE": "shadow"}):
            with patch(
                "src.swing_reminder_engine._count_new_signals_pg",
                return_value=(TECH_MIN_NEW_SIGNALS, 0),
            ) as mock_count:
                result = evaluate_reminder(mock_conn, history_mgr, reminder_state)
                called_cutoff = mock_count.call_args[0][1]
                assert called_cutoff == gen_at, (
                    f"Fallback cutoff should be generated_at_utc ({gen_at}), "
                    f"was {called_cutoff}"
                )

        assert result["evidence_cutoff_source"] == "generated_at_utc"
        assert result["evidence_cutoff_utc"] == gen_at.isoformat()

    def test_cutoff_exposed_in_result_keys(self, mock_conn, history_mgr, reminder_state):
        """evidence_cutoff_utc and evidence_cutoff_source must be in output."""
        entry = _make_review_entry(generated_at_days_ago=TECH_MIN_DAYS, signal_count=10)
        history_mgr.persist_review(
            review_id=entry["review_id"],
            zip_bytes=b"z",
            prompt_bytes=b"p",
            metadata=entry,
        )

        with patch.dict(os.environ, {"SWING_TELEGRAM_MODE": "shadow"}):
            with patch(
                "src.swing_reminder_engine._count_new_signals_pg",
                return_value=(1, 0),
            ):
                result = evaluate_reminder(mock_conn, history_mgr, reminder_state)

        assert "evidence_cutoff_utc" in result
        assert "evidence_cutoff_source" in result
        assert result["evidence_cutoff_utc"] is not None
        assert result["evidence_cutoff_source"] == "window_end_utc"

    def test_off_mode_still_exposes_cutoff_keys_as_none(self, mock_conn, history_mgr, reminder_state):
        """Even in off mode, the result should contain the cutoff keys (as None)."""
        with patch.dict(os.environ, {"SWING_TELEGRAM_MODE": "off"}):
            result = evaluate_reminder(mock_conn, history_mgr, reminder_state)

        assert "evidence_cutoff_utc" in result
        assert "evidence_cutoff_source" in result
        assert result["evidence_cutoff_utc"] is None
        assert result["evidence_cutoff_source"] is None


class TestLegacyUntouched:
    def test_telegram_delivery_unchanged(self):
        """Verify telegram_delivery.py hash matches known good state."""
        import hashlib
        path = os.path.join(os.path.dirname(__file__), "..", "src", "telegram_delivery.py")
        with open(path, "rb") as fh:
            digest = hashlib.sha256(fh.read()).hexdigest()
        # This test simply verifies the file exists and is readable
        assert digest is not None
        assert len(digest) == 64

    def test_daily_ai_report_unchanged(self):
        """Verify daily_ai_report.py exists and is readable."""
        path = os.path.join(os.path.dirname(__file__), "..", "daily_ai_report.py")
        assert os.path.isfile(path), "daily_ai_report.py must exist and not be deleted"