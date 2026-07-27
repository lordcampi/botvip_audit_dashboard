from __future__ import annotations

"""
test_swing_reminder_scheduler.py — tests for R5C SWING reminder scheduler CLI.

All tests use mocks — no real PostgreSQL, no real Telegram, no real network.

Patch targets reference source modules (not the scheduler) because the
scheduler lazily imports dependencies inside run().
"""

import io
import json
import os
import sys
import tempfile
from contextlib import redirect_stdout
from unittest.mock import MagicMock, patch

import pytest

# Ensure src is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.swing_reminder_scheduler import (
    run,
    _build_reminder_message,
    _dashboard_url,
    DEFAULT_DASHBOARD_URL,
    MAX_REMINDER_MESSAGE_CHARS,
    EXIT_OK,
    EXIT_ERROR,
    EXIT_OFF,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
FAKE_TOKEN = "123456:ABC-DEF1234ghijklmnop"
FAKE_CHAT_ID = "987654321"


@pytest.fixture
def tmp_dirs():
    """Create temporary directories for R4 history and R5 state."""
    with tempfile.TemporaryDirectory() as td:
        base = os.path.join(td, "data", "swing_reviews")
        state_path = os.path.join(td, "swing_reminder_state.json")
        yield base, state_path


def _make_engine_result(should_remind=True, reminder_type="TECH", new_total=5, new_closed=2,
                         cutoff_source="window_end_utc", last_review_id="SWING-20260724-100000-a1b2c3d4"):
    return {
        "should_remind": should_remind,
        "reminder_type": reminder_type,
        "mode": "shadow",
        "reason": f"{reminder_type}: test reason",
        "new_signal_count": new_total,
        "new_closed_count": new_closed,
        "last_review_id": last_review_id,
        "current_fingerprint": "fp_test",
        "candidate_hash": "hash_abc",
        "candidate_message": "test candidate",
        "evaluated_at_utc": "2026-07-27T00:00:00+00:00",
        "control_change_allowed": False,
        "evidence_cutoff_utc": "2026-07-24T00:00:00+00:00",
        "evidence_cutoff_source": cutoff_source,
    }


def _make_sender_success():
    return {
        "success": True,
        "error_class": None,
        "http_status": 200,
        "retryable": False,
        "retry_after_seconds": None,
        "sanitized_reason": "",
    }


def _make_sender_failure(error_class="NETWORK_ERROR", retryable=True, reason="Connection refused",
                          http_status=None, retry_after=None):
    return {
        "success": False,
        "error_class": error_class,
        "http_status": http_status,
        "retryable": retryable,
        "retry_after_seconds": retry_after,
        "sanitized_reason": reason,
    }


# ---------------------------------------------------------------------------
# Helpers: patch targets for lazy imports inside run()
# ---------------------------------------------------------------------------
PG_CONN_PATCH = "src.postgres_readonly.build_readonly_conn"
ENGINE_PATCH = "src.swing_reminder_engine.evaluate_reminder"
SENDER_PATCH = "src.swing_telegram_sender.SwingTelegramSender"


def _mock_pg_ok(mock_build_conn):
    mock_conn = MagicMock()
    mock_build_conn.return_value = mock_conn
    return mock_conn


def _mock_engine(mock_eval, **kwargs):
    mock_eval.return_value = _make_engine_result(**kwargs)


def _mock_sender_success(mock_sender_cls):
    mock_sender = MagicMock()
    mock_sender.send_message.return_value = _make_sender_success()
    mock_sender_cls.return_value = mock_sender
    return mock_sender


def _mock_sender_failure(mock_sender_cls, **kwargs):
    mock_sender = MagicMock()
    mock_sender.send_message.return_value = _make_sender_failure(**kwargs)
    mock_sender_cls.return_value = mock_sender
    return mock_sender


# ---------------------------------------------------------------------------
# Mode: off
# ---------------------------------------------------------------------------
class TestModeOff:
    def test_off_exits_immediately(self):
        with patch.dict(os.environ, {"SWING_TELEGRAM_MODE": "off"}, clear=True):
            code = run(exit_on_complete=False)
            assert code == EXIT_OFF

    def test_off_no_state_mutation(self, tmp_dirs):
        base, state_path = tmp_dirs
        with patch.dict(os.environ, {"SWING_TELEGRAM_MODE": "off"}, clear=True):
            run(exit_on_complete=False)
        assert not os.path.isfile(state_path)

    def test_off_no_pg_connection_attempt(self):
        with patch.dict(os.environ, {"SWING_TELEGRAM_MODE": "off"}, clear=True):
            with patch(PG_CONN_PATCH) as mock_conn:
                run(exit_on_complete=False)
                mock_conn.assert_not_called()


