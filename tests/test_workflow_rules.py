"""Pure workflow rules (core.workflow), without a database."""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from core.errors import DomainError, ErrorCode
from core.models import (
    Action,
    CycleStatus,
    Employee,
    Level,
    NotificationType,
    RespondentRole,
    ReviewCycle,
    ReviewProgress,
    Status,
)
from core.workflow import (
    STEP_COLUMNS,
    TRANSITIONS,
    TransitionContext,
    TransitionPayload,
    plan_transition,
)
from tests.conftest import emp

TZ = ZoneInfo("Europe/Berlin")
NOW = datetime(2026, 10, 10, 8, 0)  # UTC
SOON = NOW + timedelta(hours=2)  # same local day as NOW

HR = emp("H1", Level.C, is_hr_admin=True)
C1 = emp("C1", Level.C, "H1")
B1 = emp("B1", Level.B, "C1")
A1 = emp("A1", Level.A, "B1")
OTHER = emp("C2", Level.C, "H1")
CYCLE = ReviewCycle(1, "2026 Annual", date(2026, 10, 1), date(2026, 11, 30))


def progress_at(
    status: Status, reviewer: Employee = C1, reviewee: Employee = B1
) -> ReviewProgress | None:
    """A consistent row for ``status`` (meeting later today, so ack and reschedule both work)."""
    if status is Status.NOT_STARTED:
        return None
    values: dict[str, object] = {}
    for step, columns in STEP_COLUMNS:
        for col in columns:
            if col.endswith("_by"):
                values[col] = reviewer.employee_id
            elif col == "meeting_at":
                values[col] = SOON
            else:
                values[col] = NOW - timedelta(days=1)
        if step is status:
            break
    if status is Status.ACKNOWLEDGED:
        values.pop("reviewee_ack_at")  # only the reviewer has confirmed so far
    return ReviewProgress(
        review_id=1,
        cycle_id=1,
        reviewer_id=reviewer.employee_id,
        reviewee_id=reviewee.employee_id,
        **values,  # type: ignore[arg-type]
    )


def ctx(
    status: Status,
    actor: Employee,
    *,
    reviewer: Employee = C1,
    reviewee: Employee = B1,
    now: datetime = NOW,
    cycle: ReviewCycle = CYCLE,
    complete: bool = True,
    own: Status = Status.COMPLETED,
    progress: ReviewProgress | None = None,
) -> TransitionContext:
    return TransitionContext(
        actor=actor,
        reviewer=reviewer,
        reviewee=reviewee,
        cycle=cycle,
        progress=progress if progress is not None else progress_at(status, reviewer, reviewee),
        now=now,
        tz=TZ,
        reviewer_own_status=own,
        required_answers_complete=complete,
        missing_answers=() if complete else ("goals",),
    )


ACTOR_FOR = {
    Action.INITIATE: C1,
    Action.SUBMIT_SELF: B1,
    Action.SUBMIT_MANAGER: C1,
    Action.SCHEDULE_MEETING: C1,
    Action.ACKNOWLEDGE: B1,
    Action.HR_ADVANCE: HR,
    Action.HR_REVERT: HR,
}
GOOD_PAYLOAD = TransitionPayload(meeting_at=NOW + timedelta(days=3), reason="Fixing a mix-up")


@pytest.mark.parametrize("action", list(Action))
@pytest.mark.parametrize("status", list(Status))
def test_transition_matrix(action: Action, status: Status) -> None:
    """Every action from every status: allowed exactly where the table says."""
    context = ctx(status, ACTOR_FOR[action])
    if status in TRANSITIONS[action].allowed_from:
        outcome = plan_transition(action, context, GOOD_PAYLOAD)
        assert outcome.old_status is status
        if not (action is Action.SCHEDULE_MEETING and status is Status.MEETING_SCHEDULED):
            assert outcome.new_status is not status  # every step moves (reschedule aside)
    else:
        with pytest.raises(DomainError) as err:
            plan_transition(action, context, GOOD_PAYLOAD)
        expected = (
            ErrorCode.ALREADY_INITIATED
            if action is Action.INITIATE
            else ErrorCode.INVALID_TRANSITION
        )
        assert err.value.code is expected


