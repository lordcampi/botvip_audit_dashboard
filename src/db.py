from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Optional, Sequence
from urllib.parse import quote

import pandas as pd

try:
    from dotenv import load_dotenv
except Exception:  # pragma: no cover
    load_dotenv = None


def load_environment() -> None:
    if load_dotenv is not None:
        load_dotenv()


def get_db_path() -> str:
    load_environment()
    return os.getenv("DB_PATH", "./data/trading_bot.db").strip()


def get_max_rows(default: int = 20000) -> int:
    load_environment()
    raw = os.getenv("MAX_ROWS", str(default)).strip()
    try:
        value = int(raw)
        return max(100, min(value, 500000))
    except ValueError:
        return default


def normalize_db_path(db_path: str) -> Path:
    path = Path(db_path).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    return path.resolve()


def sqlite_readonly_uri(db_path: str) -> str:
    path = normalize_db_path(db_path)
    # SQLite URI requires URL escaping. Use POSIX representation for cross-platform readability.
    return "file:" + quote(path.as_posix(), safe="/:._-~") + "?mode=ro"


def db_exists(db_path: str) -> bool:
    return normalize_db_path(db_path).is_file()


def db_size_mb(db_path: str) -> float:
    path = normalize_db_path(db_path)
    if not path.exists():
        return 0.0
    return round(path.stat().st_size / (1024 * 1024), 2)


def connect_readonly(db_path: Optional[str] = None) -> sqlite3.Connection:
    """Open SQLite in read-only mode and additionally enforce query_only.

    This function intentionally does not create the DB if it does not exist.
    """
    selected = db_path or get_db_path()
    if not db_exists(selected):
        raise FileNotFoundError(f"DB_PATH does not exist: {normalize_db_path(selected)}")
    uri = sqlite_readonly_uri(selected)
    conn = sqlite3.connect(uri, uri=True, timeout=5, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    conn.execute("PRAGMA busy_timeout=3000")
    return conn


def read_sql_df(conn: sqlite3.Connection, query: str, params: Optional[Sequence] = None) -> pd.DataFrame:
    return pd.read_sql_query(query, conn, params=params or [])


def quote_ident(identifier: str) -> str:
    safe = str(identifier).replace('"', '""')
    return f'"{safe}"'