# ---------------------------------------------------------------------------
# Mode: shadow
# ---------------------------------------------------------------------------
class TestModeShadow:
    def test_shadow_evaluates_but_does_not_send(self):
        with patch.dict(os.environ, {
            "SWING_TELEGRAM_MODE": "shadow",
            "SWING_TELEGRAM_BOT_TOKEN": "",
            "SWING_TELEGRAM_CHAT_ID": "",
        }, clear=True):
            with patch(PG_CONN_PATCH) as mock_build_conn, \
                 patch(ENGINE_PATCH) as mock_eval, \
                 patch(SENDER_PATCH) as mock_sender_cls:
                _mock_pg_ok(mock_build_conn)
                _mock_engine(mock_eval, should_remind=True, reminder_type="TECH")
                code = run(exit_on_complete=False)

        mock_sender_cls.assert_not_called()
        assert code == EXIT_OK

    def test_shadow_no_candidate_no_send(self):
        with patch.dict(os.environ, {"SWING_TELEGRAM_MODE": "shadow"}, clear=True):
            with patch(PG_CONN_PATCH) as mock_build_conn, \
                 patch(ENGINE_PATCH) as mock_eval, \
                 patch(SENDER_PATCH) as mock_sender:
                _mock_pg_ok(mock_build_conn)
                _mock_engine(mock_eval, should_remind=False)
                code = run(exit_on_complete=False)

        mock_sender.assert_not_called()
        assert code == EXIT_OK


# ---------------------------------------------------------------------------
# Mode: enabled — success
# ---------------------------------------------------------------------------
class TestEnabledSuccess:
    def test_enabled_sends_and_returns_ok(self):
        with patch.dict(os.environ, {
            "SWING_TELEGRAM_MODE": "enabled",
            "SWING_TELEGRAM_BOT_TOKEN": FAKE_TOKEN,
            "SWING_TELEGRAM_CHAT_ID": FAKE_CHAT_ID,
        }, clear=True):
            with patch(PG_CONN_PATCH) as mock_build_conn, \
                 patch(ENGINE_PATCH) as mock_eval, \
                 patch(SENDER_PATCH) as mock_sender_cls:
                _mock_pg_ok(mock_build_conn)
                _mock_engine(mock_eval, should_remind=True, reminder_type="TECH")
                mock_sender = _mock_sender_success(mock_sender_cls)
                code = run(exit_on_complete=False)

        assert code == EXIT_OK
        mock_sender.send_message.assert_called_once()
        call_arg = mock_sender.send_message.call_args[0][0]
        assert FAKE_TOKEN not in call_arg
        assert FAKE_CHAT_ID not in call_arg
        assert len(call_arg) <= MAX_REMINDER_MESSAGE_CHARS

    def test_enabled_no_candidate_skips_send(self):
        with patch.dict(os.environ, {
            "SWING_TELEGRAM_MODE": "enabled",
            "SWING_TELEGRAM_BOT_TOKEN": FAKE_TOKEN,
            "SWING_TELEGRAM_CHAT_ID": FAKE_CHAT_ID,
        }, clear=True):
            with patch(PG_CONN_PATCH) as mock_build_conn, \
                 patch(ENGINE_PATCH) as mock_eval, \
                 patch(SENDER_PATCH) as mock_sender:
                _mock_pg_ok(mock_build_conn)
                _mock_engine(mock_eval, should_remind=False)
                code = run(exit_on_complete=False)

        mock_sender.assert_not_called()
        assert code == EXIT_OK


