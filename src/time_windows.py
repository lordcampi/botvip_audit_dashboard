from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

BOGOTA_TZ = ZoneInfo("America/Bogota")
UTC_TZ = ZoneInfo("UTC")


@dataclass(frozen=True)
class TimeWindow:
    label: str
    start_local: datetime
    end_local: datetime
    start_text: str
    end_text: str


def _fmt(dt: datetime) -> str:
    # DB timestamps observed are naive text in local/server time style.
    # We keep text without timezone offset for SQLite comparisons.
    return dt.replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S")


def now_bogota() -> datetime:
    return datetime.now(BOGOTA_TZ)


def previous_daily_report_window(reference: datetime | None = None) -> TimeWindow:
    ref = reference.astimezone(BOGOTA_TZ) if reference else now_bogota()
    today_5 = datetime.combine(ref.date(), time(hour=5, minute=0, second=0), tzinfo=BOGOTA_TZ)
    if ref >= today_5:
        end_local = today_5
    else:
        end_local = today_5 - timedelta(days=1)
    start_local = end_local - timedelta(days=1)
    return TimeWindow(
        label="previous_24h_colombia_5am",
        start_local=start_local,
        end_local=end_local,
        start_text=_fmt(start_local),
        end_text=_fmt(end_local),
    )


def rolling_window(hours: int, reference: datetime | None = None) -> TimeWindow:
    ref = reference.astimezone(BOGOTA_TZ) if reference else now_bogota()
    start = ref - timedelta(hours=hours)
    return TimeWindow(
        label=f"rolling_{hours}h",
        start_local=start,
        end_local=ref,
        start_text=_fmt(start),
        end_text=_fmt(ref),
    )


def parse_window(window: str, reference: datetime | None = None) -> TimeWindow:
    value = str(window or "24h").strip().lower()
    if value in {"daily", "previous_24h_colombia", "previous_24h_colombia_5am"}:
        return previous_daily_report_window(reference)
    if value.endswith("h"):
        return rolling_window(int(value[:-1]), reference)
    if value.endswith("d"):
        return rolling_window(int(value[:-1]) * 24, reference)
    raise ValueError("Unsupported window: " + str(window))
