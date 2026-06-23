from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Dict, List

import pandas as pd

from .db import quote_ident, read_sql_df


@dataclass(frozen=True)
class TableInfo:
    name: str
    columns: List[str]
    row_count: int | None = None


def discover_tables(conn: sqlite3.Connection) -> List[str]:
    query = """
    SELECT name
    FROM sqlite_master
    WHERE type='table'
      AND name NOT LIKE 'sqlite_%'
    ORDER BY name
    """
    df = read_sql_df(conn, query)
    if df.empty:
        return []
    return df["name"].astype(str).tolist()


def discover_columns(conn: sqlite3.Connection, table: str) -> List[str]:
    rows = conn.execute(f"PRAGMA table_info({quote_ident(table)})").fetchall()
    return [str(row[1]) for row in rows]


def safe_count(conn: sqlite3.Connection, table: str) -> int | None:
    try:
        row = conn.execute(f"SELECT COUNT(*) AS n FROM {quote_ident(table)}").fetchone()
        return int(row[0]) if row is not None else None
    except Exception:
        return None


def discover_schema(conn: sqlite3.Connection) -> Dict[str, TableInfo]:
    schema: Dict[str, TableInfo] = {}
    for table in discover_tables(conn):
        schema[table] = TableInfo(
            name=table,
            columns=discover_columns(conn, table),
            row_count=safe_count(conn, table),
        )
    return schema


def columns_present(schema: Dict[str, TableInfo], table: str, expected: List[str]) -> List[str]:
    if table not in schema:
        return []
    available = set(schema[table].columns)
    return [col for col in expected if col in available]


def missing_columns(schema: Dict[str, TableInfo], table: str, expected: List[str]) -> List[str]:
    if table not in schema:
        return expected
    available = set(schema[table].columns)
    return [col for col in expected if col not in available]


def schema_dataframe(schema: Dict[str, TableInfo]) -> pd.DataFrame:
    rows = []
    for name, info in schema.items():
        rows.append({"table": name, "row_count": info.row_count, "columns": ", ".join(info.columns)})
    return pd.DataFrame(rows)
