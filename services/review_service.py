"""Review orchestration: runs workflow transitions and answer saves in one transaction."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from config.settings import Settings
from core.errors import DomainError, ErrorCode
from core.models import (
    Action,
    Answer,
    Employee,
    Level,
    RespondentRole,
    ReviewCycle,
    ReviewProgress,
    Status,
)
from core.permissions import Viewer, can_edit_answers
from core.survey import SurveyRegistry, check_answers, normalise_answers
from core.timeutil import to_utc_naive
from core.workflow import (
    TRANSITIONS,
    TransitionContext,
    TransitionOutcome,
    TransitionPayload,
    apply_changes,
    derive_status,
    plan_transition,
)
from db.repositories import Repositories
from services.clock import Clock
from services.notifications.renderer import NotificationQueue

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class TransitionResult:
    review_id: int | None  # None after a revert that removed the row
    action: Action
    old_status: Status
    new_status: Status
    notifications_queued: int


@dataclass(frozen=True)
class TeamRow:
    employee: Employee
    progress: ReviewProgress | None
    status: Status


class ReviewService:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        surveys: SurveyRegistry,
        queue: NotificationQueue,
        clock: Clock,
        settings: Settings,
    ) -> None:
        self._sessions = session_factory
        self.surveys = surveys
        self.queue = queue
        self.clock = clock
        self.settings = settings

    # ---- the single workflow entry point ---------------------------------------------

    def transition(
        self,
        review_id: int | None,
        action: Action | str,
        actor_id: str,
        payload: Mapping[str, Any] | None = None,
    ) -> TransitionResult:
        """Run one workflow step in ONE transaction: guards → row → audit → notifications.

        ``review_id`` is None for ``initiate`` (no row exists yet); then the payload
        must contain ``reviewee_id`` and may contain ``cycle_id`` (default: open cycle).
        Other payload keys: ``meeting_at`` (datetime; naive = local time), ``reason``
        (HR overrides), ``comment`` (reviewee acknowledgement) and ``answers``
        (submit actions: saved as drafts first, then locked, same transaction).
        Raises ``DomainError`` on any rule violation; nothing is written then.
        """
        action = Action(action)
        payload = dict(payload or {})
        now = self.clock.now()
        try:
            with self._sessions.begin() as session:
                repos = Repositories.for_session(session)
                result = self._transition(repos, review_id, action, actor_id, payload, now)
        except IntegrityError as exc:
            # Two managers clicking "Initiate" at once: the unique constraint wins.
            if action is Action.INITIATE:
                raise DomainError(ErrorCode.ALREADY_INITIATED) from exc
            raise
        log.info(
            "Review %s: %s by %s (%s → %s)",
            result.review_id,
            action.value,
            actor_id,
            result.old_status.value,
            result.new_status.value,
        )
        return result

    def preview(
        self,
        review_id: int | None,
        action: Action | str,
        actor_id: str,
        payload: Mapping[str, Any] | None = None,
    ) -> TransitionOutcome:
        """What ``transition`` would do, without writing anything (e.g. to explain an
        HR override before confirming). Raises the same ``DomainError``s."""
        payload = dict(payload or {})
        payload.pop("answers", None)
        with self._sessions() as session:
            repos = Repositories.for_session(session)
            outcome, _ = self._plan(
                repos,
                review_id,
                Action(action),
                actor_id,
                payload,
                self.clock.now(),
                save_answers=False,
            )
            session.rollback()
        return outcome

    def _transition(
        self,
        repos: Repositories,
        review_id: int | None,
        action: Action,
        actor_id: str,
        payload: dict[str, Any],
        now: datetime,
    ) -> TransitionResult:
        outcome, ctx = self._plan(repos, review_id, action, actor_id, payload, now)
        new_id, final = self._persist(repos, outcome, ctx)
        queued = self.queue.enqueue(
            repos,
            outcome.notifications,
            cycle=ctx.cycle,
            progress=final,
            people={e.employee_id: e for e in (ctx.reviewer, ctx.reviewee)},
            now=now,
        )
        return TransitionResult(new_id, action, outcome.old_status, outcome.new_status, queued)

    def _plan(
        self,
        repos: Repositories,
        review_id: int | None,
        action: Action,
        actor_id: str,
        payload: dict[str, Any],
        now: datetime,
        *,
        save_answers: bool = True,
    ) -> tuple[TransitionOutcome, TransitionContext]:
        actor = self._employee(repos, actor_id)
        progress, cycle, reviewer, reviewee = self._load_pair(repos, review_id, action, payload)

        reviewer_own_status = Status.NOT_STARTED
        if action is Action.INITIATE and reviewer.level is Level.B:
            reviewer_own_status = derive_status(
                repos.progress.get_for(cycle.cycle_id, reviewer.employee_id)
            )

        complete, missing = False, ()
        if action in (Action.SUBMIT_SELF, Action.SUBMIT_MANAGER) and progress is not None:
            role = RespondentRole.SELF if action is Action.SUBMIT_SELF else RespondentRole.MANAGER
            owner_id = progress.reviewee_id if role is RespondentRole.SELF else progress.reviewer_id
            # Save only when this submit is possible at all; otherwise plan_transition
            # below raises the proper permission or status error.
            allowed = derive_status(progress) in TRANSITIONS[action].allowed_from
            if save_answers and "answers" in payload and actor_id == owner_id and allowed:
                viewer = Viewer(employee=actor)
                self._save_answers(repos, viewer, progress, role, payload["answers"], now)
            survey = self.surveys.get(progress.survey_version)
            check = check_answers(
                survey, repos.answers.values(progress.review_id or 0, role), require_complete=True
            )
            complete, missing = check.complete, check.missing + tuple(check.invalid)

        ctx = TransitionContext(
            actor=actor,
            reviewer=reviewer,
            reviewee=reviewee,
            cycle=cycle,
            progress=progress,
            now=now,
            tz=self.settings.tz,
            reviewer_own_status=reviewer_own_status,
            required_answers_complete=complete,
            missing_answers=missing,
        )
        return plan_transition(action, ctx, self._payload(payload)), ctx

    def _persist(
        self, repos: Repositories, outcome: TransitionOutcome, ctx: TransitionContext
    ) -> tuple[int | None, ReviewProgress | None]:
        now = ctx.now
        if outcome.create_row:
            base = ReviewProgress(
                review_id=None,
                cycle_id=ctx.cycle.cycle_id,
                reviewer_id=ctx.reviewer.employee_id,
                reviewee_id=ctx.reviewee.employee_id,
                survey_version=self.surveys.current.version,
            )
            row = apply_changes(base, outcome.changes)
            review_id: int | None = repos.progress.insert(row)
            row.review_id = review_id
            final: ReviewProgress | None = row
        else:
            assert ctx.progress is not None and ctx.progress.review_id is not None
            review_id = ctx.progress.review_id
            if outcome.delete_row:
                final = None
            else:
                final = repos.progress.update(review_id, outcome.changes)

        assert review_id is not None
        for role in outcome.lock_answers:
            repos.answers.lock(review_id, role, now)
        for role in outcome.unlock_answers:
            repos.answers.unlock(review_id, role)
        if outcome.clear_comments:
            repos.comments.delete_for_review(review_id)
        if outcome.comment:
            repos.comments.add(review_id, outcome.actor_id, outcome.comment, now)
        for entry in outcome.audit:
            repos.audit.add(entry, actor_id=outcome.actor_id, review_id=review_id, now=now)
        if outcome.delete_row:
            repos.progress.delete(review_id)  # answers/comments cascade
            return None, None
        return review_id, final

    # ---- answers -----------------------------------------------------------------------

    def save_answers(
        self,
        review_id: int,
        actor_id: str,
        role: RespondentRole | str,
        answers: Mapping[str, object],
    ) -> None:
        """Save draft answers. Enforces locking here, not only in the UI."""
        role = RespondentRole(role)
        now = self.clock.now()
        with self._sessions.begin() as session:
            repos = Repositories.for_session(session)
            actor = self._employee(repos, actor_id)
            progress = self._progress(repos, review_id)
            self._save_answers(repos, Viewer(employee=actor), progress, role, answers, now)

    def _save_answers(
        self,
        repos: Repositories,
        viewer: Viewer,
        progress: ReviewProgress,
        role: RespondentRole,
        answers: Mapping[str, object],
        now: datetime,
    ) -> None:
        assert progress.review_id is not None
        if repos.answers.is_locked(progress.review_id, role):
            raise DomainError(ErrorCode.ANSWERS_LOCKED)
        if not can_edit_answers(viewer, progress, role):
            raise DomainError(ErrorCode.CANNOT_EDIT_ANSWERS)
        values = normalise_answers(answers)
        check = check_answers(
            self.surveys.get(progress.survey_version), values, require_complete=False
        )
        if check.invalid:
            details = "; ".join(f"{k}: {v}" for k, v in check.invalid.items())
            raise DomainError(ErrorCode.ANSWERS_INVALID, details=details)
        repos.answers.save_drafts(progress.review_id, role, values, now)

    def get_answers(self, review_id: int, role: RespondentRole) -> dict[str, Answer]:
        """Raw answers. Callers must apply core.permissions.can_see_answers first."""
        with self._sessions() as session:
            return Repositories.for_session(session).answers.get(review_id, RespondentRole(role))

    # ---- queries -----------------------------------------------------------------------

    def list_team(self, cycle_id: int, manager_id: str) -> list[TeamRow]:
        """All active direct reports joined with their progress rows.

        People without a row are "Not started"; no placeholder rows are ever inserted.
        """
        with self._sessions() as session:
            repos = Repositories.for_session(session)
            reports = repos.employees.direct_reports(manager_id)
            rows = repos.progress.list_for_cycle(cycle_id, [e.employee_id for e in reports])
        return [
            TeamRow(emp, rows.get(emp.employee_id), derive_status(rows.get(emp.employee_id)))
            for emp in reports
        ]

    def get_progress(self, review_id: int) -> ReviewProgress | None:
        with self._sessions() as session:
            return Repositories.for_session(session).progress.get(review_id)

    def get_progress_for(self, cycle_id: int, reviewee_id: str) -> ReviewProgress | None:
        with self._sessions() as session:
            return Repositories.for_session(session).progress.get_for(cycle_id, reviewee_id)

    # ---- helpers -----------------------------------------------------------------------

    def _payload(self, payload: Mapping[str, Any]) -> TransitionPayload:
        meeting = payload.get("meeting_at")
        if meeting is not None and not isinstance(meeting, datetime):
            raise DomainError(ErrorCode.MISSING_PAYLOAD, field="meeting_at (date and time)")
        return TransitionPayload(
            meeting_at=to_utc_naive(meeting, self.settings.tz) if meeting else None,
            reason=payload.get("reason"),
            comment=payload.get("comment"),
        )

    @staticmethod
    def _employee(repos: Repositories, employee_id: str | None) -> Employee:
        emp = repos.employees.get(employee_id) if employee_id else None
        if emp is None:
            raise DomainError(ErrorCode.UNKNOWN_EMPLOYEE, employee_id=employee_id)
        return emp

    @staticmethod
    def _progress(repos: Repositories, review_id: int | None) -> ReviewProgress:
        progress = repos.progress.get(review_id) if review_id is not None else None
        if progress is None:
            raise DomainError(ErrorCode.REVIEW_NOT_FOUND, review_id=review_id)
        return progress

    @staticmethod
    def _cycle(repos: Repositories, cycle_id: int | None) -> ReviewCycle:
        cycle = repos.cycles.get(cycle_id) if cycle_id is not None else repos.cycles.get_open()
        if cycle is None:
            if cycle_id is None:
                raise DomainError(ErrorCode.NO_OPEN_CYCLE)
            raise DomainError(ErrorCode.CYCLE_NOT_FOUND, cycle_id=cycle_id)
        return cycle

    def _load_pair(
        self,
        repos: Repositories,
        review_id: int | None,
        action: Action,
        payload: Mapping[str, Any],
    ) -> tuple[ReviewProgress | None, ReviewCycle, Employee, Employee]:
        if action is Action.INITIATE:
            reviewee_id = payload.get("reviewee_id")
            if not reviewee_id:
                raise DomainError(ErrorCode.MISSING_PAYLOAD, field="reviewee_id")
            reviewee = self._employee(repos, reviewee_id)
            cycle = self._cycle(repos, payload.get("cycle_id"))
            if not reviewee.manager_id:
                raise DomainError(ErrorCode.NO_MANAGER, name=reviewee.name)
            reviewer = self._employee(repos, reviewee.manager_id)
            progress = repos.progress.get_for(cycle.cycle_id, reviewee.employee_id)
            return progress, cycle, reviewer, reviewee
        progress = self._progress(repos, review_id)
        cycle = self._cycle(repos, progress.cycle_id)
        reviewer = self._employee(repos, progress.reviewer_id)
        reviewee = self._employee(repos, progress.reviewee_id)
        return progress, cycle, reviewer, reviewee