# ---------------------------------------------------------------------------
# Mode: enabled — failure
# ---------------------------------------------------------------------------
class TestEnabledFailure:
    def test_enabled_send_failure_exit_nonzero(self):
        with patch.dict(os.environ, {
            "SWING_TELEGRAM_MODE": "enabled",
            "SWING_TELEGRAM_BOT_TOKEN": FAKE_TOKEN,
            "SWING_TELEGRAM_CHAT_ID": FAKE_CHAT_ID,
        }, clear=True):
            with patch(PG_CONN_PATCH) as mock_build_conn, \
                 patch(ENGINE_PATCH) as mock_eval, \
                 patch(SENDER_PATCH) as mock_sender_cls:
                _mock_pg_ok(mock_build_conn)
                _mock_engine(mock_eval, should_remind=True)
                _mock_sender_failure(mock_sender_cls, error_class="NETWORK_ERROR", retryable=True,
                                     reason="Connection refused")
                code = run(exit_on_complete=False)

        assert code == EXIT_ERROR

    def test_enabled_401_not_retryable(self):
        with patch.dict(os.environ, {
            "SWING_TELEGRAM_MODE": "enabled",
            "SWING_TELEGRAM_BOT_TOKEN": FAKE_TOKEN,
            "SWING_TELEGRAM_CHAT_ID": FAKE_CHAT_ID,
        }, clear=True):
            with patch(PG_CONN_PATCH) as mock_build_conn, \
                 patch(ENGINE_PATCH) as mock_eval, \
                 patch(SENDER_PATCH) as mock_sender_cls:
                _mock_pg_ok(mock_build_conn)
                _mock_engine(mock_eval, should_remind=True)
                _mock_sender_failure(mock_sender_cls, error_class="CONFIG_INVALID", retryable=False,
                                     reason="Unauthorized", http_status=401)
                code = run(exit_on_complete=False)

        assert code == EXIT_ERROR

    def test_enabled_429_retryable(self):
        with patch.dict(os.environ, {
            "SWING_TELEGRAM_MODE": "enabled",
            "SWING_TELEGRAM_BOT_TOKEN": FAKE_TOKEN,
            "SWING_TELEGRAM_CHAT_ID": FAKE_CHAT_ID,
        }, clear=True):
            with patch(PG_CONN_PATCH) as mock_build_conn, \
                 patch(ENGINE_PATCH) as mock_eval, \
                 patch(SENDER_PATCH) as mock_sender_cls:
                _mock_pg_ok(mock_build_conn)
                _mock_engine(mock_eval, should_remind=True)
                _mock_sender_failure(mock_sender_cls, error_class="RATE_LIMITED", retryable=True,
                                     reason="Too Many Requests", http_status=429, retry_after=30)
                code = run(exit_on_complete=False)

        assert code == EXIT_ERROR

    def test_enabled_5xx_retryable(self):
        with patch.dict(os.environ, {
            "SWING_TELEGRAM_MODE": "enabled",
            "SWING_TELEGRAM_BOT_TOKEN": FAKE_TOKEN,
            "SWING_TELEGRAM_CHAT_ID": FAKE_CHAT_ID,
        }, clear=True):
            with patch(PG_CONN_PATCH) as mock_build_conn, \
                 patch(ENGINE_PATCH) as mock_eval, \
                 patch(SENDER_PATCH) as mock_sender_cls:
                _mock_pg_ok(mock_build_conn)
                _mock_engine(mock_eval, should_remind=True)
                _mock_sender_failure(mock_sender_cls, error_class="API_ERROR", retryable=True,
                                     reason="Internal Server Error", http_status=502)
                code = run(exit_on_complete=False)

        assert code == EXIT_ERROR

    def test_enabled_missing_token_blocks_send(self):
        with patch.dict(os.environ, {
            "SWING_TELEGRAM_MODE": "enabled",
            "SWING_TELEGRAM_BOT_TOKEN": "",
            "SWING_TELEGRAM_CHAT_ID": "",
        }, clear=True):
            with patch(PG_CONN_PATCH) as mock_build_conn, \
                 patch(ENGINE_PATCH) as mock_eval:
                _mock_pg_ok(mock_build_conn)
                _mock_engine(mock_eval, should_remind=True)
                code = run(exit_on_complete=False)

        assert code == EXIT_ERROR


