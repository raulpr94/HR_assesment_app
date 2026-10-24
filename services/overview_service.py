"""Rows for the Overview page: my review, my team and the HR company view.

Rows are built by joining active employees with ``review_progress`` in memory.
People without a progress row are "Not started"; no placeholder rows are inserted.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from dataclasses import dataclass

from sqlalchemy.orm import Session, sessionmaker

from config.settings import Settings
from core.errors import DomainError, ErrorCode
from core.models import Action, Employee, Level, ReviewProgress, Status
from core.next_step import NextStep, next_step
from core.permissions import Viewer, writeback_actions
from core.workflow import derive_status
from db.repositories import Repositories
from services.clock import Clock

REVIEWABLE_LEVELS = (Level.A, Level.B)  # C-level and HR are not reviewed (spec §2)


@dataclass(frozen=True)
class OverviewRow:
    employee: Employee  # the reviewee
    reviewer: Employee | None  # their manager
    progress: ReviewProgress | None
    status: Status
    next: NextStep  # from the viewer's point of view
    actions: frozenset[Action]  # writeback cells the viewer may edit in this row
    reviewer_own_status: Status  # the manager's own review (B→A unlock rule)

    @property
    def review_id(self) -> int | None:
        return self.progress.review_id if self.progress else None


class OverviewService:
    def __init__(
        self, session_factory: sessionmaker[Session], clock: Clock, settings: Settings
    ) -> None:
        self._sessions = session_factory
        self.clock = clock
        self.settings = settings

    def _load(self, cycle_id: int) -> tuple[dict[str, Employee], dict[str, ReviewProgress]]:
        with self._sessions() as session:
            repos = Repositories.for_session(session)
            people = {e.employee_id: e for e in repos.employees.list()}
            progress = repos.progress.list_for_cycle(cycle_id)
        return people, progress

    def _row(
        self,
        viewer: Viewer,
        emp: Employee,
        people: Mapping[str, Employee],
        progress: Mapping[str, ReviewProgress],
    ) -> OverviewRow:
        reviewer = people.get(emp.manager_id) if emp.manager_id else None
        row = progress.get(emp.employee_id)
        reviewer_id = reviewer.employee_id if reviewer else ""
        own = derive_status(progress.get(reviewer_id)) if reviewer else Status.NOT_STARTED
        now = self.clock.now()
        return OverviewRow(
            employee=emp,
            reviewer=reviewer,
            progress=row,
            status=derive_status(row),
            next=next_step(
                viewer.employee_id, reviewer_id, emp.employee_id, row, now, self.settings.tz
            ),
            actions=(
                writeback_actions(viewer, reviewer_id, reviewer.level, own, row, now)
                if reviewer
                else frozenset()
            ),
            reviewer_own_status=own,
        )

    def my_review(self, cycle_id: int, viewer: Viewer) -> OverviewRow | None:
        """The viewer's own review, or None if they aren't reviewed (C level)."""
        if viewer.level not in REVIEWABLE_LEVELS:
            return None
        people, progress = self._load(cycle_id)
        return self._row(viewer, viewer.employee, people, progress)

    def team_rows(self, cycle_id: int, viewer: Viewer) -> list[OverviewRow]:
        """All the viewer's reviewable direct reports, started or not."""
        people, progress = self._load(cycle_id)
        reports = [
            e
            for e in people.values()
            if e.manager_id == viewer.employee_id and e.level in REVIEWABLE_LEVELS
        ]
        return [self._row(viewer, e, people, progress) for e in _sorted(reports)]

    def company_rows(self, cycle_id: int, viewer: Viewer) -> list[OverviewRow]:
        """Every reviewable employee in the company (HR only)."""
        if not viewer.is_hr:
            raise DomainError(ErrorCode.HR_ONLY)
        people, progress = self._load(cycle_id)
        reviewable = [e for e in people.values() if e.level in REVIEWABLE_LEVELS]
        return [self._row(viewer, e, people, progress) for e in _sorted(reviewable)]

    def team_locked(self, cycle_id: int, viewer: Viewer) -> bool:
        """True for a B manager whose own review isn't completed yet."""
        if viewer.level is not Level.B:
            return False
        _, progress = self._load(cycle_id)
        return derive_status(progress.get(viewer.employee_id)) is not Status.COMPLETED

    @staticmethod
    def filter_rows(
        rows: list[OverviewRow],
        *,
        departments: Collection[str] = (),
        levels: Collection[Level] = (),
        statuses: Collection[Status] = (),
        manager_ids: Collection[str] = (),
    ) -> list[OverviewRow]:
        """Empty filters mean "all"."""
        return [
            r
            for r in rows
            if (not departments or r.employee.department in departments)
            and (not levels or r.employee.level in levels)
            and (not statuses or r.status in statuses)
            and (not manager_ids or (r.reviewer and r.reviewer.employee_id in manager_ids))
        ]


def _sorted(people: list[Employee]) -> list[Employee]:
    return sorted(people, key=lambda e: (e.department.lower(), e.level.value, e.name.lower()))
