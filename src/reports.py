from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Dict

import pandas as pd


def dataframe_to_csv_bytes(df: pd.DataFrame) -> bytes:
    if df is None:
        df = pd.DataFrame()
    return df.to_csv(index=False).encode("utf-8")


def summary_to_json_bytes(summary: Dict) -> bytes:
    payload = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "summary": summary,
    }
    return json.dumps(payload, indent=2, ensure_ascii=False, default=str).encode("utf-8")


def audit_markdown(summary: Dict, event_counts_df: pd.DataFrame) -> str:
    lines = [
        "# BotVIP / AlphaScalp Audit Summary",
        "",
        f"Generated at UTC: {datetime.now(timezone.utc).isoformat()}",
        "",
        "## Metrics",
        "",
    ]
    for key, value in summary.items():
        lines.append(f"- **{key}**: {value}")
    lines.extend(["", "## F4_T11a Event Counts", ""])
    if event_counts_df is not None and not event_counts_df.empty:
        for _, row in event_counts_df.iterrows():
            lines.append(f"- {row.get('event_type')}: {row.get('count')}")
    else:
        lines.append("No F4_T11a events found in selected window.")
    return "\n".join(lines)
