"""ReviewService.transition and answers, against an in-memory SQLite database."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from core.errors import DomainError, ErrorCode
from core.models import RespondentRole, ReviewCycle, ReviewProgress, Status
from core.workflow import derive_status
from db.repositories import Repositories
from db.schema import audit_log, notification_outbox, review_comments, survey_answers
from services.clock import FixedClock
from services.container import AppServices
from tests.conftest import FULL_ANSWERS, drive


def count(services: AppServices, table) -> int:  # type: ignore[no-untyped-def]
    with services.sessions() as session:
        return int(session.execute(select(func.count()).select_from(table)).scalar_one())


def outbox(services: AppServices) -> list[tuple[str, str]]:
    with services.sessions() as session:
        rows = session.execute(
            select(notification_outbox.c.type, notification_outbox.c.recipient_email).order_by(
                notification_outbox.c.id
            )
        ).all()
    return [(t, e) for t, e in rows]


def status_of(services: AppServices, review_id: int) -> Status:
    return derive_status(services.reviews.get_progress(review_id))


# ---- writeback table basics ----------------------------------------------------------


def test_review_progress_starts_empty(services: AppServices) -> None:
    with services.sessions() as session:
        assert Repositories.for_session(session).progress.count() == 0


def test_team_view_shows_everyone_as_not_started_without_rows(
    services: AppServices, cycle: ReviewCycle
) -> None:
    team = services.reviews.list_team(cycle.cycle_id, "C1")
    assert [(r.employee.employee_id, r.status) for r in team] == [
        ("B1", Status.NOT_STARTED),
        ("B2", Status.NOT_STARTED),
    ]
    with services.sessions() as session:
        assert Repositories.for_session(session).progress.count() == 0  # nothing inserted


def test_cycle_open_notifies_c_level_managers(services: AppServices) -> None:
    assert sorted(outbox(services)) == [
        ("cycle_started", "carl@example.com"),
        ("cycle_started", "hilde@example.com"),
    ]


# ---- full flows ----------------------------------------------------------------------


def test_full_c_to_b_then_b_to_a_flow(services: AppServices, clock: FixedClock) -> None:
    c_to_b = drive(services, clock, "C1", "B1", Status.COMPLETED)
    assert status_of(services, c_to_b) is Status.COMPLETED
    b_to_a = drive(services, clock, "B1", "A1", Status.COMPLETED)
    assert status_of(services, b_to_a) is Status.COMPLETED

    types = [t for t, _ in outbox(services)]
    # cycle start (2) + per review: request, ready, 2 invites, 2 completions
    assert len(types) == 2 + 2 * 6
    assert types.count("review_completed") == 4
    with services.sessions() as session:
        comments = session.execute(select(review_comments.c.comment)).scalars().all()
    assert comments == ["Good talk", "Good talk"]


def test_b_to_a_unlock_rule(services: AppServices, clock: FixedClock) -> None:
    with pytest.raises(DomainError) as err:
        services.reviews.transition(None, "initiate", "B1", {"reviewee_id": "A1"})
    assert err.value.code is ErrorCode.OWN_REVIEW_NOT_COMPLETED

    review_id = drive(services, clock, "C1", "B1", Status.ACKNOWLEDGED)  # one ack only
    with pytest.raises(DomainError):
        services.reviews.transition(None, "initiate", "B1", {"reviewee_id": "A1"})

    services.reviews.transition(review_id, "acknowledge", "C1")  # second ack → COMPLETED
    result = services.reviews.transition(None, "initiate", "B1", {"reviewee_id": "A1"})
    assert result.new_status is Status.INITIATED
    # B2's own review isn't complete, so B2 is still blocked.
    with pytest.raises(DomainError):
        services.reviews.transition(None, "initiate", "B2", {"reviewee_id": "A3"})


def test_cannot_initiate_twice(services: AppServices) -> None:
    services.reviews.transition(None, "initiate", "C1", {"reviewee_id": "B1"})
    with pytest.raises(DomainError) as err:
        services.reviews.transition(None, "initiate", "C1", {"reviewee_id": "B1"})
    assert err.value.code is ErrorCode.ALREADY_INITIATED


def test_unique_constraint_prevents_duplicate_rows(
    services: AppServices, cycle: ReviewCycle
) -> None:
    row = ReviewProgress(None, cycle.cycle_id, "C1", "B1", initiated_at=datetime(2026, 10, 1))
    with pytest.raises(IntegrityError), services.sessions.begin() as session:
        repo = Repositories.for_session(session).progress
        repo.insert(row)
        repo.insert(row)


def test_only_direct_manager_can_initiate(services: AppServices) -> None:
    with pytest.raises(DomainError) as err:
        services.reviews.transition(None, "initiate", "B2", {"reviewee_id": "A1"})
    assert err.value.code is ErrorCode.NOT_DIRECT_MANAGER


def test_c_level_not_reviewed(services: AppServices) -> None:
    with pytest.raises(DomainError) as err:
        services.reviews.transition(None, "initiate", "H1", {"reviewee_id": "C1"})
    assert err.value.code is ErrorCode.NOT_REVIEWABLE


def test_invalid_transition_changes_nothing(services: AppServices, clock: FixedClock) -> None:
    review_id = drive(services, clock, "C1", "B1", Status.INITIATED)
    before = services.reviews.get_progress(review_id)
    audit_before = count(services, audit_log)
    with pytest.raises(DomainError) as err:
        services.reviews.transition(review_id, "submit_manager", "C1", {"answers": FULL_ANSWERS})
    assert err.value.code is ErrorCode.INVALID_TRANSITION
    assert services.reviews.get_progress(review_id) == before
    assert count(services, audit_log) == audit_before


def test_failure_mid_transaction_rolls_everything_back(
    services: AppServices, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Row, audit and outbox are written together or not at all."""
    audit_before, outbox_before = count(services, audit_log), count(services, notification_outbox)

    def boom(*args: object, **kwargs: object) -> int:
        raise RuntimeError("SMTP template exploded")

    monkeypatch.setattr(services.reviews.queue, "enqueue", boom)
    with pytest.raises(RuntimeError):
        services.reviews.transition(None, "initiate", "C1", {"reviewee_id": "B1"})
    with services.sessions() as session:
        assert Repositories.for_session(session).progress.count() == 0
    assert count(services, audit_log) == audit_before
    assert count(services, notification_outbox) == outbox_before


