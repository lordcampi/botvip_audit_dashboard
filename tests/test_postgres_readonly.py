from __future__ import annotations

"""Tests for postgres_readonly.py — comprehensive security guard coverage."""

import os
import re
from unittest.mock import MagicMock, patch

import pytest

import src.postgres_readonly as pg_mod


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _set_all_env(monkeypatch):
    monkeypatch.setenv("PG_HOST", "testhost")
    monkeypatch.setenv("PG_PORT", "5432")
    monkeypatch.setenv("PG_DATABASE", "testdb")
    monkeypatch.setenv("PG_USER", "reader")
    monkeypatch.setenv("PG_PASSWORD", "testpass")


# ---------------------------------------------------------------------------
# get_pg_config
# ---------------------------------------------------------------------------
class TestGetPgConfig:
    def test_missing_env_vars_raises(self, monkeypatch):
        monkeypatch.delenv("PG_HOST", raising=False)
        monkeypatch.delenv("PG_DATABASE", raising=False)
        monkeypatch.delenv("PG_USER", raising=False)
        monkeypatch.delenv("PG_PASSWORD", raising=False)
        # Prevent load_dotenv from re-loading .env file vars
        with patch.object(pg_mod, "_ensure_env", return_value=None):
            with pytest.raises(RuntimeError, match="Missing PostgreSQL"):
                pg_mod.get_pg_config()

    def test_all_vars_present_returns_config(self, monkeypatch):
        monkeypatch.setenv("PG_HOST", "localhost")
        monkeypatch.setenv("PG_PORT", "5433")
        monkeypatch.setenv("PG_DATABASE", "testdb")
        monkeypatch.setenv("PG_USER", "reader")
        monkeypatch.setenv("PG_PASSWORD", "secret")
        cfg = pg_mod.get_pg_config()
        assert cfg["host"] == "localhost"
        assert cfg["port"] == 5433
        assert cfg["dbname"] == "testdb"
        assert cfg["user"] == "reader"
        assert cfg["password"] == "secret"
        assert cfg["application_name"] == "botvip_swing_review_center"

    def test_config_does_not_expose_dsn(self, monkeypatch):
        """Error messages must not contain host/user/password."""
        monkeypatch.delenv("PG_HOST", raising=False)
        monkeypatch.delenv("PG_DATABASE", raising=False)
        monkeypatch.setenv("PG_USER", "reader")
        monkeypatch.setenv("PG_PASSWORD", "secret")
        with pytest.raises(RuntimeError) as exc_info:
            pg_mod.get_pg_config()
        msg = str(exc_info.value)
        assert "secret" not in msg
        assert "reader" not in msg.lower()


