"""Time helpers. The database stores naive UTC; users see local time as DD.MM.YYYY."""

from __future__ import annotations

from datetime import date, datetime, timezone, tzinfo

DATE_FORMAT = "%d.%m.%Y"
DATETIME_FORMAT = "%d.%m.%Y %H:%M"


def utcnow() -> datetime:
    """Current time as naive UTC (the storage convention)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def to_utc_naive(value: datetime, tz: tzinfo) -> datetime:
    """Convert a datetime to naive UTC. Naive input is read as local time in ``tz``."""
    aware = value.replace(tzinfo=tz) if value.tzinfo is None else value
    return aware.astimezone(timezone.utc).replace(tzinfo=None)


def to_local(value_utc: datetime, tz: tzinfo) -> datetime:
    """Convert stored naive UTC to an aware local datetime."""
    return value_utc.replace(tzinfo=timezone.utc).astimezone(tz)


def local_date(value_utc: datetime, tz: tzinfo) -> date:
    return to_local(value_utc, tz).date()


def format_date(value: date | None) -> str:
    return value.strftime(DATE_FORMAT) if value else ""


def format_local_datetime(value_utc: datetime | None, tz: tzinfo) -> str:
    return to_local(value_utc, tz).strftime(DATETIME_FORMAT) if value_utc else ""


def format_local_date(value_utc: datetime | None, tz: tzinfo) -> str:
    return to_local(value_utc, tz).strftime(DATE_FORMAT) if value_utc else ""
