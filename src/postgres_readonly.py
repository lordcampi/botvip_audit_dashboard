from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Any, Generator, Optional

import pandas as pd

try:
    import psycopg2
    import psycopg2.extras
    import psycopg2.sql
except ImportError:
    psycopg2 = None  # type: ignore[assignment]

from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Hardcoded guard: this application name must appear in pg_stat_activity
# so DBAs can identify this review centre.
# ---------------------------------------------------------------------------
_APPLICATION_NAME = "botvip_swing_review_center"

# ---------------------------------------------------------------------------
# Environment loading (safe, no side effects when dotenv is absent)
# ---------------------------------------------------------------------------
_ENV_LOADED = False


def _ensure_env() -> None:
    global _ENV_LOADED
    if not _ENV_LOADED:
        load_dotenv()
        _ENV_LOADED = True


# ---------------------------------------------------------------------------
# Connection helpers
# ---------------------------------------------------------------------------
def get_pg_config() -> dict[str, str]:
    """Return PostgreSQL connection parameters from environment.

    Required env vars:
        PG_HOST
        PG_PORT (default 5432)
        PG_DATABASE
        PG_USER (read-only dedicated user)
        PG_PASSWORD

    Raises RuntimeError if any required variable is missing.
    """
    _ensure_env()
    required = ["PG_HOST", "PG_DATABASE", "PG_USER", "PG_PASSWORD"]
    missing = [k for k in required if not os.getenv(k)]
    if missing:
        raise RuntimeError(
            f"Missing PostgreSQL environment variables: {', '.join(missing)}. "
            "Set PG_HOST, PG_DATABASE, PG_USER, PG_PASSWORD in .env or environment."
        )
    return {
        "host": os.getenv("PG_HOST", "").strip(),
        "port": int(os.getenv("PG_PORT", "5432").strip()),
        "dbname": os.getenv("PG_DATABASE", "").strip(),
        "user": os.getenv("PG_USER", "").strip(),
        "password": os.getenv("PG_PASSWORD", "").strip(),
        "application_name": _APPLICATION_NAME,
    }


def build_readonly_conn() -> "psycopg2.extensions.connection":
    """Open a PostgreSQL connection that is EXPLICITLY read-only.

    Enforces:
    - default_transaction_read_only = on   (session-level)
    - statement_timeout                     (prevents runaway queries)
    - lock_timeout                          (prevents blocking DDL/DML)
    - application_name = botvip_swing_review_center

    This function MUST fail if the user has write privileges –
    the connection verifies read-only state before returning.
    """
    if psycopg2 is None:
        raise RuntimeError("psycopg2 is not installed. Run: pip install psycopg2-binary")

    cfg = get_pg_config()
    conn = psycopg2.connect(
        host=cfg["host"],
        port=cfg["port"],
        dbname=cfg["dbname"],
        user=cfg["user"],
        password=cfg["password"],
        application_name=cfg["application_name"],
        connect_timeout=10,
    )
    conn.set_session(readonly=True, autocommit=False)

    # Enforce timeouts at session level (seconds)
    with conn.cursor() as cur:
        cur.execute("SET statement_timeout = '30000'")   # 30 s
        cur.execute("SET lock_timeout = '8000'")          # 8 s

    # Verify: the connection truly cannot write
    _verify_read_only(conn)
    return conn


def _verify_read_only(conn: "psycopg2.extensions.connection") -> None:
    """Execute a harmless write attempt that MUST fail."""
    try:
        with conn.cursor() as cur:
            cur.execute("CREATE TEMP TABLE IF NOT EXISTS _rw_check_readonly (x int)")
        # If we reach here, write was allowed – unacceptable
        conn.rollback()
        raise RuntimeError(
            "PostgreSQL connection allows writes! The dedicated read-only user "
            "must have only SELECT privileges and default_transaction_read_only=on."
        )
    except psycopg2.errors.ReadOnlySqlTransaction:
        conn.rollback()
        # Expected – connection is truly read-only
        return
    except Exception:
        conn.rollback()
        raise


def assert_pg_readonly(conn: "psycopg2.extensions.connection") -> None:
    """Re-verify the connection is still read-only (call after any reconnect)."""
    _verify_read_only(conn)


