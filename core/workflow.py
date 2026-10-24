"""Review workflow: step columns, ``derive_status``, the transition table and its rules.

Pure domain logic. ``plan_transition`` checks permissions and guards and returns a
``TransitionOutcome`` describing what must change. The service layer
(``services.review_service.ReviewService.transition``) loads the data, calls it and
persists the outcome (row update, audit entries, notifications) in ONE transaction.

The same code handles C→B and B→A reviews: a review is always the generic pair
(reviewer = manager, reviewee = direct report). Level only matters for the B→A
unlock rule, permissions and wording.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import datetime, tzinfo

from core.errors import DomainError, ErrorCode
from core.models import (
    Action,
    AuditEntry,
    CycleStatus,
    Employee,
    Level,
    NotificationIntent,
    NotificationType,
    Party,
    RespondentRole,
    ReviewCycle,
    ReviewProgress,
    Status,
)
from core.timeutil import format_date, local_date

# --------------------------------------------------------------------------------------
# Step columns: the single definition of which columns belong to which step, in order.
# --------------------------------------------------------------------------------------

STEP_COLUMNS: tuple[tuple[Status, tuple[str, ...]], ...] = (
    (Status.INITIATED, ("initiated_at", "initiated_by")),
    (Status.SELF_ASSESSMENT_SUBMITTED, ("self_submitted_at",)),
    (Status.MANAGER_ASSESSMENT_SUBMITTED, ("manager_submitted_at",)),
    (Status.MEETING_SCHEDULED, ("meeting_at", "meeting_set_by")),
    (Status.ACKNOWLEDGED, ("reviewee_ack_at", "reviewer_ack_at")),
    (Status.COMPLETED, ("completed_at",)),
)

# Timestamp columns that mark a step as done (the *_by columns are metadata).
STEP_MARKERS: dict[Status, tuple[str, ...]] = {
    Status.INITIATED: ("initiated_at",),
    Status.SELF_ASSESSMENT_SUBMITTED: ("self_submitted_at",),
    Status.MANAGER_ASSESSMENT_SUBMITTED: ("manager_submitted_at",),
    Status.MEETING_SCHEDULED: ("meeting_at",),
    Status.ACKNOWLEDGED: ("reviewee_ack_at", "reviewer_ack_at"),
    Status.COMPLETED: ("completed_at",),
}

STEP_ORDER: tuple[Status, ...] = (Status.NOT_STARTED, *(s for s, _ in STEP_COLUMNS))
PROGRESS_COLUMNS: tuple[str, ...] = tuple(c for _, cols in STEP_COLUMNS for c in cols)
_MARKER_COLUMNS = frozenset(c for cols in STEP_MARKERS.values() for c in cols)


def _step_done(row: ReviewProgress, step: Status) -> bool:
    return any(getattr(row, col) is not None for col in STEP_MARKERS[step])


def check_column_order(row: ReviewProgress) -> None:
    """Raise if a later step is filled while an earlier one is empty."""
    first_gap: Status | None = None
    for step, _ in STEP_COLUMNS:
        done = _step_done(row, step)
        if done and first_gap is not None:
            raise DomainError(
                ErrorCode.INCONSISTENT_ROW,
                review_id=row.review_id,
                detail=f"{step.value} is filled but {first_gap.value} is empty",
            )
        if not done and first_gap is None:
            first_gap = step
    if row.completed_at is not None and (
        row.reviewee_ack_at is None or row.reviewer_ack_at is None
    ):
        raise DomainError(
            ErrorCode.INCONSISTENT_ROW,
            review_id=row.review_id,
            detail="completed without both acknowledgements",
        )


def derive_status(row: ReviewProgress | None) -> Status:
    """The ONE place that turns filled columns into a status. No row = Not started."""
    if row is None:
        return Status.NOT_STARTED
    check_column_order(row)
    for step, _ in reversed(STEP_COLUMNS):
        if _step_done(row, step):
            return step
    return Status.NOT_STARTED


def step_index(status: Status) -> int:
    """Position of a status in the process (0 = Not started, 6 = Completed)."""
    return STEP_ORDER.index(status)


# Who acts next, per status (used by the UI for "what's next").
NEXT_STEP: dict[Status, tuple[Party, Action] | None] = {
    Status.NOT_STARTED: (Party.REVIEWER, Action.INITIATE),
    Status.INITIATED: (Party.REVIEWEE, Action.SUBMIT_SELF),
    Status.SELF_ASSESSMENT_SUBMITTED: (Party.REVIEWER, Action.SUBMIT_MANAGER),
    Status.MANAGER_ASSESSMENT_SUBMITTED: (Party.REVIEWER, Action.SCHEDULE_MEETING),
    Status.MEETING_SCHEDULED: (Party.EITHER, Action.ACKNOWLEDGE),
    Status.ACKNOWLEDGED: (Party.EITHER, Action.ACKNOWLEDGE),
    Status.COMPLETED: None,
}


# --------------------------------------------------------------------------------------
# Transition inputs and outputs
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class TransitionContext:
    """Everything a rule needs to decide. Built by the service layer."""

    actor: Employee
    reviewer: Employee
    reviewee: Employee
    cycle: ReviewCycle
    progress: ReviewProgress | None
    now: datetime  # naive UTC
    tz: tzinfo
    reviewer_own_status: Status = Status.NOT_STARTED  # reviewer's own review, same cycle
    required_answers_complete: bool = False
    missing_answers: tuple[str, ...] = ()

    @property
    def status(self) -> Status:
        return derive_status(self.progress)


@dataclass(frozen=True)
class TransitionPayload:
    meeting_at: datetime | None = None  # naive UTC
    reason: str | None = None
    comment: str | None = None


@dataclass
class TransitionOutcome:
    """What the service must persist, all in one transaction."""

    action: Action
    actor_id: str
    old_status: Status
    reason: str | None = None
    new_status: Status = Status.NOT_STARTED
    changes: dict[str, object] = field(default_factory=dict)
    create_row: bool = False
    delete_row: bool = False
    lock_answers: list[RespondentRole] = field(default_factory=list)
    unlock_answers: list[RespondentRole] = field(default_factory=list)
    clear_comments: bool = False
    comment: str | None = None
    audit: list[AuditEntry] = field(default_factory=list)
    notifications: list[NotificationIntent] = field(default_factory=list)


Handler = Callable[[TransitionContext, TransitionPayload, TransitionOutcome], None]


@dataclass(frozen=True)
class TransitionRule:
    action: Action
    allowed_from: frozenset[Status]
    who: Party
    handler: Handler
    requires_open_cycle: bool = True


# --------------------------------------------------------------------------------------
# Helpers used by the rules
# --------------------------------------------------------------------------------------


def _fmt(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat(timespec="seconds")
    return str(value)


def _current(ctx: TransitionContext, out: TransitionOutcome, column: str) -> object:
    """Column value including changes already planned in this transition."""
    if column in out.changes:
        return out.changes[column]
    return getattr(ctx.progress, column) if ctx.progress is not None else None


def _set(ctx: TransitionContext, out: TransitionOutcome, column: str, value: object) -> None:
    old = getattr(ctx.progress, column) if ctx.progress is not None else None
    out.changes[column] = value
    if column in _MARKER_COLUMNS:  # *_by columns are implied by the actor, not audited
        out.audit.append(
            AuditEntry(
                action=out.action.value,
                field_changed=column,
                old_value=_fmt(old),
                new_value=_fmt(value),
                reason=out.reason,
            )
        )


def _notify(
    out: TransitionOutcome, kind: NotificationType, *recipients: str, **ctx: object
) -> None:
    out.notifications.append(NotificationIntent(kind, tuple(recipients), dict(ctx)))


def _both(ctx: TransitionContext) -> tuple[str, str]:
    return (ctx.reviewee.employee_id, ctx.reviewer.employee_id)


def _check_party(rule: TransitionRule, ctx: TransitionContext) -> None:
    actor_id = ctx.actor.employee_id
    is_reviewer = actor_id == ctx.reviewer.employee_id
    is_reviewee = actor_id == ctx.reviewee.employee_id
    if rule.who is Party.REVIEWEE and not is_reviewee:
        raise DomainError(ErrorCode.NOT_REVIEWEE)
    if rule.who is Party.REVIEWER and not is_reviewer:
        raise DomainError(ErrorCode.NOT_REVIEWER)
    if rule.who is Party.EITHER and not (is_reviewer or is_reviewee):
        raise DomainError(ErrorCode.NOT_A_PARTY)
    if rule.who is Party.HR and not ctx.actor.is_hr_admin:
        raise DomainError(ErrorCode.HR_ONLY)
    if rule.who is Party.REVIEWER_OR_HR and not (is_reviewer or ctx.actor.is_hr_admin):
        code = (
            ErrorCode.NOT_DIRECT_MANAGER
            if rule.action is Action.INITIATE
            else ErrorCode.NOT_REVIEWER
        )
        raise DomainError(code)


def _require_reason(payload: TransitionPayload) -> str:
    reason = (payload.reason or "").strip()
    if not reason:
        raise DomainError(ErrorCode.REASON_REQUIRED)
    return reason


def _validate_meeting(ctx: TransitionContext, meeting_at: datetime | None) -> datetime:
    if meeting_at is None:
        raise DomainError(ErrorCode.MEETING_REQUIRED)
    if meeting_at <= ctx.now:
        raise DomainError(ErrorCode.MEETING_IN_PAST)
    day = local_date(meeting_at, ctx.tz)
    if not ctx.cycle.start_date <= day <= ctx.cycle.due_date:
        raise DomainError(
            ErrorCode.MEETING_OUTSIDE_CYCLE,
            start=format_date(ctx.cycle.start_date),
            due=format_date(ctx.cycle.due_date),
        )
    return meeting_at


def _complete(ctx: TransitionContext, out: TransitionOutcome) -> None:
    _set(ctx, out, "completed_at", ctx.now)
    _notify(out, NotificationType.REVIEW_COMPLETED, *_both(ctx))


def _complete_if_both_acknowledged(ctx: TransitionContext, out: TransitionOutcome) -> None:
    both = (
        _current(ctx, out, "reviewee_ack_at") is not None
        and _current(ctx, out, "reviewer_ack_at") is not None
    )
    if both:
        _complete(ctx, out)


# --------------------------------------------------------------------------------------
# Rules (one handler per action)
# --------------------------------------------------------------------------------------


def _initiate(ctx: TransitionContext, p: TransitionPayload, out: TransitionOutcome) -> None:
    # Assumption (spec §2): C-level managers and the HR Director are not reviewed.
    if ctx.reviewee.level is Level.C:
        raise DomainError(ErrorCode.NOT_REVIEWABLE, name=ctx.reviewee.name)
    if not ctx.reviewee.active:
        raise DomainError(ErrorCode.EMPLOYEE_INACTIVE, name=ctx.reviewee.name)
    # B managers may initiate their A reports only after their own review is COMPLETED.
    # This applies even when HR initiates on their behalf; HR can advance the B's own
    # review first if needed.
    if ctx.reviewer.level is Level.B and ctx.reviewer_own_status is not Status.COMPLETED:
        raise DomainError(ErrorCode.OWN_REVIEW_NOT_COMPLETED)
    out.create_row = True
    _set(ctx, out, "initiated_at", ctx.now)
    _set(ctx, out, "initiated_by", ctx.actor.employee_id)
    _notify(out, NotificationType.SELF_ASSESSMENT_REQUESTED, ctx.reviewee.employee_id)


def _submit_self(ctx: TransitionContext, p: TransitionPayload, out: TransitionOutcome) -> None:
    if not ctx.required_answers_complete:
        raise DomainError(ErrorCode.ANSWERS_INCOMPLETE, missing=", ".join(ctx.missing_answers))
    _set(ctx, out, "self_submitted_at", ctx.now)
    out.lock_answers.append(RespondentRole.SELF)
    _notify(out, NotificationType.SELF_ASSESSMENT_READY, ctx.reviewer.employee_id)


def _submit_manager(ctx: TransitionContext, p: TransitionPayload, out: TransitionOutcome) -> None:
    if not ctx.required_answers_complete:
        raise DomainError(ErrorCode.ANSWERS_INCOMPLETE, missing=", ".join(ctx.missing_answers))
    _set(ctx, out, "manager_submitted_at", ctx.now)
    out.lock_answers.append(RespondentRole.MANAGER)


def _schedule_meeting(ctx: TransitionContext, p: TransitionPayload, out: TransitionOutcome) -> None:
    rescheduling = ctx.status is Status.MEETING_SCHEDULED
    held = (
        ctx.progress is not None
        and ctx.progress.meeting_at is not None
        and (ctx.now >= ctx.progress.meeting_at)
    )
    if rescheduling and held:  # can be rescheduled until the meeting takes place
        raise DomainError(ErrorCode.MEETING_ALREADY_HELD)
    meeting_at = _validate_meeting(ctx, p.meeting_at)
    _set(ctx, out, "meeting_at", meeting_at)
    _set(ctx, out, "meeting_set_by", ctx.actor.employee_id)
    _notify(out, NotificationType.MEETING_INVITE, *_both(ctx), rescheduled=rescheduling)


def _acknowledge(ctx: TransitionContext, p: TransitionPayload, out: TransitionOutcome) -> None:
    meeting_at = ctx.progress.meeting_at if ctx.progress else None
    if meeting_at is None:  # unreachable given allowed_from, kept as a safety net
        raise DomainError(
            ErrorCode.INVALID_TRANSITION, action=out.action.value, status="no meeting"
        )
    # Spec: "From the meeting date, both ... confirm". Compared on the local calendar day.
    if local_date(ctx.now, ctx.tz) < local_date(meeting_at, ctx.tz):
        raise DomainError(
            ErrorCode.MEETING_NOT_YET_REACHED, date=format_date(local_date(meeting_at, ctx.tz))
        )
    is_reviewee = ctx.actor.employee_id == ctx.reviewee.employee_id
    column = "reviewee_ack_at" if is_reviewee else "reviewer_ack_at"
    if _current(ctx, out, column) is not None:
        raise DomainError(ErrorCode.ALREADY_ACKNOWLEDGED)
    _set(ctx, out, column, ctx.now)
    # Assumption: only the reviewee's comment is stored; a reviewer comment is ignored.
    if is_reviewee and p.comment and p.comment.strip():
        out.comment = p.comment.strip()
    _complete_if_both_acknowledged(ctx, out)


def _hr_advance(ctx: TransitionContext, p: TransitionPayload, out: TransitionOutcome) -> None:
    """Fill the next step on the participants' behalf. Guards are bypassed by design."""
    _require_reason(p)
    status = ctx.status
    if status is Status.INITIATED:
        _set(ctx, out, "self_submitted_at", ctx.now)
        out.lock_answers.append(RespondentRole.SELF)
        _notify(out, NotificationType.SELF_ASSESSMENT_READY, ctx.reviewer.employee_id)
    elif status is Status.SELF_ASSESSMENT_SUBMITTED:
        _set(ctx, out, "manager_submitted_at", ctx.now)
        out.lock_answers.append(RespondentRole.MANAGER)
    elif status is Status.MANAGER_ASSESSMENT_SUBMITTED:
        # Assumption: without a given meeting time, HR records the meeting as "now".
        _set(ctx, out, "meeting_at", p.meeting_at or ctx.now)
        _set(ctx, out, "meeting_set_by", ctx.actor.employee_id)
        _notify(out, NotificationType.MEETING_INVITE, *_both(ctx), rescheduled=False)
    else:  # MEETING_SCHEDULED or ACKNOWLEDGED: fill one acknowledgement, reviewee first
        column = (
            "reviewee_ack_at"
            if _current(ctx, out, "reviewee_ack_at") is None
            else ("reviewer_ack_at")
        )
        _set(ctx, out, column, ctx.now)
        _complete_if_both_acknowledged(ctx, out)