# ---------------------------------------------------------------------------
# Query guards — SELECT permitted, INSERT/UPDATE/DELETE rejected
# ---------------------------------------------------------------------------
class TestQueryGuards:
    def test_select_allowed(self):
        mock_conn = MagicMock()
        # Patch to avoid actual DB call
        with patch("pandas.read_sql_query", return_value=MagicMock()):
            df = pg_mod.read_sql_df_pg(mock_conn, "SELECT 1")
            assert df is not None

    def test_with_select_allowed(self):
        mock_conn = MagicMock()
        clean_cte = "WITH mycte AS (SELECT * FROM signal_records) SELECT * FROM mycte"
        with patch("pandas.read_sql_query", return_value=MagicMock()):
            df = pg_mod.read_sql_df_pg(mock_conn, clean_cte)
            assert df is not None

    def test_insert_rejected(self):
        mock_conn = MagicMock()
        with pytest.raises(ValueError, match="Only SELECT"):
            pg_mod.read_sql_df_pg(mock_conn, "INSERT INTO x VALUES (1)")

    def test_update_rejected(self):
        mock_conn = MagicMock()
        with pytest.raises(ValueError, match="Only SELECT"):
            pg_mod.read_sql_df_pg(mock_conn, "UPDATE x SET y=1")

    def test_delete_rejected(self):
        mock_conn = MagicMock()
        with pytest.raises(ValueError, match="Only SELECT"):
            pg_mod.read_sql_df_pg(mock_conn, "DELETE FROM x")

    def test_ddl_rejected(self):
        mock_conn = MagicMock()
        with pytest.raises(ValueError, match="Only SELECT"):
            pg_mod.read_sql_df_pg(mock_conn, "CREATE TABLE x (y int)")

    def test_multi_statement_rejected(self):
        mock_conn = MagicMock()
        with pytest.raises(ValueError, match="Multiple statements"):
            pg_mod.read_sql_df_pg(mock_conn, "SELECT 1; DROP TABLE x;")

    def test_multi_statement_read_rows_rejected(self):
        mock_conn = MagicMock()
        with pytest.raises(ValueError, match="Multiple statements"):
            pg_mod.read_rows_pg(mock_conn, "SELECT 1; SELECT 2;")

    def test_with_containing_delete_rejected(self):
        mock_conn = MagicMock()
        malicious = "WITH x AS (DELETE FROM signal_records RETURNING id) SELECT * FROM x"
        with pytest.raises(ValueError, match="WITH query contains write operations"):
            pg_mod.read_sql_df_pg(mock_conn, malicious)

    def test_with_containing_update_rejected(self):
        mock_conn = MagicMock()
        malicious = "WITH x AS (UPDATE signal_records SET status='x' RETURNING id) SELECT * FROM x"
        with pytest.raises(ValueError, match="WITH query contains write operations"):
            pg_mod.read_sql_df_pg(mock_conn, malicious)

    def test_with_containing_insert_rejected(self):
        mock_conn = MagicMock()
        malicious = "WITH x AS (INSERT INTO signal_records VALUES (1)) SELECT * FROM x"
        with pytest.raises(ValueError, match="WITH query contains write operations"):
            pg_mod.read_sql_df_pg(mock_conn, malicious)


# ---------------------------------------------------------------------------
# Error sanitization
# ---------------------------------------------------------------------------
class TestErrorSanitization:
    def test_dsn_not_in_error_message(self):
        """Errors from read_sql_df_pg must not leak connection details."""
        mock_conn = MagicMock()
        with patch("pandas.read_sql_query", side_effect=Exception("connection failed host=secret password=abc123")):
            with pytest.raises(RuntimeError) as exc_info:
                pg_mod.read_sql_df_pg(mock_conn, "SELECT 1")
            msg = str(exc_info.value)
            assert "host=" not in msg
            assert "password" not in msg.lower()
            assert "secret" not in msg


# ---------------------------------------------------------------------------
# build_readonly_conn error when psycopg2 absent
# ---------------------------------------------------------------------------
class TestBuildReadonlyConn:
    def test_psycopg2_not_installed_raises(self, monkeypatch):
        monkeypatch.setattr(pg_mod, "psycopg2", None)
        monkeypatch.setenv("PG_HOST", "x")
        monkeypatch.setenv("PG_DATABASE", "x")
        monkeypatch.setenv("PG_USER", "x")
        monkeypatch.setenv("PG_PASSWORD", "x")
        with pytest.raises(RuntimeError, match="psycopg2"):
            pg_mod.build_readonly_conn()


# ---------------------------------------------------------------------------
# table_exists_pg / table_columns_pg
# ---------------------------------------------------------------------------
class TestTableInfo:
    def test_table_exists_returns_true(self):
        mock_conn = MagicMock()
        with patch.object(pg_mod, "read_rows_pg", return_value=[{"?column?": 1}]):
            assert pg_mod.table_exists_pg(mock_conn, "some_table") is True

    def test_table_exists_returns_false(self):
        mock_conn = MagicMock()
        with patch.object(pg_mod, "read_rows_pg", return_value=[]):
            assert pg_mod.table_exists_pg(mock_conn, "missing_table") is False

    def test_table_columns_returns_list(self):
        mock_conn = MagicMock()
        rows = [{"column_name": "id"}, {"column_name": "symbol"}, {"column_name": "status"}]
        with patch.object(pg_mod, "read_rows_pg", return_value=rows):
            cols = pg_mod.table_columns_pg(mock_conn, "t")
            assert cols == ["id", "symbol", "status"]