# ---------------------------------------------------------------------------
# Safe query execution
# ---------------------------------------------------------------------------
def _contains_write_cte(query: str) -> bool:
    """Detect CTEs that contain write operations (INSERT/UPDATE/DELETE/TRUNCATE/DROP/CREATE).

    A query like `WITH x AS (DELETE ...) SELECT * FROM x` would pass the
    simple startswith("with") guard but is still a write operation.
    """
    lowered = query.lower()
    # Look for write keywords inside CTE definitions (between WITH and the main SELECT)
    # Simple heuristic: any write keyword after WITH and before the outermost SELECT
    import re
    # Strip leading/trailing whitespace and semicolons for analysis
    cleaned = query.strip().rstrip(";")
    # Check if any CTE body contains write operations
    write_keywords = r'\b(insert\s+into|update\s+\w+|delete\s+from|truncate\s+|drop\s+table|create\s+table|alter\s+table)\b'
    return bool(re.search(write_keywords, cleaned, re.IGNORECASE))


def _sanitize_error_message(exc: Exception) -> str:
    """Return a sanitized error message without connection details."""
    msg = str(exc)
    # If the message is already generic, return as-is
    if len(msg) < 100 and "password" not in msg.lower() and "host=" not in msg.lower():
        return msg
    return "PostgreSQL query error (details sanitized for security)"


def read_sql_df_pg(
    conn: "psycopg2.extensions.connection",
    query: str,
    params: Optional[tuple] = None,
) -> pd.DataFrame:
    """Execute a SELECT query and return results as a DataFrame.

    Refuses any non-SELECT statement.
    Refuses WITH queries that contain write CTEs.
    All queries are parameterised; no string interpolation.
    """
    lowered = query.strip().lower()
    if not lowered.startswith("select") and not lowered.startswith("with"):
        raise ValueError("Only SELECT / WITH queries are permitted on read-only connection")
    # Block multi-statement injections
    if ";" in query.rstrip(";"):
        raise ValueError("Multiple statements not allowed – use a single SELECT")
    # Block WITH queries containing write CTEs
    if lowered.startswith("with") and _contains_write_cte(query):
        raise ValueError("WITH query contains write operations (INSERT/UPDATE/DELETE/TRUNCATE/CREATE/DROP/ALTER) – rejected")

    try:
        return pd.read_sql_query(query, conn, params=params or ())
    except Exception as e:
        raise RuntimeError(_sanitize_error_message(e)) from e


def read_rows_pg(
    conn: "psycopg2.extensions.connection",
    query: str,
    params: Optional[tuple] = None,
) -> list[dict]:
    """Execute SELECT and return list of dicts."""
    lowered = query.strip().lower()
    if not lowered.startswith("select") and not lowered.startswith("with"):
        raise ValueError("Only SELECT / WITH queries are permitted")
    if ";" in query.rstrip(";"):
        raise ValueError("Multiple statements not allowed")

    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(query, params or ())
        rows = cur.fetchall()
        return [dict(r) for r in rows]


def table_exists_pg(conn: "psycopg2.extensions.connection", table: str) -> bool:
    """Check if a table exists in the public schema."""
    row = read_rows_pg(
        conn,
        "SELECT 1 FROM information_schema.tables WHERE table_schema = 'public' AND table_name = %s",
        (table,),
    )
    return len(row) > 0


def table_columns_pg(conn: "psycopg2.extensions.connection", table: str) -> list[str]:
    """Return column names for a table."""
    rows = read_rows_pg(
        conn,
        "SELECT column_name FROM information_schema.columns WHERE table_schema = 'public' AND table_name = %s ORDER BY ordinal_position",
        (table,),
    )
    return [r["column_name"] for r in rows]


# ---------------------------------------------------------------------------
# Context manager for safe connection lifecycle
# ---------------------------------------------------------------------------
@contextmanager
def pg_readonly_connection() -> Generator["psycopg2.extensions.connection", None, None]:
    """Context manager that opens a read-only connection and closes it."""
    conn = build_readonly_conn()
    try:
        yield conn
    finally:
        try:
            conn.close()
        except Exception:
            pass