def _hr_revert(ctx: TransitionContext, p: TransitionPayload, out: TransitionOutcome) -> None:
    """Clear the current step and all later ones, unlocking matching answers."""
    _require_reason(p)
    status = ctx.status
    # Completion is automatic, so stepping back from COMPLETED clears the acknowledgements.
    target = Status.ACKNOWLEDGED if status is Status.COMPLETED else status
    start = next(i for i, (step, _) in enumerate(STEP_COLUMNS) if step is target)
    assert ctx.progress is not None  # guaranteed: NOT_STARTED is not in allowed_from
    for step, columns in STEP_COLUMNS[start:]:
        if not _step_done(ctx.progress, step):
            continue
        for column in columns:
            if getattr(ctx.progress, column) is not None:
                _set(ctx, out, column, None)
        if step is Status.SELF_ASSESSMENT_SUBMITTED:
            out.unlock_answers.append(RespondentRole.SELF)
        elif step is Status.MANAGER_ASSESSMENT_SUBMITTED:
            out.unlock_answers.append(RespondentRole.MANAGER)
        elif step is Status.ACKNOWLEDGED:
            out.clear_comments = True
    if target is Status.INITIATED:
        # Reverting initiation returns the reviewee to "Not started": the row (and its
        # draft answers) is removed, since rows only exist for initiated reviews.
        out.delete_row = True


