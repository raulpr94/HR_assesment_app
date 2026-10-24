"""Dashboard figures for a C-level manager's reporting tree, or the whole company for HR."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date

from sqlalchemy.orm import Session, sessionmaker

from config.settings import Settings
from core.errors import DomainError, ErrorCode
from core.hierarchy import children_map, descendants
from core.models import Employee, Level, Page, RespondentRole, ReviewCycle, ReviewProgress, Status
from core.permissions import Viewer, can_access_page
from core.survey import QuestionType, SurveyRegistry
from core.timeutil import local_date
from core.workflow import derive_status
from db.repositories import Repositories
from services.clock import Clock

# Averages over fewer submitted answers than this are not shown, so a small team's
# figures can't be traced back to one person (assumption; see README).
MIN_GROUP_SIZE = 3
REVIEWABLE = (Level.A, Level.B)


@dataclass(frozen=True)
class OverdueItem:
    employee: Employee
    manager: Employee | None
    status: Status
    days_overdue: int


@dataclass(frozen=True)
class RatingAverage:
    question_id: str
    question: str
    self_avg: float | None  # None when fewer than MIN_GROUP_SIZE answers
    manager_avg: float | None
    self_n: int
    manager_n: int


@dataclass(frozen=True)
class TeamSummary:
    manager: Employee
    size: int
    started: int
    completed: int
    overdue: int

    @property
    def completion_rate(self) -> float:
        return self.completed / self.size if self.size else 0.0


@dataclass(frozen=True)
class Dashboard:
    cycle: ReviewCycle
    company_wide: bool
    team: Employee | None  # the drilled-down team's manager, if any
    eligible: int
    status_counts: dict[Status, int]
    days_to_due: int  # negative once the due date has passed
    overdue: list[OverdueItem]
    ratings: list[RatingAverage]
    teams: list[TeamSummary]
    min_group_size: int = MIN_GROUP_SIZE

    @property
    def completed(self) -> int:
        return self.status_counts.get(Status.COMPLETED, 0)

    @property
    def completion_rate(self) -> float:
        return self.completed / self.eligible if self.eligible else 0.0


class DashboardService:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        surveys: SurveyRegistry,
        clock: Clock,
        settings: Settings,
    ) -> None:
        self._sessions = session_factory
        self.surveys = surveys
        self.clock = clock
        self.settings = settings
        self.min_group_size = MIN_GROUP_SIZE

    def build(
        self, cycle_id: int, viewer: Viewer, *, team_manager_id: str | None = None
    ) -> Dashboard:
        """Figures for the viewer's scope; ``team_manager_id`` narrows it to one team."""
        if not can_access_page(viewer, Page.DASHBOARD):
            raise DomainError(ErrorCode.PAGE_FORBIDDEN)
        with self._sessions() as session:
            repos = Repositories.for_session(session)
            cycle = repos.cycles.get(cycle_id)
            if cycle is None:
                raise DomainError(ErrorCode.CYCLE_NOT_FOUND, cycle_id=cycle_id)
            people = {e.employee_id: e for e in repos.employees.list()}
            progress = repos.progress.list_for_cycle(cycle_id)

            if viewer.is_hr:
                scope_ids = set(people)
            else:
                scope_ids = descendants(children_map(people.values()), viewer.employee_id)
            scope = [people[i] for i in scope_ids if i in people and people[i].level in REVIEWABLE]

            team_manager = None
            if team_manager_id:
                team_manager = people.get(team_manager_id)
                scope = [e for e in scope if e.manager_id == team_manager_id]

            in_scope = {e.employee_id for e in scope}
            review_ids = [
                p.review_id for rid, p in progress.items() if rid in in_scope and p.review_id
            ]
            answers = repos.answers.submitted_for(review_ids)

        today = local_date(self.clock.now(), self.settings.tz)
        statuses = {e.employee_id: derive_status(progress.get(e.employee_id)) for e in scope}
        counts = {status: 0 for status in Status}
        for status in statuses.values():
            counts[status] += 1

        overdue = self._overdue(scope, statuses, people, cycle.due_date, today)
        return Dashboard(
            cycle=cycle,
            company_wide=viewer.is_hr,
            team=team_manager,
            eligible=len(scope),
            status_counts=counts,
            days_to_due=(cycle.due_date - today).days,
            overdue=overdue,
            ratings=self._ratings(answers),
            teams=self._teams(
                scope, statuses, people, progress, {o.employee.employee_id for o in overdue}
            ),
            min_group_size=self.min_group_size,
        )

    @staticmethod
    def _overdue(
        scope: list[Employee],
        statuses: dict[str, Status],
        people: dict[str, Employee],
        due: date,
        today: date,
    ) -> list[OverdueItem]:
        if today <= due:
            return []
        items = [
            OverdueItem(
                employee=e,
                manager=people.get(e.manager_id) if e.manager_id else None,
                status=statuses[e.employee_id],
                days_overdue=(today - due).days,
            )
            for e in scope
            if statuses[e.employee_id] is not Status.COMPLETED
        ]
        return sorted(items, key=lambda i: (i.status.value, i.employee.name))

    def _ratings(
        self, answers: list[tuple[int, RespondentRole, str, str | None]]
    ) -> list[RatingAverage]:
        values: dict[tuple[str, RespondentRole], list[int]] = defaultdict(list)
        for _review_id, role, question_id, value in answers:
            if value is not None and value.isdigit():
                values[(question_id, role)].append(int(value))

        def avg(numbers: list[int]) -> float | None:
            if len(numbers) < self.min_group_size:
                return None
            return round(sum(numbers) / len(numbers), 2)

        return [
            RatingAverage(
                question_id=q.id,
                question=q.text,
                self_avg=avg(values[(q.id, RespondentRole.SELF)]),
                manager_avg=avg(values[(q.id, RespondentRole.MANAGER)]),
                self_n=len(values[(q.id, RespondentRole.SELF)]),
                manager_n=len(values[(q.id, RespondentRole.MANAGER)]),
            )
            for q in self.surveys.current.questions
            if q.type is QuestionType.RATING
        ]

    @staticmethod
    def _teams(
        scope: list[Employee],
        statuses: dict[str, Status],
        people: dict[str, Employee],
        progress: dict[str, ReviewProgress],
        overdue_ids: set[str],
    ) -> list[TeamSummary]:
        by_manager: dict[str, list[Employee]] = defaultdict(list)
        for e in scope:
            if e.manager_id:
                by_manager[e.manager_id].append(e)
        teams = []
        for manager_id, members in by_manager.items():
            manager = people.get(manager_id)
            if manager is None:
                continue
            teams.append(
                TeamSummary(
                    manager=manager,
                    size=len(members),
                    started=sum(m.employee_id in progress for m in members),
                    completed=sum(statuses[m.employee_id] is Status.COMPLETED for m in members),
                    overdue=sum(m.employee_id in overdue_ids for m in members),
                )
            )
        return sorted(teams, key=lambda t: (t.manager.department, t.manager.name))