# ---------------------------------------------------------------------------
# pg_readonly_connection context manager
# ---------------------------------------------------------------------------
class TestPgReadonlyConnection:
    def test_context_closes_on_exception(self, monkeypatch):
        """The connection must be closed even if the body raises."""
        _set_all_env(monkeypatch)
        mock_conn = MagicMock()
        with patch.object(pg_mod, "build_readonly_conn", return_value=mock_conn):
            try:
                with pg_mod.pg_readonly_connection() as conn:
                    raise RuntimeError("simulated error")
            except RuntimeError:
                pass
            mock_conn.close.assert_called_once()

    def test_context_closes_on_success(self, monkeypatch):
        """The connection must be closed after normal use."""
        _set_all_env(monkeypatch)
        mock_conn = MagicMock()
        with patch.object(pg_mod, "build_readonly_conn", return_value=mock_conn):
            with pg_mod.pg_readonly_connection() as conn:
                assert conn is mock_conn
            mock_conn.close.assert_called_once()


# ---------------------------------------------------------------------------
# _contains_write_cte edge cases
# ---------------------------------------------------------------------------
class TestContainsWriteCte:
    def test_clean_cte_passes(self):
        assert not pg_mod._contains_write_cte("WITH a AS (SELECT 1) SELECT * FROM a")

    def test_nested_clean_cte_passes(self):
        assert not pg_mod._contains_write_cte(
            "WITH a AS (SELECT 1), b AS (SELECT * FROM a) SELECT * FROM b"
        )

    def test_delete_in_cte_detected(self):
        assert pg_mod._contains_write_cte("WITH a AS (DELETE FROM x RETURNING id) SELECT * FROM a")

    def test_update_in_cte_detected(self):
        assert pg_mod._contains_write_cte("WITH a AS (UPDATE x SET y=1 RETURNING id) SELECT * FROM a")

    def test_insert_in_cte_detected(self):
        assert pg_mod._contains_write_cte("WITH a AS (INSERT INTO x VALUES (1) RETURNING id) SELECT * FROM a")

    def test_case_insensitive(self):
        assert pg_mod._contains_write_cte("with a as (delete from x returning id) select * from a")


# ---------------------------------------------------------------------------
# readonly session / timeouts
# ---------------------------------------------------------------------------
class TestReadOnlySession:
    def test_build_readonly_conn_sets_session_readonly(self, monkeypatch):
        """Verify conn.set_session is called with readonly=True."""
        _set_all_env(monkeypatch)
        monkeypatch.setattr(pg_mod, "psycopg2", MagicMock())
        mock_conn = MagicMock()
        monkeypatch.setattr(pg_mod.psycopg2, "connect", lambda **kw: mock_conn)
        # patch _verify_read_only to pass
        with patch.object(pg_mod, "_verify_read_only", return_value=None):
            pg_mod.build_readonly_conn()
        mock_conn.set_session.assert_called_with(readonly=True, autocommit=False)

    def test_build_readonly_conn_sets_timeouts(self, monkeypatch):
        """Verify statement_timeout and lock_timeout are set."""
        _set_all_env(monkeypatch)
        monkeypatch.setattr(pg_mod, "psycopg2", MagicMock())
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value.__enter__.return_value = mock_cursor
        monkeypatch.setattr(pg_mod.psycopg2, "connect", lambda **kw: mock_conn)
        with patch.object(pg_mod, "_verify_read_only", return_value=None):
            pg_mod.build_readonly_conn()
        calls = [str(c[0][0]) for c in mock_cursor.execute.call_args_list]
        assert any("statement_timeout" in c for c in calls)
        assert any("lock_timeout" in c for c in calls)