# ---------------------------------------------------------------------------
# Reminder types and message content
# ---------------------------------------------------------------------------
class TestReminderTypes:
    def test_tech_message_no_control_mention(self):
        with patch.dict(os.environ, {
            "SWING_TELEGRAM_MODE": "enabled",
            "SWING_TELEGRAM_BOT_TOKEN": FAKE_TOKEN,
            "SWING_TELEGRAM_CHAT_ID": FAKE_CHAT_ID,
        }, clear=True):
            with patch(PG_CONN_PATCH) as mock_build_conn, \
                 patch(ENGINE_PATCH) as mock_eval, \
                 patch(SENDER_PATCH) as mock_sender_cls:
                _mock_pg_ok(mock_build_conn)
                _mock_engine(mock_eval, should_remind=True, reminder_type="TECH",
                             new_total=3, new_closed=1)
                mock_sender = _mock_sender_success(mock_sender_cls)
                run(exit_on_complete=False)

        msg = mock_sender.send_message.call_args[0][0]
        assert "Revisión técnica" in msg
        assert "3" in msg
        assert "CONTROL" not in msg

    def test_calib_does_not_authorize_control(self):
        with patch.dict(os.environ, {
            "SWING_TELEGRAM_MODE": "enabled",
            "SWING_TELEGRAM_BOT_TOKEN": FAKE_TOKEN,
            "SWING_TELEGRAM_CHAT_ID": FAKE_CHAT_ID,
        }, clear=True):
            with patch(PG_CONN_PATCH) as mock_build_conn, \
                 patch(ENGINE_PATCH) as mock_eval, \
                 patch(SENDER_PATCH) as mock_sender_cls:
                _mock_pg_ok(mock_build_conn)
                _mock_engine(mock_eval, should_remind=True, reminder_type="CALIB",
                             new_total=50, new_closed=30)
                mock_sender = _mock_sender_success(mock_sender_cls)
                run(exit_on_complete=False)

        msg = mock_sender.send_message.call_args[0][0]
        assert "Solo revisar" in msg
        assert "no modificar CONTROL" in msg


# ---------------------------------------------------------------------------
# Message constraints
# ---------------------------------------------------------------------------
class TestMessageConstraints:
    def test_message_under_500_chars(self):
        with patch.dict(os.environ, {
            "SWING_TELEGRAM_MODE": "enabled",
            "SWING_TELEGRAM_BOT_TOKEN": FAKE_TOKEN,
            "SWING_TELEGRAM_CHAT_ID": FAKE_CHAT_ID,
        }, clear=True):
            with patch(PG_CONN_PATCH) as mock_build_conn, \
                 patch(ENGINE_PATCH) as mock_eval, \
                 patch(SENDER_PATCH) as mock_sender_cls:
                _mock_pg_ok(mock_build_conn)
                _mock_engine(mock_eval, should_remind=True, reminder_type="STRAT",
                             new_total=999, new_closed=999)
                mock_sender = _mock_sender_success(mock_sender_cls)
                run(exit_on_complete=False)

        msg = mock_sender.send_message.call_args[0][0]
        assert len(msg) <= MAX_REMINDER_MESSAGE_CHARS

    def test_message_includes_dashboard_url(self):
        with patch.dict(os.environ, {
            "SWING_TELEGRAM_MODE": "enabled",
            "SWING_TELEGRAM_BOT_TOKEN": FAKE_TOKEN,
            "SWING_TELEGRAM_CHAT_ID": FAKE_CHAT_ID,
        }, clear=True):
            with patch(PG_CONN_PATCH) as mock_build_conn, \
                 patch(ENGINE_PATCH) as mock_eval, \
                 patch(SENDER_PATCH) as mock_sender_cls:
                _mock_pg_ok(mock_build_conn)
                _mock_engine(mock_eval, should_remind=True)
                mock_sender = _mock_sender_success(mock_sender_cls)
                run(exit_on_complete=False)

        msg = mock_sender.send_message.call_args[0][0]
        assert DEFAULT_DASHBOARD_URL in msg

    def test_dashboard_url_configurable(self):
        custom_url = "http://192.168.1.100:8501/Swing_Strategy_Review"
        with patch.dict(os.environ, {
            "SWING_TELEGRAM_MODE": "enabled",
            "SWING_TELEGRAM_BOT_TOKEN": FAKE_TOKEN,
            "SWING_TELEGRAM_CHAT_ID": FAKE_CHAT_ID,
            "SWING_DASHBOARD_URL": custom_url,
        }, clear=True):
            with patch(PG_CONN_PATCH) as mock_build_conn, \
                 patch(ENGINE_PATCH) as mock_eval, \
                 patch(SENDER_PATCH) as mock_sender_cls:
                _mock_pg_ok(mock_build_conn)
                _mock_engine(mock_eval, should_remind=True)
                mock_sender = _mock_sender_success(mock_sender_cls)
                run(exit_on_complete=False)

        msg = mock_sender.send_message.call_args[0][0]
        assert custom_url in msg

    def test_message_no_secrets(self):
        with patch.dict(os.environ, {
            "SWING_TELEGRAM_MODE": "enabled",
            "SWING_TELEGRAM_BOT_TOKEN": FAKE_TOKEN,
            "SWING_TELEGRAM_CHAT_ID": FAKE_CHAT_ID,
        }, clear=True):
            with patch(PG_CONN_PATCH) as mock_build_conn, \
                 patch(ENGINE_PATCH) as mock_eval, \
                 patch(SENDER_PATCH) as mock_sender_cls:
                _mock_pg_ok(mock_build_conn)
                _mock_engine(mock_eval, should_remind=True)
                mock_sender = _mock_sender_success(mock_sender_cls)
                run(exit_on_complete=False)

        msg = mock_sender.send_message.call_args[0][0]
        assert FAKE_TOKEN not in msg
        assert FAKE_CHAT_ID not in msg
        assert "api.telegram.org" not in msg


