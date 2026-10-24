"""HR-only lookups and actions for the Admin page: cycles, audit log, outbox."""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from config.settings import Settings
from core.errors import DomainError, ErrorCode
from core.models import Level, NotificationStatus, ReviewCycle, Status
from core.permissions import Viewer
from core.workflow import derive_status
from db.repositories import Repositories
from services.clock import Clock
from services.notifications.base import NotificationSender, ProcessReport, process_outbox


@dataclass(frozen=True)
class CycleSummary:
    cycle: ReviewCycle
    eligible: int  # active A/B employees (only meaningful for the open cycle)
    started: int
    completed: int


class AdminService:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        clock: Clock,
        settings: Settings,
        sender_factory: Callable[[], NotificationSender],
    ) -> None:
        self._sessions = session_factory
        self.clock = clock
        self.settings = settings
        self._sender_factory = sender_factory

    @staticmethod
    def _require_hr(viewer: Viewer) -> None:
        if not viewer.is_hr:
            raise DomainError(ErrorCode.HR_ONLY)

    # ---- cycles ------------------------------------------------------------------------

    def cycles(self, viewer: Viewer) -> list[CycleSummary]:
        self._require_hr(viewer)
        with self._sessions() as session:
            repos = Repositories.for_session(session)
            eligible = sum(e.level in (Level.A, Level.B) for e in repos.employees.list())
            summaries = []
            for cycle in repos.cycles.list():
                rows = repos.progress.list_for_cycle(cycle.cycle_id).values()
                summaries.append(
                    CycleSummary(
                        cycle=cycle,
                        eligible=eligible,
                        started=len(rows),
                        completed=sum(derive_status(r) is Status.COMPLETED for r in rows),
                    )
                )
        return summaries

    # ---- audit log ---------------------------------------------------------------------

    def audit(
        self,
        viewer: Viewer,
        *,
        actor_id: str | None = None,
        actions: Sequence[str] = (),
        review_id: int | None = None,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        """Newest first, with the actor's name added (``actor_name``)."""
        self._require_hr(viewer)
        with self._sessions() as session:
            repos = Repositories.for_session(session)
            entries = repos.audit.list(
                review_id=review_id, actor_id=actor_id, actions=list(actions), limit=limit
            )
            names = {
                e.employee_id: e.name
                for e in repos.employees.get_many(str(x["actor_id"]) for x in entries).values()
            }
        for entry in entries:
            entry["actor_name"] = names.get(str(entry["actor_id"]), str(entry["actor_id"]))
        return entries

    def audit_actions(self, viewer: Viewer) -> list[str]:
        self._require_hr(viewer)
        with self._sessions() as session:
            return Repositories.for_session(session).audit.actions()

    # ---- notification outbox -----------------------------------------------------------

    @property
    def notify_mode(self) -> str:
        return self.settings.notify_mode.value

    def outbox(
        self, viewer: Viewer, *, status: NotificationStatus | None = None, limit: int = 500
    ) -> list[dict[str, Any]]:
        self._require_hr(viewer)
        with self._sessions() as session:
            return Repositories.for_session(session).outbox.list(status=status, limit=limit)

    def outbox_counts(self, viewer: Viewer) -> dict[str, int]:
        self._require_hr(viewer)
        with self._sessions() as session:
            counts = Repositories.for_session(session).outbox.counts()
        return {s.value: counts.get(s.value, 0) for s in NotificationStatus}

    def outbox_message(self, viewer: Viewer, message_id: int) -> dict[str, Any] | None:
        """One message with its payload decoded (``payload``)."""
        self._require_hr(viewer)
        with self._sessions() as session:
            row = Repositories.for_session(session).outbox.get(message_id)
        if row is None:
            return None
        try:
            row["payload"] = json.loads(row["payload_json"] or "{}")
        except json.JSONDecodeError:
            row["payload"] = {}
        return row

    def send_pending(self, viewer: Viewer, *, retry_failed: bool = False) -> ProcessReport:
        """Process the outbox in the configured NOTIFY_MODE (dry-run by default)."""
        self._require_hr(viewer)
        return process_outbox(
            self._sessions, self._sender_factory(), self.clock, retry_failed=retry_failed
        )
