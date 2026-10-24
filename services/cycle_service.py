"""Review cycles: open, close, look up."""

from __future__ import annotations

import logging
from datetime import date, timedelta

from sqlalchemy.orm import Session, sessionmaker

from config.settings import Settings
from core.errors import DomainError, ErrorCode
from core.models import (
    AuditEntry,
    CycleStatus,
    Level,
    NotificationIntent,
    NotificationType,
    ReviewCycle,
)
from core.timeutil import local_date
from db.repositories import Repositories
from services.clock import Clock
from services.notifications.renderer import NotificationQueue

log = logging.getLogger(__name__)

SYSTEM_ACTOR = "system"
DEFAULT_CYCLE_DAYS = 42


class CycleService:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        queue: NotificationQueue,
        clock: Clock,
        settings: Settings,
    ) -> None:
        self._sessions = session_factory
        self.queue = queue
        self.clock = clock
        self.settings = settings

    def today(self) -> date:
        return local_date(self.clock.now(), self.settings.tz)

    def open_cycle(
        self,
        name: str,
        start_date: date | None = None,
        due_date: date | None = None,
        actor_id: str | None = None,
    ) -> tuple[ReviewCycle, int]:
        """Create an open cycle and notify all C-level managers.

        Returns (cycle, notifications queued). Assumption: only one cycle is open at a
        time, so "the current cycle" is never ambiguous.
        """
        name = (name or "").strip()
        if not name:
            raise DomainError(ErrorCode.CYCLE_NAME_REQUIRED)
        start = start_date or self.today()
        due = due_date or start + timedelta(days=DEFAULT_CYCLE_DAYS)
        if due < start:
            raise DomainError(ErrorCode.CYCLE_INVALID_DATES)
        now = self.clock.now()
        with self._sessions.begin() as session:
            repos = Repositories.for_session(session)
            if actor_id is not None:
                actor = repos.employees.get(actor_id)
                if actor is None:
                    raise DomainError(ErrorCode.UNKNOWN_EMPLOYEE, employee_id=actor_id)
                if not actor.is_hr_admin:
                    raise DomainError(ErrorCode.HR_ONLY)
            current = repos.cycles.get_open()
            if current is not None:
                raise DomainError(ErrorCode.CYCLE_ALREADY_OPEN, cycle=current.name)
            if repos.cycles.get_by_name(name) is not None:
                raise DomainError(ErrorCode.CYCLE_NAME_TAKEN, name=name)
            cycle = repos.cycles.add(name, start, due, actor_id, now)
            repos.audit.add(
                AuditEntry(action="open_cycle", field_changed="cycle", new_value=name),
                actor_id=actor_id or SYSTEM_ACTOR,
                review_id=None,
                now=now,
            )
            # "All C-level managers": active level-C employees who manage someone.
            managers = [
                e
                for e in repos.employees.list_by_level(Level.C)
                if repos.employees.direct_reports(e.employee_id)
            ]
            intent = NotificationIntent(
                NotificationType.CYCLE_STARTED, tuple(m.employee_id for m in managers)
            )
            queued = self.queue.enqueue(
                repos,
                [intent],
                cycle=cycle,
                progress=None,
                people={m.employee_id: m for m in managers},
                now=now,
            )
        log.debug("Opened cycle %r (%s – %s), queued %d notification(s)", name, start, due, queued)
        return cycle, queued

    def close_cycle(self, cycle_id: int, actor_id: str | None = None) -> ReviewCycle:
        now = self.clock.now()
        with self._sessions.begin() as session:
            repos = Repositories.for_session(session)
            if actor_id is not None:
                actor = repos.employees.get(actor_id)
                if actor is None or not actor.is_hr_admin:
                    raise DomainError(ErrorCode.HR_ONLY)
            cycle = repos.cycles.get(cycle_id)
            if cycle is None:
                raise DomainError(ErrorCode.CYCLE_NOT_FOUND, cycle_id=cycle_id)
            repos.cycles.set_status(cycle_id, CycleStatus.CLOSED, now)
            repos.audit.add(
                AuditEntry(
                    action="close_cycle",
                    field_changed="status",
                    old_value=cycle.status.value,
                    new_value=CycleStatus.CLOSED.value,
                ),
                actor_id=actor_id or SYSTEM_ACTOR,
                review_id=None,
                now=now,
            )
            closed = repos.cycles.get(cycle_id)
        assert closed is not None
        return closed

    def get_open_cycle(self) -> ReviewCycle | None:
        with self._sessions() as session:
            return Repositories.for_session(session).cycles.get_open()

    def list_cycles(self) -> list[ReviewCycle]:
        with self._sessions() as session:
            return Repositories.for_session(session).cycles.list()
