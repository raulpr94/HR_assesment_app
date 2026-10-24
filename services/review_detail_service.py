"""Everything the Review detail page shows, with visibility rules already applied.

Answers a viewer may not see never leave this service, so the UI cannot leak them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum

from sqlalchemy.orm import Session, sessionmaker

from config.settings import Settings
from core.errors import DomainError, ErrorCode
from core.models import (
    Employee,
    RespondentRole,
    RevealPolicy,
    ReviewCycle,
    ReviewProgress,
    Status,
)
from core.next_step import NextKind, NextStep, next_step
from core.permissions import Viewer, can_edit_answers, can_see_answers, can_view_review
from core.survey import SurveyDefinition, SurveyRegistry
from core.timeutil import local_date
from core.workflow import derive_status
from db.repositories import Repositories
from services.clock import Clock


class Relation(str, Enum):
    """How the viewer relates to a review."""

    REVIEWEE = "reviewee"
    REVIEWER = "reviewer"
    HR = "hr"
    OBSERVER = "observer"  # a manager higher up the reporting line


class HiddenReason(str, Enum):
    NOT_SUBMITTED = "not_submitted"  # the other side hasn't submitted yet
    REVEAL_LATER = "reveal_later"  # manager answers: visible from the meeting date
    PARTICIPANTS_ONLY = "participants_only"  # skip-level managers see progress only


@dataclass(frozen=True)
class SideView:
    role: RespondentRole
    visible: bool
    editable: bool
    submitted: bool  # locked: submitted and read-only
    answers: dict[str, str | None] = field(default_factory=dict)  # empty when hidden
    has_drafts: bool = False
    hidden_reason: HiddenReason | None = None
    reveal_date: date | None = None


@dataclass(frozen=True)
class Comment:
    author: str
    text: str
    created_at: datetime


@dataclass(frozen=True)
class ReviewDetail:
    progress: ReviewProgress
    status: Status
    cycle: ReviewCycle
    reviewer: Employee
    reviewee: Employee
    survey: SurveyDefinition
    relation: Relation
    self_side: SideView
    manager_side: SideView
    comments: tuple[Comment, ...]
    next: NextStep

    @property
    def review_id(self) -> int:
        assert self.progress.review_id is not None
        return self.progress.review_id

    @property
    def can_acknowledge(self) -> bool:
        return (
            self.next.kind is NextKind.ACT
            and self.relation
            in (
                Relation.REVIEWER,
                Relation.REVIEWEE,
            )
            and self.status in (Status.MEETING_SCHEDULED, Status.ACKNOWLEDGED)
        )

    def side(self, role: RespondentRole) -> SideView:
        return self.self_side if role is RespondentRole.SELF else self.manager_side


@dataclass(frozen=True)
class ReviewListItem:
    review_id: int
    reviewee: str
    reviewer: str
    status: Status
    relation: Relation


class ReviewDetailService:
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

    @property
    def policy(self) -> RevealPolicy:
        return RevealPolicy(self.settings.reveal_manager_answers)

    @staticmethod
    def relation(viewer: Viewer, progress: ReviewProgress) -> Relation:
        if viewer.employee_id == progress.reviewee_id:
            return Relation.REVIEWEE
        if viewer.employee_id == progress.reviewer_id:
            return Relation.REVIEWER
        return Relation.HR if viewer.is_hr else Relation.OBSERVER

    def get(self, review_id: int, viewer: Viewer) -> ReviewDetail:
        """The review as ``viewer`` may see it. Raises REVIEW_HIDDEN for outsiders."""
        now = self.clock.now()
        tz = self.settings.tz
        with self._sessions() as session:
            repos = Repositories.for_session(session)
            progress = repos.progress.get(review_id)
            if progress is None:
                raise DomainError(ErrorCode.REVIEW_NOT_FOUND, review_id=review_id)
            if not can_view_review(viewer, progress.reviewer_id, progress.reviewee_id):
                raise DomainError(ErrorCode.REVIEW_HIDDEN)
            cycle = repos.cycles.get(progress.cycle_id)
            people = repos.employees.get_many([progress.reviewer_id, progress.reviewee_id])
            sides = {}
            for role in RespondentRole:
                stored = repos.answers.get(review_id, role)
                sides[role] = self._side(viewer, progress, role, stored, now)
            comment_rows = repos.comments.list_for_review(review_id)
            authors = repos.employees.get_many(str(c["employee_id"]) for c in comment_rows)
        assert cycle is not None
        comments = tuple(
            Comment(
                author=authors[str(c["employee_id"])].name
                if str(c["employee_id"]) in authors
                else str(c["employee_id"]),
                text=str(c["comment"]),
                created_at=c["created_at"],  # type: ignore[arg-type]
            )
            for c in comment_rows
        )
        return ReviewDetail(
            progress=progress,
            status=derive_status(progress),
            cycle=cycle,
            reviewer=people[progress.reviewer_id],
            reviewee=people[progress.reviewee_id],
            survey=self.surveys.get(progress.survey_version),
            relation=self.relation(viewer, progress),
            self_side=sides[RespondentRole.SELF],
            manager_side=sides[RespondentRole.MANAGER],
            comments=comments,
            next=next_step(
                viewer.employee_id, progress.reviewer_id, progress.reviewee_id, progress, now, tz
            ),
        )

    def _side(
        self,
        viewer: Viewer,
        progress: ReviewProgress,
        role: RespondentRole,
        stored: dict,
        now: datetime,
    ) -> SideView:
        tz = self.settings.tz
        submitted = (
            progress.self_submitted_at
            if role is RespondentRole.SELF
            else progress.manager_submitted_at
        ) is not None
        visible = can_see_answers(viewer, progress, role, now, tz, self.policy)
        if not visible:
            relation = self.relation(viewer, progress)
            if relation is Relation.OBSERVER:
                reason = HiddenReason.PARTICIPANTS_ONLY
            elif not submitted:
                reason = HiddenReason.NOT_SUBMITTED
            else:
                reason = HiddenReason.REVEAL_LATER
            reveal = (
                local_date(progress.meeting_at, tz)
                if reason is HiddenReason.REVEAL_LATER and progress.meeting_at
                else None
            )
            return SideView(role, False, False, submitted, hidden_reason=reason, reveal_date=reveal)
        return SideView(
            role=role,
            visible=True,
            editable=can_edit_answers(viewer, progress, role),
            submitted=submitted,
            answers={qid: a.value for qid, a in stored.items()},
            has_drafts=any(a.is_draft for a in stored.values()),
        )

    def viewable(self, cycle_id: int, viewer: Viewer) -> list[ReviewListItem]:
        """Started reviews the viewer may open: own first, then as reviewer, then others."""
        with self._sessions() as session:
            repos = Repositories.for_session(session)
            rows = repos.progress.list_for_cycle(cycle_id)
            people = repos.employees.get_many(
                {p.reviewer_id for p in rows.values()} | {p.reviewee_id for p in rows.values()}
            )
        items = [
            ReviewListItem(
                review_id=p.review_id or 0,
                reviewee=people[p.reviewee_id].name if p.reviewee_id in people else p.reviewee_id,
                reviewer=people[p.reviewer_id].name if p.reviewer_id in people else p.reviewer_id,
                status=derive_status(p),
                relation=self.relation(viewer, p),
            )
            for p in rows.values()
            if can_view_review(viewer, p.reviewer_id, p.reviewee_id)
        ]
        order = {Relation.REVIEWEE: 0, Relation.REVIEWER: 1, Relation.HR: 2, Relation.OBSERVER: 2}
        return sorted(items, key=lambda i: (order[i.relation], i.reviewee.lower()))

    def history(self, review_id: int, viewer: Viewer) -> list[dict[str, object]]:
        """Audit entries for one review (HR only)."""
        if not viewer.is_hr:
            raise DomainError(ErrorCode.HR_ONLY)
        with self._sessions() as session:
            return Repositories.for_session(session).audit.list(review_id=review_id)
