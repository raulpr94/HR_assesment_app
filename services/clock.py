"""Injectable clock, so tests and demos can control "now"."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Protocol

from core.timeutil import utcnow


class Clock(Protocol):
    def now(self) -> datetime:
        """Current time as naive UTC."""
        ...


class SystemClock:
    def now(self) -> datetime:
        return utcnow()


class FixedClock:
    """A clock that only moves when told to."""

    def __init__(self, start: datetime) -> None:
        self._now = start

    def now(self) -> datetime:
        return self._now

    def set(self, value: datetime) -> None:
        self._now = value

    def advance(self, **delta: float) -> None:
        self._now += timedelta(**delta)