def test_happy_path_sequence_and_notifications() -> None:
    expected = [
        (
            Status.NOT_STARTED,
            Action.INITIATE,
            C1,
            Status.INITIATED,
            [(NotificationType.SELF_ASSESSMENT_REQUESTED, ("B1",))],
        ),
        (
            Status.INITIATED,
            Action.SUBMIT_SELF,
            B1,
            Status.SELF_ASSESSMENT_SUBMITTED,
            [(NotificationType.SELF_ASSESSMENT_READY, ("C1",))],
        ),
        (
            Status.SELF_ASSESSMENT_SUBMITTED,
            Action.SUBMIT_MANAGER,
            C1,
            Status.MANAGER_ASSESSMENT_SUBMITTED,
            [],
        ),
        (
            Status.MANAGER_ASSESSMENT_SUBMITTED,
            Action.SCHEDULE_MEETING,
            C1,
            Status.MEETING_SCHEDULED,
            [(NotificationType.MEETING_INVITE, ("B1", "C1"))],
        ),
    ]
    for status, action, actor, new_status, notes in expected:
        outcome = plan_transition(action, ctx(status, actor), GOOD_PAYLOAD)
        assert outcome.new_status is new_status
        assert [(n.type, n.recipient_ids) for n in outcome.notifications] == notes
        assert outcome.changes["updated_by"] == actor.employee_id


# ---- permissions (who may trigger) ----------------------------------------------------


@pytest.mark.parametrize(
    ("action", "status", "actor", "code"),
    [
        (Action.INITIATE, Status.NOT_STARTED, OTHER, ErrorCode.NOT_DIRECT_MANAGER),
        (Action.INITIATE, Status.NOT_STARTED, B1, ErrorCode.NOT_DIRECT_MANAGER),
        (Action.SUBMIT_SELF, Status.INITIATED, C1, ErrorCode.NOT_REVIEWEE),
        (Action.SUBMIT_MANAGER, Status.SELF_ASSESSMENT_SUBMITTED, B1, ErrorCode.NOT_REVIEWER),
        (Action.SUBMIT_MANAGER, Status.SELF_ASSESSMENT_SUBMITTED, HR, ErrorCode.NOT_REVIEWER),
        (Action.SCHEDULE_MEETING, Status.MANAGER_ASSESSMENT_SUBMITTED, B1, ErrorCode.NOT_REVIEWER),
        (Action.ACKNOWLEDGE, Status.MEETING_SCHEDULED, OTHER, ErrorCode.NOT_A_PARTY),
        (Action.HR_ADVANCE, Status.INITIATED, C1, ErrorCode.HR_ONLY),
        (Action.HR_REVERT, Status.INITIATED, B1, ErrorCode.HR_ONLY),
    ],
)
def test_wrong_actor_is_refused(
    action: Action, status: Status, actor: Employee, code: ErrorCode
) -> None:
    with pytest.raises(DomainError) as err:
        plan_transition(action, ctx(status, actor), GOOD_PAYLOAD)
    assert err.value.code is code


def test_permission_is_checked_before_status() -> None:
    """An outsider learns nothing about the review's status."""
    with pytest.raises(DomainError) as err:
        plan_transition(Action.SUBMIT_SELF, ctx(Status.COMPLETED, OTHER))
    assert err.value.code is ErrorCode.NOT_REVIEWEE


# ---- initiation ----------------------------------------------------------------------


def test_c_level_is_not_reviewable() -> None:
    with pytest.raises(DomainError) as err:
        plan_transition(Action.INITIATE, ctx(Status.NOT_STARTED, HR, reviewer=HR, reviewee=C1))
    assert err.value.code is ErrorCode.NOT_REVIEWABLE