# --------------------------------------------------------------------------------------
# Transition table
# --------------------------------------------------------------------------------------

_ACTIVE = frozenset(s for s in Status if s is not Status.NOT_STARTED)

TRANSITIONS: dict[Action, TransitionRule] = {
    rule.action: rule
    for rule in (
        TransitionRule(
            Action.INITIATE, frozenset({Status.NOT_STARTED}), Party.REVIEWER_OR_HR, _initiate
        ),
        TransitionRule(
            Action.SUBMIT_SELF, frozenset({Status.INITIATED}), Party.REVIEWEE, _submit_self
        ),
        TransitionRule(
            Action.SUBMIT_MANAGER,
            frozenset({Status.SELF_ASSESSMENT_SUBMITTED}),
            Party.REVIEWER,
            _submit_manager,
        ),
        TransitionRule(
            Action.SCHEDULE_MEETING,
            frozenset({Status.MANAGER_ASSESSMENT_SUBMITTED, Status.MEETING_SCHEDULED}),
            Party.REVIEWER_OR_HR,
            _schedule_meeting,
        ),
        TransitionRule(
            Action.ACKNOWLEDGE,
            frozenset({Status.MEETING_SCHEDULED, Status.ACKNOWLEDGED}),
            Party.EITHER,
            _acknowledge,
        ),
        # HR overrides also work on closed cycles, so HR can tidy up afterwards.
        TransitionRule(
            Action.HR_ADVANCE,
            _ACTIVE - {Status.COMPLETED},
            Party.HR,
            _hr_advance,
            requires_open_cycle=False,
        ),
        TransitionRule(Action.HR_REVERT, _ACTIVE, Party.HR, _hr_revert, requires_open_cycle=False),
    )
}