def test_unknown_review_and_employee(services: AppServices) -> None:
    with pytest.raises(DomainError) as err:
        services.reviews.transition(999, "submit_self", "B1")
    assert err.value.code is ErrorCode.REVIEW_NOT_FOUND
    with pytest.raises(DomainError) as err:
        services.reviews.transition(None, "initiate", "nobody", {"reviewee_id": "B1"})
    assert err.value.code is ErrorCode.UNKNOWN_EMPLOYEE


# ---- answers: drafts and locking -----------------------------------------------------


def test_drafts_then_submit(services: AppServices, clock: FixedClock) -> None:
    review_id = drive(services, clock, "C1", "B1", Status.INITIATED)
    services.reviews.save_answers(review_id, "B1", "self", {"goals": 3, "strengths": "Calm"})
    services.reviews.save_answers(review_id, "B1", "self", {"goals": 4})  # overwrite draft
    answers = services.reviews.get_answers(review_id, RespondentRole.SELF)
    assert answers["goals"].value == "4" and answers["goals"].is_draft

    with pytest.raises(DomainError) as err:
        services.reviews.transition(review_id, "submit_self", "B1")
    assert err.value.code is ErrorCode.ANSWERS_INCOMPLETE

    services.reviews.transition(review_id, "submit_self", "B1", {"answers": FULL_ANSWERS})
    answers = services.reviews.get_answers(review_id, RespondentRole.SELF)
    assert all(not a.is_draft and a.submitted_at for a in answers.values())


def test_invalid_answer_values_are_rejected(services: AppServices, clock: FixedClock) -> None:
    review_id = drive(services, clock, "C1", "B1", Status.INITIATED)
    for bad in ({"goals": 7}, {"goals": "great"}, {"no_such_question": "x"}):
        with pytest.raises(DomainError) as err:
            services.reviews.save_answers(review_id, "B1", "self", bad)
        assert err.value.code is ErrorCode.ANSWERS_INVALID


def test_self_answers_locked_after_submission(services: AppServices, clock: FixedClock) -> None:
    review_id = drive(services, clock, "C1", "B1", Status.SELF_ASSESSMENT_SUBMITTED)
    with pytest.raises(DomainError) as err:
        services.reviews.save_answers(review_id, "B1", "self", {"goals": 1})
    assert err.value.code is ErrorCode.ANSWERS_LOCKED
    # Locking holds in the repository too, not only in the service.
    with pytest.raises(PermissionError), services.sessions.begin() as session:
        Repositories.for_session(session).answers.save_drafts(
            review_id, RespondentRole.SELF, {"goals": "1"}, clock.now()
        )


