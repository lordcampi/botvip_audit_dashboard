from __future__ import annotations

from typing import Any, Optional

from .db_readonly import quote_ident, read_rows
from .schema_mapper import SchemaMap, TableMapping
from .time_windows import TimeWindow


def _order_limit_clause(mapping: TableMapping, limit: Optional[int]) -> str:
    ts = mapping.qcol("timestamp")
    clause = " ORDER BY " + ts + " ASC"
    if limit is not None and int(limit) > 0:
        clause += " LIMIT " + str(int(limit))
    return clause


def load_role_window(conn, schema: SchemaMap, role: str, window: TimeWindow, limit: Optional[int] = None) -> list[dict[str, Any]]:
    mapping = schema.mapping(role)
    query = (
        "SELECT * FROM " + quote_ident(mapping.table) + " "
        "WHERE " + mapping.qcol("timestamp") + " >= ? "
        "AND " + mapping.qcol("timestamp") + " < ?"
        + _order_limit_clause(mapping, limit)
    )
    return read_rows(conn, query, [window.start_text, window.end_text])


def load_events(conn, schema: SchemaMap, window: TimeWindow, limit: Optional[int] = None) -> list[dict[str, Any]]:
    return load_role_window(conn, schema, "events", window, limit)


def load_signals(conn, schema: SchemaMap, window: TimeWindow, limit: Optional[int] = None) -> list[dict[str, Any]]:
    return load_role_window(conn, schema, "signals", window, limit)


def load_candidate_snapshots(conn, schema: SchemaMap, window: TimeWindow, limit: Optional[int] = None) -> list[dict[str, Any]]:
    return load_role_window(conn, schema, "candidate_snapshots", window, limit)


def load_shadow_diagnostics(conn, schema: SchemaMap, window: TimeWindow, limit: Optional[int] = None) -> list[dict[str, Any]]:
    return load_role_window(conn, schema, "shadow_diagnostics", window, limit)


def load_scan_cycles(conn, schema: SchemaMap, window: TimeWindow, limit: Optional[int] = None) -> list[dict[str, Any]]:
    return load_role_window(conn, schema, "scan_cycles", window, limit)


def count_by(conn, schema: SchemaMap, role: str, column_key: str, window: TimeWindow, limit: int = 50) -> list[dict[str, Any]]:
    mapping = schema.mapping(role)
    query = (
        "SELECT " + mapping.qcol(column_key) + " AS value, COUNT(*) AS count "
        "FROM " + quote_ident(mapping.table) + " "
        "WHERE " + mapping.qcol("timestamp") + " >= ? "
        "AND " + mapping.qcol("timestamp") + " < ? "
        "GROUP BY " + mapping.qcol(column_key) + " "
        "ORDER BY count DESC LIMIT ?"
    )
    return read_rows(conn, query, [window.start_text, window.end_text, int(limit)])