@pytest.mark.parametrize("own", [s for s in Status if s is not Status.COMPLETED])
def test_b_manager_needs_own_review_completed(own: Status) -> None:
    context = ctx(Status.NOT_STARTED, B1, reviewer=B1, reviewee=A1, own=own)
    with pytest.raises(DomainError) as err:
        plan_transition(Action.INITIATE, context)
    assert err.value.code is ErrorCode.OWN_REVIEW_NOT_COMPLETED


def test_b_manager_can_initiate_after_own_review_completed() -> None:
    context = ctx(Status.NOT_STARTED, B1, reviewer=B1, reviewee=A1, own=Status.COMPLETED)
    outcome = plan_transition(Action.INITIATE, context)
    assert outcome.create_row and outcome.new_status is Status.INITIATED


def test_c_manager_is_not_subject_to_the_unlock_rule() -> None:
    outcome = plan_transition(Action.INITIATE, ctx(Status.NOT_STARTED, C1, own=Status.NOT_STARTED))
    assert outcome.new_status is Status.INITIATED


def test_hr_can_initiate_on_behalf_of_the_manager() -> None:
    outcome = plan_transition(Action.INITIATE, ctx(Status.NOT_STARTED, HR))
    assert outcome.changes["initiated_by"] == "H1"


def test_inactive_reviewee_cannot_be_initiated() -> None:
    gone = replace(B1, active=False)
    with pytest.raises(DomainError) as err:
        plan_transition(Action.INITIATE, ctx(Status.NOT_STARTED, C1, reviewee=gone))
    assert err.value.code is ErrorCode.EMPLOYEE_INACTIVE


def test_closed_cycle_blocks_normal_steps_but_not_hr() -> None:
    closed = replace(CYCLE, status=CycleStatus.CLOSED)
    with pytest.raises(DomainError) as err:
        plan_transition(Action.SUBMIT_SELF, ctx(Status.INITIATED, B1, cycle=closed))
    assert err.value.code is ErrorCode.CYCLE_CLOSED
    outcome = plan_transition(
        Action.HR_ADVANCE, ctx(Status.INITIATED, HR, cycle=closed), TransitionPayload(reason="late")
    )
    assert outcome.new_status is Status.SELF_ASSESSMENT_SUBMITTED


# ---- submissions ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("action", "status", "actor"),
    [
        (Action.SUBMIT_SELF, Status.INITIATED, B1),
        (Action.SUBMIT_MANAGER, Status.SELF_ASSESSMENT_SUBMITTED, C1),
    ],
)
def test_submit_requires_complete_answers(action: Action, status: Status, actor: Employee) -> None:
    with pytest.raises(DomainError) as err:
        plan_transition(action, ctx(status, actor, complete=False))
    assert err.value.code is ErrorCode.ANSWERS_INCOMPLETE
    assert "goals" in err.value.message


def test_submit_locks_the_submitters_answers() -> None:
    self_out = plan_transition(Action.SUBMIT_SELF, ctx(Status.INITIATED, B1))
    mgr_out = plan_transition(Action.SUBMIT_MANAGER, ctx(Status.SELF_ASSESSMENT_SUBMITTED, C1))
    assert self_out.lock_answers == [RespondentRole.SELF]
    assert mgr_out.lock_answers == [RespondentRole.MANAGER]


# ---- meeting -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("meeting_at", "code"),
    [
        (None, ErrorCode.MEETING_REQUIRED),
        (NOW - timedelta(minutes=1), ErrorCode.MEETING_IN_PAST),
        (NOW, ErrorCode.MEETING_IN_PAST),
        (datetime(2026, 12, 1, 9, 0), ErrorCode.MEETING_OUTSIDE_CYCLE),
    ],
)
def test_meeting_guards(meeting_at: datetime | None, code: ErrorCode) -> None:
    with pytest.raises(DomainError) as err:
        plan_transition(
            Action.SCHEDULE_MEETING,
            ctx(Status.MANAGER_ASSESSMENT_SUBMITTED, C1),
            TransitionPayload(meeting_at=meeting_at),
        )
    assert err.value.code is code