def test_answers_editable_only_by_owner_at_their_step(
    services: AppServices, clock: FixedClock
) -> None:
    review_id = drive(services, clock, "C1", "B1", Status.INITIATED)
    with pytest.raises(DomainError) as err:  # manager can't start before self-assessment
        services.reviews.save_answers(review_id, "C1", "manager", {"goals": 3})
    assert err.value.code is ErrorCode.CANNOT_EDIT_ANSWERS
    with pytest.raises(DomainError):  # manager can't edit the self side
        services.reviews.save_answers(review_id, "C1", "self", {"goals": 3})
    with pytest.raises(DomainError):  # HR can't edit either (HR reverts instead)
        services.reviews.save_answers(review_id, "H1", "self", {"goals": 3})


def test_manager_answers_locked_after_submission(services: AppServices, clock: FixedClock) -> None:
    review_id = drive(services, clock, "C1", "B1", Status.MANAGER_ASSESSMENT_SUBMITTED)
    with pytest.raises(DomainError) as err:
        services.reviews.save_answers(review_id, "C1", "manager", {"goals": 1})
    assert err.value.code is ErrorCode.ANSWERS_LOCKED


def test_submit_by_wrong_person_gives_permission_error(
    services: AppServices, clock: FixedClock
) -> None:
    review_id = drive(services, clock, "C1", "B1", Status.INITIATED)
    with pytest.raises(DomainError) as err:
        services.reviews.transition(review_id, "submit_self", "C1", {"answers": FULL_ANSWERS})
    assert err.value.code is ErrorCode.NOT_REVIEWEE
    assert services.reviews.get_answers(review_id, RespondentRole.SELF) == {}


def test_survey_version_is_stored(services: AppServices, clock: FixedClock) -> None:
    review_id = drive(services, clock, "C1", "B1", Status.INITIATED)
    progress = services.reviews.get_progress(review_id)
    assert progress is not None and progress.survey_version == services.surveys.current.version


# ---- meeting -------------------------------------------------------------------------


def test_meeting_local_time_is_stored_as_utc(services: AppServices, clock: FixedClock) -> None:
    review_id = drive(services, clock, "C1", "B1", Status.MANAGER_ASSESSMENT_SUBMITTED)
    services.reviews.transition(
        review_id, "schedule_meeting", "C1", {"meeting_at": datetime(2026, 10, 5, 14, 0)}
    )
    progress = services.reviews.get_progress(review_id)
    assert progress is not None
    assert progress.meeting_at == datetime(2026, 10, 5, 12, 0)  # Berlin is UTC+2 in October
    assert progress.meeting_set_by == "C1"


def test_meeting_invite_contains_calendar_details(services: AppServices, clock: FixedClock) -> None:
    review_id = drive(services, clock, "C1", "B1", Status.MANAGER_ASSESSMENT_SUBMITTED)
    services.reviews.transition(
        review_id, "schedule_meeting", "C1", {"meeting_at": datetime(2026, 10, 5, 14, 0)}
    )
    with services.sessions() as session:
        rows = Repositories.for_session(session).outbox.list()
    invites = [r for r in rows if r["type"] == "meeting_invite"]
    assert {r["recipient_email"] for r in invites} == {"carl@example.com", "berta@example.com"}
    assert "05.10.2026 14:00" in invites[0]["subject"]
    assert "DTSTART:20261005T120000Z" in invites[0]["payload_json"]


# ---- HR overrides --------------------------------------------------------------------


def test_hr_revert_clears_later_columns_and_unlocks_answers(
    services: AppServices, clock: FixedClock
) -> None:
    review_id = drive(services, clock, "C1", "B1", Status.MEETING_SCHEDULED)
    reviews = services.reviews

    reviews.transition(review_id, "hr_revert", "H1", {"reason": "Wrong date"})
    progress = reviews.get_progress(review_id)
    assert progress is not None and progress.meeting_at is None and progress.meeting_set_by is None
    assert derive_status(progress) is Status.MANAGER_ASSESSMENT_SUBMITTED

    reviews.transition(review_id, "hr_revert", "H1", {"reason": "Manager wants to redo"})
    assert status_of(services, review_id) is Status.SELF_ASSESSMENT_SUBMITTED
    reviews.save_answers(review_id, "C1", "manager", {"goals": 2})  # unlocked again
    assert reviews.get_answers(review_id, RespondentRole.SELF)["goals"].is_draft is False

    with services.sessions() as session:
        entries = Repositories.for_session(session).audit.list(review_id=review_id)
    reverts = [e for e in entries if e["action"] == "hr_revert"]
    assert {e["field_changed"] for e in reverts} == {"meeting_at", "manager_submitted_at"}
    assert {e["reason"] for e in reverts} == {"Wrong date", "Manager wants to redo"}
    assert all(e["new_value"] is None and e["old_value"] for e in reverts)