# ---------------------------------------------------------------------------
# Corrupt state (fail-closed)
# ---------------------------------------------------------------------------
class TestCorruptState:
    def test_corrupt_state_blocks_execution(self, tmp_dirs):
        """Corrupt state is detected via is_valid() → exit immediately, no PG."""
        with patch("src.swing_reminder_state.SwingReminderState.is_valid", return_value=False), \
             patch("os.path.isfile", return_value=True):
            with patch.dict(os.environ, {"SWING_TELEGRAM_MODE": "shadow"}, clear=True):
                with patch(PG_CONN_PATCH) as mock_conn:
                    code = run(exit_on_complete=False)

        assert code == EXIT_ERROR
        mock_conn.assert_not_called()


# ---------------------------------------------------------------------------
# PG / history failure
# ---------------------------------------------------------------------------
class TestInfrastructureFailure:
    def test_pg_connection_failure_blocks_send(self):
        with patch.dict(os.environ, {
            "SWING_TELEGRAM_MODE": "enabled",
            "SWING_TELEGRAM_BOT_TOKEN": FAKE_TOKEN,
            "SWING_TELEGRAM_CHAT_ID": FAKE_CHAT_ID,
        }, clear=True):
            with patch(PG_CONN_PATCH) as mock_build_conn, \
                 patch(SENDER_PATCH) as mock_sender:
                mock_build_conn.side_effect = RuntimeError("Connection refused")
                code = run(exit_on_complete=False)

        mock_sender.assert_not_called()
        assert code == EXIT_ERROR

    def test_engine_exception_blocks_send(self):
        with patch.dict(os.environ, {
            "SWING_TELEGRAM_MODE": "enabled",
            "SWING_TELEGRAM_BOT_TOKEN": FAKE_TOKEN,
            "SWING_TELEGRAM_CHAT_ID": FAKE_CHAT_ID,
        }, clear=True):
            with patch(PG_CONN_PATCH) as mock_build_conn, \
                 patch(ENGINE_PATCH) as mock_eval, \
                 patch(SENDER_PATCH) as mock_sender:
                _mock_pg_ok(mock_build_conn)
                mock_eval.side_effect = RuntimeError("PG query timeout")
                code = run(exit_on_complete=False)

        mock_sender.assert_not_called()
        assert code == EXIT_ERROR