def test_meeting_on_due_date_in_local_time_is_inside_cycle() -> None:
    # 30.11. 23:30 Berlin = 22:30 UTC: still the due date locally.
    late = datetime(2026, 11, 30, 22, 30)
    outcome = plan_transition(
        Action.SCHEDULE_MEETING,
        ctx(Status.MANAGER_ASSESSMENT_SUBMITTED, C1),
        TransitionPayload(meeting_at=late),
    )
    assert outcome.changes["meeting_at"] == late


def test_meeting_can_be_rescheduled_until_it_happens() -> None:
    new_time = NOW + timedelta(days=5)
    outcome = plan_transition(
        Action.SCHEDULE_MEETING,
        ctx(Status.MEETING_SCHEDULED, C1),
        TransitionPayload(meeting_at=new_time),
    )
    assert outcome.changes["meeting_at"] == new_time
    assert outcome.notifications[0].context == {"rescheduled": True}
    with pytest.raises(DomainError) as err:
        plan_transition(
            Action.SCHEDULE_MEETING,
            ctx(Status.MEETING_SCHEDULED, C1, now=SOON + timedelta(minutes=1)),
            TransitionPayload(meeting_at=new_time),
        )
    assert err.value.code is ErrorCode.MEETING_ALREADY_HELD


# ---- acknowledgement and completion -------------------------------------------------


def test_acknowledge_only_from_the_meeting_date() -> None:
    tomorrow = replace(progress_at(Status.MEETING_SCHEDULED), meeting_at=NOW + timedelta(days=1))
    with pytest.raises(DomainError) as err:
        plan_transition(Action.ACKNOWLEDGE, ctx(Status.MEETING_SCHEDULED, B1, progress=tomorrow))
    assert err.value.code is ErrorCode.MEETING_NOT_YET_REACHED
    assert "11.10.2026" in err.value.message


def test_first_acknowledgement_is_partial_and_stores_reviewee_comment() -> None:
    outcome = plan_transition(
        Action.ACKNOWLEDGE,
        ctx(Status.MEETING_SCHEDULED, B1),
        TransitionPayload(comment="  Thanks "),
    )
    assert outcome.new_status is Status.ACKNOWLEDGED
    assert outcome.comment == "Thanks"
    assert "completed_at" not in outcome.changes


def test_second_acknowledgement_completes_automatically() -> None:
    # ACKNOWLEDGED fixture: reviewer already confirmed, reviewee confirms now.
    outcome = plan_transition(Action.ACKNOWLEDGE, ctx(Status.ACKNOWLEDGED, B1))
    assert outcome.new_status is Status.COMPLETED
    assert outcome.changes["completed_at"] == NOW
    assert [(n.type, n.recipient_ids) for n in outcome.notifications] == [
        (NotificationType.REVIEW_COMPLETED, ("B1", "C1"))
    ]


def test_cannot_acknowledge_twice() -> None:
    with pytest.raises(DomainError) as err:
        plan_transition(Action.ACKNOWLEDGE, ctx(Status.ACKNOWLEDGED, C1))
    assert err.value.code is ErrorCode.ALREADY_ACKNOWLEDGED


def test_reviewer_comment_is_ignored() -> None:
    outcome = plan_transition(
        Action.ACKNOWLEDGE, ctx(Status.MEETING_SCHEDULED, C1), TransitionPayload(comment="note")
    )
    assert outcome.comment is None


# ---- HR overrides --------------------------------------------------------------------


@pytest.mark.parametrize("action", [Action.HR_ADVANCE, Action.HR_REVERT])
@pytest.mark.parametrize("reason", [None, "", "   "])
def test_hr_override_needs_a_reason(action: Action, reason: str | None) -> None:
    with pytest.raises(DomainError) as err:
        plan_transition(action, ctx(Status.INITIATED, HR), TransitionPayload(reason=reason))
    assert err.value.code is ErrorCode.REASON_REQUIRED


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (Status.INITIATED, Status.SELF_ASSESSMENT_SUBMITTED),
        (Status.SELF_ASSESSMENT_SUBMITTED, Status.MANAGER_ASSESSMENT_SUBMITTED),
        (Status.MANAGER_ASSESSMENT_SUBMITTED, Status.MEETING_SCHEDULED),
        (Status.MEETING_SCHEDULED, Status.ACKNOWLEDGED),
        (Status.ACKNOWLEDGED, Status.COMPLETED),
    ],
)
def test_hr_advance_moves_exactly_one_step(status: Status, expected: Status) -> None:
    outcome = plan_transition(Action.HR_ADVANCE, ctx(status, HR), TransitionPayload(reason="r"))
    assert outcome.new_status is expected
    assert all(entry.reason == "r" for entry in outcome.audit)