def test_hr_revert_from_completed_clears_acks_and_comment(
    services: AppServices, clock: FixedClock
) -> None:
    review_id = drive(services, clock, "C1", "B1", Status.COMPLETED)
    services.reviews.transition(review_id, "hr_revert", "H1", {"reason": "Not really discussed"})
    progress = services.reviews.get_progress(review_id)
    assert progress is not None
    assert (progress.reviewee_ack_at, progress.reviewer_ack_at, progress.completed_at) == (
        None,
        None,
        None,
    )
    assert progress.meeting_at is not None
    assert count(services, review_comments) == 0


def test_hr_revert_of_initiation_returns_to_not_started(
    services: AppServices, clock: FixedClock, cycle: ReviewCycle
) -> None:
    review_id = drive(services, clock, "C1", "B1", Status.INITIATED)
    services.reviews.save_answers(review_id, "B1", "self", {"goals": 3})
    result = services.reviews.transition(review_id, "hr_revert", "H1", {"reason": "Wrong person"})
    assert result.review_id is None and result.new_status is Status.NOT_STARTED
    assert services.reviews.get_progress_for(cycle.cycle_id, "B1") is None
    assert count(services, survey_answers) == 0
    with services.sessions() as session:  # the audit trail survives the deleted row
        entries = Repositories.for_session(session).audit.list(review_id=review_id)
    assert any(e["action"] == "hr_revert" for e in entries)
    # ...and the review can be initiated again.
    services.reviews.transition(None, "initiate", "C1", {"reviewee_id": "B1"})


def test_hr_advance_needs_reason_and_is_audited(services: AppServices, clock: FixedClock) -> None:
    review_id = drive(services, clock, "C1", "B1", Status.INITIATED)
    with pytest.raises(DomainError) as err:
        services.reviews.transition(review_id, "hr_advance", "H1", {"reason": " "})
    assert err.value.code is ErrorCode.REASON_REQUIRED
    with pytest.raises(DomainError) as err:
        services.reviews.transition(review_id, "hr_advance", "C1", {"reason": "x"})
    assert err.value.code is ErrorCode.HR_ONLY

    services.reviews.transition(review_id, "hr_advance", "H1", {"reason": "Submitted on paper"})
    assert status_of(services, review_id) is Status.SELF_ASSESSMENT_SUBMITTED
    with services.sessions() as session:
        entries = Repositories.for_session(session).audit.list(review_id=review_id)
    assert entries[0]["action"] == "hr_advance" and entries[0]["reason"] == "Submitted on paper"


def test_hr_can_advance_all_the_way_to_completed(services: AppServices, clock: FixedClock) -> None:
    review_id = drive(services, clock, "C1", "B1", Status.INITIATED)
    for _ in range(5):
        services.reviews.transition(review_id, "hr_advance", "H1", {"reason": "Offline review"})
    assert status_of(services, review_id) is Status.COMPLETED
    with pytest.raises(DomainError):
        services.reviews.transition(review_id, "hr_advance", "H1", {"reason": "again"})


# ---- acknowledgement -----------------------------------------------------------------


def test_auto_completion_on_double_acknowledgement(
    services: AppServices, clock: FixedClock
) -> None:
    review_id = drive(services, clock, "C1", "B1", Status.MEETING_SCHEDULED)
    progress = services.reviews.get_progress(review_id)
    assert progress is not None and progress.meeting_at is not None

    clock.set(progress.meeting_at - timedelta(days=1))
    with pytest.raises(DomainError) as err:
        services.reviews.transition(review_id, "acknowledge", "B1")
    assert err.value.code is ErrorCode.MEETING_NOT_YET_REACHED

    clock.set(progress.meeting_at + timedelta(hours=1))
    first = services.reviews.transition(review_id, "acknowledge", "C1")
    assert first.new_status is Status.ACKNOWLEDGED
    second = services.reviews.transition(review_id, "acknowledge", "B1", {"comment": "Fair"})
    assert second.new_status is Status.COMPLETED
    done = services.reviews.get_progress(review_id)
    assert done is not None and done.completed_at == clock.now()
    assert done.reviewer_ack_at and done.reviewee_ack_at