def apply_changes(progress: ReviewProgress, changes: dict[str, object]) -> ReviewProgress:
    return replace(progress, **changes)  # type: ignore[arg-type]


def plan_transition(
    action: Action, ctx: TransitionContext, payload: TransitionPayload | None = None
) -> TransitionOutcome:
    """Check permissions and guards, and describe the resulting change.

    Raises ``DomainError`` for anything not allowed. Never touches storage.
    """
    payload = payload or TransitionPayload()
    rule = TRANSITIONS[action]

    _check_party(rule, ctx)  # permission first, so status is never leaked to outsiders
    if rule.requires_open_cycle and ctx.cycle.status is not CycleStatus.OPEN:
        raise DomainError(ErrorCode.CYCLE_CLOSED, cycle=ctx.cycle.name)
    status = ctx.status
    if status not in rule.allowed_from:
        if action is Action.INITIATE:
            raise DomainError(ErrorCode.ALREADY_INITIATED)
        raise DomainError(ErrorCode.INVALID_TRANSITION, action=action.value, status=status.value)

    out = TransitionOutcome(
        action=action,
        actor_id=ctx.actor.employee_id,
        old_status=status,
        reason=(payload.reason or "").strip() or None,
    )
    rule.handler(ctx, payload, out)

    if out.delete_row:
        out.new_status = Status.NOT_STARTED
        return out
    out.changes["updated_at"] = ctx.now
    out.changes["updated_by"] = ctx.actor.employee_id
    base = ctx.progress or ReviewProgress(
        review_id=None,
        cycle_id=ctx.cycle.cycle_id,
        reviewer_id=ctx.reviewer.employee_id,
        reviewee_id=ctx.reviewee.employee_id,
    )
    out.new_status = derive_status(apply_changes(base, out.changes))  # also re-checks order
    return out