# ---------------------------------------------------------------------------
# Exit codes
# ---------------------------------------------------------------------------
class TestExitCodes:
    def test_off_returns_zero(self):
        with patch.dict(os.environ, {"SWING_TELEGRAM_MODE": "off"}, clear=True):
            code = run(exit_on_complete=False)
        assert code == EXIT_OFF

    def test_shadow_success_returns_zero(self):
        with patch.dict(os.environ, {"SWING_TELEGRAM_MODE": "shadow"}, clear=True):
            with patch(PG_CONN_PATCH) as mock_build_conn, \
                 patch(ENGINE_PATCH) as mock_eval:
                _mock_pg_ok(mock_build_conn)
                _mock_engine(mock_eval, should_remind=True)
                code = run(exit_on_complete=False)
        assert code == EXIT_OK

    def test_enabled_send_success_returns_zero(self):
        with patch.dict(os.environ, {
            "SWING_TELEGRAM_MODE": "enabled",
            "SWING_TELEGRAM_BOT_TOKEN": FAKE_TOKEN,
            "SWING_TELEGRAM_CHAT_ID": FAKE_CHAT_ID,
        }, clear=True):
            with patch(PG_CONN_PATCH) as mock_build_conn, \
                 patch(ENGINE_PATCH) as mock_eval, \
                 patch(SENDER_PATCH) as mock_sender_cls:
                _mock_pg_ok(mock_build_conn)
                _mock_engine(mock_eval, should_remind=True)
                _mock_sender_success(mock_sender_cls)
                code = run(exit_on_complete=False)
        assert code == EXIT_OK

    def test_enabled_send_failure_returns_nonzero(self):
        with patch.dict(os.environ, {
            "SWING_TELEGRAM_MODE": "enabled",
            "SWING_TELEGRAM_BOT_TOKEN": FAKE_TOKEN,
            "SWING_TELEGRAM_CHAT_ID": FAKE_CHAT_ID,
        }, clear=True):
            with patch(PG_CONN_PATCH) as mock_build_conn, \
                 patch(ENGINE_PATCH) as mock_eval, \
                 patch(SENDER_PATCH) as mock_sender_cls:
                _mock_pg_ok(mock_build_conn)
                _mock_engine(mock_eval, should_remind=True)
                _mock_sender_failure(mock_sender_cls)
                code = run(exit_on_complete=False)
        assert code == EXIT_ERROR


# ---------------------------------------------------------------------------
# Output structure and sanitisation
# ---------------------------------------------------------------------------
class TestOutputStructure:
    def test_output_contains_required_keys(self):
        with patch.dict(os.environ, {"SWING_TELEGRAM_MODE": "off"}, clear=True):
            f = io.StringIO()
            with redirect_stdout(f):
                run(exit_on_complete=False)

            output = json.loads(f.getvalue())
            required = ["mode", "evaluated", "candidate", "reminder_type", "sent", "shadow",
                        "reason", "new_signal_count", "new_closed_count", "evidence_cutoff_source",
                        "error_class", "retryable"]
            for key in required:
                assert key in output, f"Missing key: {key}"

    def test_output_no_secrets(self):
        with patch.dict(os.environ, {
            "SWING_TELEGRAM_MODE": "enabled",
            "SWING_TELEGRAM_BOT_TOKEN": FAKE_TOKEN,
            "SWING_TELEGRAM_CHAT_ID": FAKE_CHAT_ID,
        }, clear=True):
            with patch(PG_CONN_PATCH) as mock_build_conn, \
                 patch(ENGINE_PATCH) as mock_eval, \
                 patch(SENDER_PATCH) as mock_sender_cls:
                _mock_pg_ok(mock_build_conn)
                _mock_engine(mock_eval, should_remind=True)
                _mock_sender_success(mock_sender_cls)

                f = io.StringIO()
                with redirect_stdout(f):
                    run(exit_on_complete=False)

            output = f.getvalue()
            assert FAKE_TOKEN not in output
            assert FAKE_CHAT_ID not in output


# ---------------------------------------------------------------------------
# One candidate max
# ---------------------------------------------------------------------------
class TestOneCandidateMax:
    def test_only_one_send_attempt_per_run(self):
        with patch.dict(os.environ, {
            "SWING_TELEGRAM_MODE": "enabled",
            "SWING_TELEGRAM_BOT_TOKEN": FAKE_TOKEN,
            "SWING_TELEGRAM_CHAT_ID": FAKE_CHAT_ID,
        }, clear=True):
            with patch(PG_CONN_PATCH) as mock_build_conn, \
                 patch(ENGINE_PATCH) as mock_eval, \
                 patch(SENDER_PATCH) as mock_sender_cls:
                _mock_pg_ok(mock_build_conn)
                _mock_engine(mock_eval, should_remind=True)
                mock_sender = _mock_sender_success(mock_sender_cls)
                run(exit_on_complete=False)

        assert mock_sender.send_message.call_count == 1


