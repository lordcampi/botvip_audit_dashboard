from __future__ import annotations

"""
test_swing_reminder_state.py — tests for R5A SwingReminderState persistence.
"""

import json
import os
import sys
import tempfile
import threading
import time
from unittest.mock import patch

import pytest

# Ensure src is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Module under test — loaded dynamically to allow patching
import src.swing_reminder_state as mod
from src.swing_reminder_state import (
    SwingReminderState,
    _DEFAULT_TYPE_ENTRY,
    VALID_REMINDER_TYPES,
    STATE_SCHEMA_VERSION,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
def tmp_state_path():
    """Temporary state path that is cleaned up after each test."""
    with tempfile.TemporaryDirectory() as td:
        yield os.path.join(td, "swing_reminder_state.json")


@pytest.fixture
def fresh_state(tmp_state_path):
    """A SwingReminderState instance with a fresh file."""
    st = SwingReminderState(state_path=tmp_state_path)
    st.initialise_if_missing()
    return st


# ---------------------------------------------------------------------------
# Initialisation
# ---------------------------------------------------------------------------
class TestInitialisation:
    def test_init_creates_file_on_initialise(self, tmp_state_path):
        st = SwingReminderState(state_path=tmp_state_path)
        assert not os.path.isfile(tmp_state_path)
        created = st.initialise_if_missing()
        assert created is True
        assert os.path.isfile(tmp_state_path)

        # Verify content
        with open(tmp_state_path, "r") as fh:
            data = json.load(fh)
        assert data["schema_version"] == STATE_SCHEMA_VERSION
        assert set(data["reminders"].keys()) == set(VALID_REMINDER_TYPES)
        for t in VALID_REMINDER_TYPES:
            assert data["reminders"][t] == _DEFAULT_TYPE_ENTRY

    def test_initialise_idempotent(self, fresh_state, tmp_state_path):
        st2 = SwingReminderState(state_path=tmp_state_path)
        created = st2.initialise_if_missing()
        assert created is False

    def test_load_returns_defaults_when_file_missing(self, tmp_state_path):
        st = SwingReminderState(state_path=tmp_state_path)
        # get_reminder_entry should return defaults (read-only) without error
        entry = st.get_reminder_entry("TECH")
        assert entry == _DEFAULT_TYPE_ENTRY

    def test_get_all_state_returns_defaults_when_file_missing(self, tmp_state_path):
        st = SwingReminderState(state_path=tmp_state_path)
        data = st.get_all_state()
        assert data["schema_version"] == STATE_SCHEMA_VERSION
        assert set(data["reminders"].keys()) == set(VALID_REMINDER_TYPES)


# ---------------------------------------------------------------------------
# Atomic write
# ---------------------------------------------------------------------------
class TestAtomicWrite:
    def test_atomic_write_does_not_corrupt_on_crash(self, fresh_state, tmp_state_path):
        """Simulate a crash during write — the file should remain intact."""
        fresh_state.record_shadow_evaluation("TECH", "hash_abc", 5, 2)

        # Read current content
        with open(tmp_state_path, "r") as fh:
            before = fh.read()

        # Monkey-patch os.replace to simulate failure after temp write
        original_replace = os.replace
        def _failing_replace(src, dst):
            raise OSError("Simulated crash during replace")

        with patch("os.replace", side_effect=_failing_replace):
            with pytest.raises(OSError, match="Simulated crash"):
                fresh_state.record_shadow_evaluation("STRAT", "hash_xyz", 10, 4)

        # File content should be unchanged (atomic write failed and was rolled back)
        with open(tmp_state_path, "r") as fh:
            after = fh.read()
        assert after == before

        # Temp files should be cleaned up
        parent = os.path.dirname(tmp_state_path)
        tmp_files = [f for f in os.listdir(parent) if f.startswith(".rst_tmp_")]
        assert len(tmp_files) == 0

    def test_write_preserves_all_types(self, fresh_state):
        fresh_state.record_shadow_evaluation("TECH", "h1", 5, 2)
        data = fresh_state.get_all_state()
        assert data["reminders"]["TECH"]["last_shadow_candidate_hash"] == "h1"
        assert data["reminders"]["TECH"]["last_known_signal_count"] == 5
        # STRAT and CALIB should remain at defaults
        assert data["reminders"]["STRAT"]["last_shadow_candidate_hash"] is None
        assert data["reminders"]["CALIB"]["last_shadow_candidate_hash"] is None

    def test_update_reminder_entry_filters_unknown_keys(self, fresh_state):
        fresh_state.update_reminder_entry("TECH", {
            "last_known_signal_count": 99,
            "bogus_key_xyz": "should_be_ignored",
        })
        entry = fresh_state.get_reminder_entry("TECH")
        assert entry["last_known_signal_count"] == 99
        assert "bogus_key_xyz" not in entry


# ---------------------------------------------------------------------------
# File lock / concurrency
# ---------------------------------------------------------------------------
class TestFileLock:
    def test_lock_prevents_concurrent_write(self, fresh_state, tmp_state_path):
        """Two threads should not corrupt the file."""
        errors = []
        results = []

        def writer(thread_id: int):
            try:
                st = SwingReminderState(state_path=tmp_state_path)
                st.record_shadow_evaluation(
                    "TECH", f"hash_t{thread_id}", thread_id * 10, thread_id
                )
                results.append(thread_id)
            except Exception as e:
                errors.append(str(e))

        t1 = threading.Thread(target=writer, args=(1,))
        t2 = threading.Thread(target=writer, args=(2,))
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        assert len(errors) == 0, f"Concurrent writes failed: {errors}"
        assert len(results) == 2

        # File should be valid JSON
        with open(tmp_state_path, "r") as fh:
            data = json.load(fh)
        assert isinstance(data, dict)
        assert "reminders" in data

    def test_lock_timeout_raises(self, fresh_state, tmp_state_path):
        """If lock is held too long, TimeoutError should be raised."""
        lock_path = tmp_state_path + ".lock"
        # Hold the lock manually
        with mod._FileLock(lock_path, timeout=0.1):
            # Try to acquire from another lock instance (simulates another process)
            lock2 = mod._FileLock(lock_path, timeout=0.1)
            with pytest.raises(TimeoutError):
                lock2.acquire()


# ---------------------------------------------------------------------------
# Corruption handling (fail-closed)
# ---------------------------------------------------------------------------
class TestCorruption:
    def test_corrupt_json_raises_on_write(self, tmp_state_path):
        """Write corrupt JSON, then try to write — first mutation must raise."""
        st = SwingReminderState(state_path=tmp_state_path)
        st.initialise_if_missing()

        # Corrupt the file
        with open(tmp_state_path, "w", encoding="utf-8") as fh:
            fh.write("this is not valid json {{{")

        # First write operation must raise (corrupt file detected)
        with pytest.raises(RuntimeError, match="corrupt"):
            st.update_reminder_entry("TECH", {"last_known_signal_count": 1})

        # After the corrupt file is archived, a fresh instance can write normally
        st2 = SwingReminderState(state_path=tmp_state_path)
        st2.initialise_if_missing()
        st2.record_shadow_evaluation("TECH", "h", 1, 1)
        entry = st2.get_reminder_entry("TECH")
        assert entry["last_known_signal_count"] == 1

    def test_corrupt_json_read_raises(self, tmp_state_path):
        """get_reminder_entry on corrupt file should raise RuntimeError."""
        st = SwingReminderState(state_path=tmp_state_path)
        st.initialise_if_missing()

        with open(tmp_state_path, "w", encoding="utf-8") as fh:
            fh.write("{{{")

        with pytest.raises(RuntimeError, match="corrupt"):
            st.get_reminder_entry("TECH")

    def test_corrupt_file_archived(self, tmp_state_path):
        """Corrupt file is renamed, not silently overwritten."""
        st = SwingReminderState(state_path=tmp_state_path)
        st.initialise_if_missing()
        assert os.path.isfile(tmp_state_path)

        with open(tmp_state_path, "w") as fh:
            fh.write("{{{")

        try:
            st.update_reminder_entry("TECH", {"last_known_signal_count": 1})
        except RuntimeError:
            pass

        # Original path should now NOT exist (was archived)
        # But the archived copy should exist
        parent = os.path.dirname(tmp_state_path)
        corrupt_files = [f for f in os.listdir(parent) if ".corrupted_" in f]
        assert len(corrupt_files) >= 1

    def test_missing_reminders_key_raises(self, tmp_state_path):
        """If JSON is valid but missing 'reminders' key, should raise."""
        st = SwingReminderState(state_path=tmp_state_path)
        st.initialise_if_missing()

        # Write valid JSON but without 'reminders'
        with open(tmp_state_path, "w", encoding="utf-8") as fh:
            json.dump({"schema_version": STATE_SCHEMA_VERSION, "bogus": 123}, fh)

        with pytest.raises(RuntimeError, match="corrupt"):
            st.update_reminder_entry("TECH", {"last_known_signal_count": 1})


# ---------------------------------------------------------------------------
# Record shadow evaluation (does NOT touch last_sent_*)
# ---------------------------------------------------------------------------
class TestShadowEvaluation:
    def test_record_shadow_does_not_touch_last_sent(self, fresh_state):
        fresh_state.record_shadow_evaluation("TECH", "hash_shadow", 10, 3, "REV-1")

        entry = fresh_state.get_reminder_entry("TECH")
        assert entry["last_shadow_evaluated_at_utc"] is not None
        assert entry["last_shadow_candidate_hash"] == "hash_shadow"
        assert entry["last_known_signal_count"] == 10
        assert entry["last_known_closed_count"] == 3
        assert entry["last_known_latest_review_id"] == "REV-1"
        # Sentinel: last_sent fields must NOT be touched
        assert entry["last_sent_at_utc"] is None
        assert entry["last_sent_message_hash"] is None

    def test_record_shadow_updates_only_target_type(self, fresh_state):
        fresh_state.record_shadow_evaluation("TECH", "h_tech", 1, 1)
        fresh_state.record_shadow_evaluation("STRAT", "h_strat", 2, 2)

        tech = fresh_state.get_reminder_entry("TECH")
        strat = fresh_state.get_reminder_entry("STRAT")
        calib = fresh_state.get_reminder_entry("CALIB")

        assert tech["last_known_signal_count"] == 1
        assert strat["last_known_signal_count"] == 2
        assert calib["last_known_signal_count"] == 0  # untouched

    def test_record_check_updates_timestamp(self, fresh_state):
        before = fresh_state.get_all_state()
        assert before["last_check_at_utc"] is None

        fresh_state.record_check()
        after = fresh_state.get_all_state()
        assert after["last_check_at_utc"] is not None


# ---------------------------------------------------------------------------
# Error counter
# ---------------------------------------------------------------------------
class TestErrorCounter:
    def test_error_counter_increments(self, fresh_state):
        fresh_state.record_telegram_error("Connection refused")
        fresh_state.record_telegram_error("Timeout")

        data = fresh_state.get_all_state()
        assert data["telegram_errors_since_last_ok"] == 2
        assert data["telegram_last_error_message"] == "Timeout"

    def test_error_message_truncated(self, fresh_state):
        long_msg = "x" * 1000
        fresh_state.record_telegram_error(long_msg)
        data = fresh_state.get_all_state()
        assert len(data["telegram_last_error_message"]) == 500

    def test_reset_error_counter(self, fresh_state):
        fresh_state.record_telegram_error("Err1")
        fresh_state.reset_telegram_error_counter()
        data = fresh_state.get_all_state()
        assert data["telegram_errors_since_last_ok"] == 0
        assert data["telegram_last_error_at_utc"] is None
        assert data["telegram_last_error_message"] is None


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------
class TestValidation:
    def test_invalid_reminder_type_raises(self, fresh_state):
        with pytest.raises(ValueError, match="Invalid reminder_type"):
            fresh_state.get_reminder_entry("BOGUS")
        with pytest.raises(ValueError, match="Invalid reminder_type"):
            fresh_state.update_reminder_entry("BOGUS", {})

    def test_is_valid_returns_true_for_no_file(self, tmp_state_path):
        st = SwingReminderState(state_path=tmp_state_path)
        assert st.is_valid() is True

    def test_is_valid_returns_false_for_corrupt(self, fresh_state, tmp_state_path):
        with open(tmp_state_path, "w", encoding="utf-8") as fh:
            fh.write("{{{")
        st2 = SwingReminderState(state_path=tmp_state_path)
        assert st2.is_valid() is False

    def test_is_valid_returns_true_for_good(self, fresh_state, tmp_state_path):
        st2 = SwingReminderState(state_path=tmp_state_path)
        assert st2.is_valid() is True


# ---------------------------------------------------------------------------
# No forbidden imports
# ---------------------------------------------------------------------------
class TestNoForbiddenImports:
    def test_no_psycopg2_import(self):
        """Reminder state module must not import psycopg2."""
        source = open(mod.__file__, encoding="utf-8").read()
        assert "psycopg2" not in source, "swing_reminder_state.py must not import psycopg2"

    def test_no_sqlite_import(self):
        """Reminder state module must not import sqlite3."""
        source = open(mod.__file__, encoding="utf-8").read()
        assert "sqlite3" not in source, "swing_reminder_state.py must not import sqlite3"

    def test_no_requests_import(self):
        """Reminder state module must not import requests."""
        source = open(mod.__file__, encoding="utf-8").read()
        assert "import requests" not in source, "swing_reminder_state.py must not import requests"

    def test_no_telegram_delivery_import(self):
        """Reminder state module must not import telegram_delivery."""
        source = open(mod.__file__, encoding="utf-8").read()
        assert "telegram_delivery" not in source, "swing_reminder_state.py must not import telegram_delivery"