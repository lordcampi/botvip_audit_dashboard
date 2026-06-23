from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

from .db_readonly import quote_ident, table_columns, table_exists

DEFAULT_SCHEMA_MAP_PATH = Path("config") / "schema_map.json"


@dataclass(frozen=True)
class TableMapping:
    role: str
    table: str
    columns: Dict[str, Any]

    def col(self, key: str, default: Optional[str] = None) -> Optional[str]:
        value = self.columns.get(key, default)
        return str(value) if isinstance(value, str) else default

    def qcol(self, key: str) -> str:
        col = self.col(key)
        if not col:
            raise KeyError(f"Missing column mapping {self.role}.{key}")
        return quote_ident(col)


class SchemaMap:
    def __init__(self, payload: Dict[str, Any]):
        self.payload = payload
        self.tables = payload.get("tables", {})
        self.safety = payload.get("safety", {})
        self.timezone = payload.get("timezone", {})
        self.daily_fact_fields = payload.get("daily_fact_fields", {})

    @classmethod
    def load(cls, path: str | Path = DEFAULT_SCHEMA_MAP_PATH) -> "SchemaMap":
        selected = Path(path)
        if not selected.exists():
            raise FileNotFoundError("schema_map.json not found: " + str(selected))
        payload = json.loads(selected.read_text(encoding="utf-8"))
        obj = cls(payload)
        obj.validate_static()
        return obj

    def validate_static(self) -> None:
        if self.safety.get("db_mode") != "read_only":
            raise ValueError("schema_map safety.db_mode must be read_only")
        required_roles = ["events", "signals", "candidate_snapshots", "shadow_diagnostics", "scan_cycles"]
        missing = [role for role in required_roles if role not in self.tables]
        if missing:
            raise ValueError("schema_map missing table roles: " + ", ".join(missing))

    def mapping(self, role: str) -> TableMapping:
        cfg = self.tables.get(role)
        if not isinstance(cfg, dict):
            raise KeyError("Unknown table role: " + str(role))
        table = cfg.get("table")
        if not table:
            raise KeyError("Missing table name for role: " + str(role))
        return TableMapping(role=role, table=str(table), columns=cfg)

    def ignored_tables(self) -> list[str]:
        return list(self.safety.get("do_not_include_tables_in_ai_pack", []))

    def validate_against_db(self, conn) -> dict[str, Any]:
        report: dict[str, Any] = {"ok": True, "roles": {}, "warnings": []}
        for role, cfg in self.tables.items():
            if not isinstance(cfg, dict):
                continue
            table = str(cfg.get("table", ""))
            role_report = {"table": table, "exists": False, "missing_columns": []}
            if not table:
                role_report["missing_table_name"] = True
                report["ok"] = False
                report["roles"][role] = role_report
                continue
            exists = table_exists(conn, table)
            role_report["exists"] = exists
            if not exists:
                if role not in {"paper_trades", "paper_trade_fills"}:
                    report["ok"] = False
                report["roles"][role] = role_report
                continue
            available = set(table_columns(conn, table))
            required_keys = ["primary_key", "timestamp"]
            if role == "events":
                required_keys += ["signal_id", "event_type", "metadata_json"]
            if role == "signals":
                required_keys += ["symbol", "side", "status", "metrics_json"]
            if role == "candidate_snapshots":
                required_keys += ["symbol", "reason", "metadata_json"]
            for key in required_keys:
                col = cfg.get(key)
                if isinstance(col, str) and col not in available:
                    role_report["missing_columns"].append({key: col})
            if role_report["missing_columns"]:
                report["ok"] = False
            report["roles"][role] = role_report
        return report


def select_clause(mapping: TableMapping, keys: Iterable[str]) -> str:
    cols = []
    for key in keys:
        col = mapping.col(key)
        if col:
            cols.append(quote_ident(col) + " AS " + quote_ident(key))
    return ", ".join(cols)