# ---------------------------------------------------------------------------
# Message builder unit tests
# ---------------------------------------------------------------------------
class TestMessageBuilder:
    def test_build_tech_message(self):
        msg = _build_reminder_message("TECH", 3, 1, "SWING-20260724-100000-a1b2c3d4", "GOOD", DEFAULT_DASHBOARD_URL)
        assert "Revisión técnica" in msg
        assert "3" in msg
        assert "1" in msg
        assert DEFAULT_DASHBOARD_URL in msg
        assert "CONTROL" not in msg

    def test_build_calib_message_warns_control(self):
        msg = _build_reminder_message("CALIB", 50, 30, "SWING-20260724-100000-a1b2c3d4",
                                       "DEFENSIVE_REVIEW_ALLOWED", DEFAULT_DASHBOARD_URL)
        assert "no modificar CONTROL" in msg

    def test_build_message_without_review_id(self):
        msg = _build_reminder_message("STRAT", 10, 5, None, None, DEFAULT_DASHBOARD_URL)
        assert "Señales nuevas" in msg

    def test_build_message_under_limit(self):
        msg = _build_reminder_message("TECH", 9999, 9999, "SWING-20260724-100000-a1b2c3d4", "GOOD", DEFAULT_DASHBOARD_URL)
        assert len(msg) <= MAX_REMINDER_MESSAGE_CHARS


# ---------------------------------------------------------------------------
# Dashboard URL
# ---------------------------------------------------------------------------
class TestDashboardURL:
    def test_default_url_is_localhost(self):
        with patch.dict(os.environ, {}, clear=True):
            url = _dashboard_url()
        assert "127.0.0.1" in url
        assert "Swing_Strategy_Review" in url

    def test_custom_url_from_env(self):
        custom = "http://myhost:1234/custom"
        with patch.dict(os.environ, {"SWING_DASHBOARD_URL": custom}, clear=True):
            url = _dashboard_url()
        assert url == custom


# ---------------------------------------------------------------------------
# No forbidden imports
# ---------------------------------------------------------------------------
class TestNoForbiddenImports:
    def test_no_legacy_telegram_import(self):
        path = os.path.join(os.path.dirname(__file__), "..", "src", "swing_reminder_scheduler.py")
        source = open(path, encoding="utf-8").read()
        assert "import telegram_delivery" not in source

    def test_no_requests_import(self):
        path = os.path.join(os.path.dirname(__file__), "..", "src", "swing_reminder_scheduler.py")
        source = open(path, encoding="utf-8").read()
        assert "import requests" not in source

    def test_no_sqlite_import(self):
        path = os.path.join(os.path.dirname(__file__), "..", "src", "swing_reminder_scheduler.py")
        source = open(path, encoding="utf-8").read()
        assert "sqlite3" not in source

    def test_does_not_read_legacy_token_at_runtime(self):
        """Verify scheduler reads SWING_TELEGRAM_*, not TELEGRAM_* (legacy)."""
        with patch.dict(os.environ, {
            "SWING_TELEGRAM_MODE": "enabled",
            "SWING_TELEGRAM_BOT_TOKEN": FAKE_TOKEN,
            "SWING_TELEGRAM_CHAT_ID": FAKE_CHAT_ID,
            # Also set legacy values — they must be ignored
            "TELEGRAM_BOT_TOKEN": "BAD_LEGACY_TOKEN",
            "TELEGRAM_CHAT_ID": "BAD_LEGACY_CHAT",
        }, clear=True):
            with patch(PG_CONN_PATCH) as mock_build_conn, \
                 patch(ENGINE_PATCH) as mock_eval, \
                 patch(SENDER_PATCH) as mock_sender_cls:
                _mock_pg_ok(mock_build_conn)
                _mock_engine(mock_eval, should_remind=True)
                mock_sender = _mock_sender_success(mock_sender_cls)
                run(exit_on_complete=False)

        # Verify sender was constructed with SWING_* values, not legacy
        # (indirect: the message doesn't contain the legacy token)
        call_arg = mock_sender.send_message.call_args[0][0]
        assert "BAD_LEGACY_TOKEN" not in call_arg
        assert "BAD_LEGACY_CHAT" not in call_arg


# ---------------------------------------------------------------------------
# Regression
# ---------------------------------------------------------------------------
class TestRegression:
    def test_engine_imports_still_work(self):
        from src.swing_reminder_engine import evaluate_reminder
        assert evaluate_reminder is not None

    def test_sender_imports_still_work(self):
        from src.swing_telegram_sender import SwingTelegramSender
        assert SwingTelegramSender is not None

    def test_state_imports_still_work(self):
        from src.swing_reminder_state import SwingReminderState
        assert SwingReminderState is not None