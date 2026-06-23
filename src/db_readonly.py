from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Optional, Sequence
from urllib.parse import quote

try:
    from dotenv import load_dotenv
except Exception:  # pragma: no cover
    load_dotenv = None


def load_environment() -> None:
    if load_dotenv is not None:
        load_dotenv()


def get_db_path(cli_db_path: Optional[str] = None) -> str:
    load_environment()
    value = cli_db_path or os.getenv("DB_PATH", "./data/trading_bot.db")
    return str(value).strip()


def normalize_path(path_text: str) -> Path:
    path = Path(path_text).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    return path.resolve()


def sqlite_readonly_uri(db_path: str) -> str:
    path = normalize_path(db_path)
    return "file:" + quote(path.as_posix(), safe="/:._-~") + "?mode=ro"


def quote_ident(identifier: str) -> str:
    return '"' + str(identifier).replace('"', '""') + '"'


def db_exists(db_path: str) -> bool:
    return normalize_path(db_path).is_file()


def db_size_mb(db_path: str) -> float:
    path = normalize_path(db_path)
    if not path.exists():
        return 0.0
    return round(path.stat().st_size / (1024 * 1024), 2)


def connect_readonly(db_path: Optional[str] = None) -> sqlite3.Connection:
    selected = get_db_path(db_path)
    normalized = normalize_path(selected)
    if not normalized.is_file():
        raise FileNotFoundError("DB_PATH does not exist: " + str(normalized))

    conn = sqlite3.connect(sqlite_readonly_uri(selected), uri=True, timeout=5, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    conn.execute("PRAGMA busy_timeout=3000")
    return conn


def assert_readonly(conn: sqlite3.Connection) -> None:
    row = conn.execute("PRAGMA query_only").fetchone()
    value = int(row[0]) if row is not None else 0
    if value != 1:
        raise RuntimeError("SQLite connection is not query_only=ON")


def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
    return row is not None


def table_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    rows = conn.execute("PRAGMA table_info(" + quote_ident(table) + ")").fetchall()
    return [str(row[1]) for row in rows]


def read_rows(conn: sqlite3.Connection, query: str, params: Optional[Sequence] = None) -> list[dict]:
    forbidden = ["insert ", "update ", "delete ", "alter ", "drop ", "vacuum", "create index", "create table"]
    lowered = " " + " ".join(str(query).lower().split()) + " "
    for token in forbidden:
        if " " + token.strip() + " " in lowered:
            raise ValueError("Refusing potentially mutating SQL: " + token.strip())
    rows = conn.execute(query, tuple(params or [])).fetchall()
    return [dict(row) for row in rows]