@pytest.mark.parametrize(
    ("status", "expected", "cleared"),
    [
        (Status.SELF_ASSESSMENT_SUBMITTED, Status.INITIATED, {"self_submitted_at"}),
        (
            Status.MANAGER_ASSESSMENT_SUBMITTED,
            Status.SELF_ASSESSMENT_SUBMITTED,
            {"manager_submitted_at"},
        ),
        (
            Status.MEETING_SCHEDULED,
            Status.MANAGER_ASSESSMENT_SUBMITTED,
            {"meeting_at", "meeting_set_by"},
        ),
        (Status.ACKNOWLEDGED, Status.MEETING_SCHEDULED, {"reviewer_ack_at"}),
        (
            Status.COMPLETED,
            Status.MEETING_SCHEDULED,
            {"reviewee_ack_at", "reviewer_ack_at", "completed_at"},
        ),
    ],
)
def test_hr_revert_clears_step_and_all_later(
    status: Status, expected: Status, cleared: set
) -> None:
    outcome = plan_transition(Action.HR_REVERT, ctx(status, HR), TransitionPayload(reason="r"))
    assert outcome.new_status is expected
    cleared_now = {col for col, value in outcome.changes.items() if value is None}
    assert cleared_now == cleared


def test_hr_revert_unlocks_matching_answers_and_clears_comments() -> None:
    reason = TransitionPayload(reason="r")
    assert plan_transition(
        Action.HR_REVERT, ctx(Status.SELF_ASSESSMENT_SUBMITTED, HR), reason
    ).unlock_answers == [RespondentRole.SELF]
    assert plan_transition(
        Action.HR_REVERT, ctx(Status.MANAGER_ASSESSMENT_SUBMITTED, HR), reason
    ).unlock_answers == [RespondentRole.MANAGER]
    assert plan_transition(Action.HR_REVERT, ctx(Status.COMPLETED, HR), reason).clear_comments


def test_hr_revert_of_initiation_removes_the_row() -> None:
    outcome = plan_transition(
        Action.HR_REVERT, ctx(Status.INITIATED, HR), TransitionPayload(reason="r")
    )
    assert outcome.delete_row and outcome.new_status is Status.NOT_STARTED


def test_same_rules_for_c_to_b_and_b_to_a() -> None:
    """The generic pair: identical outcomes whatever the levels."""
    for reviewer, reviewee in ((C1, B1), (B1, A1)):
        statuses = []
        for status, action, actor in [
            (Status.INITIATED, Action.SUBMIT_SELF, reviewee),
            (Status.SELF_ASSESSMENT_SUBMITTED, Action.SUBMIT_MANAGER, reviewer),
            (Status.MANAGER_ASSESSMENT_SUBMITTED, Action.SCHEDULE_MEETING, reviewer),
            (Status.MEETING_SCHEDULED, Action.ACKNOWLEDGE, reviewee),
            (Status.ACKNOWLEDGED, Action.ACKNOWLEDGE, reviewee),
        ]:
            context = ctx(status, actor, reviewer=reviewer, reviewee=reviewee)
            statuses.append(plan_transition(action, context, GOOD_PAYLOAD).new_status)
        assert statuses == [
            Status.SELF_ASSESSMENT_SUBMITTED,
            Status.MANAGER_ASSESSMENT_SUBMITTED,
            Status.MEETING_SCHEDULED,
            Status.ACKNOWLEDGED,
            Status.COMPLETED,
        ]
