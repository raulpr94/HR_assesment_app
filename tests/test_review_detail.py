"""ReviewDetailService: visibility of each side, per person and per step."""

from __future__ import annotations

from datetime import timedelta

import pytest

from core.errors import DomainError, ErrorCode
from core.models import RespondentRole, Status
from core.permissions import Viewer
from services.clock import FixedClock
from services.container import AppServices
from services.review_detail_service import HiddenReason, Relation
from tests.conftest import FULL_ANSWERS, drive


def viewer(services: AppServices, employee_id: str) -> Viewer:
    found = services.directory.viewer(employee_id)
    assert found is not None
    return found


def detail(services: AppServices, review_id: int, employee_id: str):  # type: ignore[no-untyped-def]
    return services.details.get(review_id, viewer(services, employee_id))


def test_relations_and_outsiders(services: AppServices, clock: FixedClock) -> None:
    review_id = drive(services, clock, "C1", "B1", Status.INITIATED)
    assert detail(services, review_id, "B1").relation is Relation.REVIEWEE
    assert detail(services, review_id, "C1").relation is Relation.REVIEWER
    assert detail(services, review_id, "H1").relation is Relation.HR
    for outsider in ("B2", "A1"):
        with pytest.raises(DomainError) as err:
            detail(services, review_id, outsider)
        assert err.value.code is ErrorCode.REVIEW_HIDDEN
    with pytest.raises(DomainError) as err:
        detail(services, 999, "H1")
    assert err.value.code is ErrorCode.REVIEW_NOT_FOUND


def test_reviewer_cannot_see_self_drafts(services: AppServices, clock: FixedClock) -> None:
    review_id = drive(services, clock, "C1", "B1", Status.INITIATED)
    services.reviews.save_answers(review_id, "B1", "self", {"goals": 2})

    mine = detail(services, review_id, "B1")
    assert mine.self_side.editable and mine.self_side.has_drafts
    assert mine.self_side.answers == {"goals": "2"}

    theirs = detail(services, review_id, "C1")
    assert not theirs.self_side.visible and theirs.self_side.answers == {}
    assert theirs.self_side.hidden_reason is HiddenReason.NOT_SUBMITTED
    assert not theirs.manager_side.editable  # waits for the self-assessment

    hr = detail(services, review_id, "H1")
    assert hr.self_side.visible and hr.self_side.answers == {"goals": "2"}
    assert not hr.self_side.editable


def test_after_self_submission(services: AppServices, clock: FixedClock) -> None:
    review_id = drive(services, clock, "C1", "B1", Status.SELF_ASSESSMENT_SUBMITTED)
    mine = detail(services, review_id, "B1")
    assert mine.self_side.submitted and not mine.self_side.editable
    reviewer = detail(services, review_id, "C1")
    assert reviewer.self_side.visible and reviewer.self_side.answers["goals"] == "4"
    assert reviewer.manager_side.editable


def test_manager_answers_hidden_until_meeting_date(
    services: AppServices, clock: FixedClock
) -> None:
    review_id = drive(services, clock, "C1", "B1", Status.MANAGER_ASSESSMENT_SUBMITTED)
    before = detail(services, review_id, "B1").manager_side
    assert not before.visible and before.answers == {}
    assert before.hidden_reason is HiddenReason.REVEAL_LATER and before.reveal_date is None

    services.reviews.transition(
        review_id,
        "schedule_meeting",
        "C1",
        {"meeting_at": (clock.now() + timedelta(days=3)).replace(hour=10, minute=0)},
    )
    scheduled = detail(services, review_id, "B1").manager_side
    assert not scheduled.visible and scheduled.reveal_date is not None

    progress = services.reviews.get_progress(review_id)
    assert progress is not None and progress.meeting_at is not None
    clock.set(progress.meeting_at - timedelta(hours=1))  # meeting day, before the meeting
    on_the_day = detail(services, review_id, "B1")
    assert on_the_day.manager_side.visible
    assert on_the_day.manager_side.answers == {k: str(v) for k, v in FULL_ANSWERS.items()}
    assert on_the_day.can_acknowledge


def test_skip_level_sees_progress_not_answers(services: AppServices, clock: FixedClock) -> None:
    drive(services, clock, "C1", "B1", Status.COMPLETED)
    review_id = drive(services, clock, "B1", "A1", Status.MANAGER_ASSESSMENT_SUBMITTED)
    seen = detail(services, review_id, "C1")
    assert seen.relation is Relation.OBSERVER
    for side in (seen.self_side, seen.manager_side):
        assert not side.visible and side.answers == {}
        assert side.hidden_reason is HiddenReason.PARTICIPANTS_ONLY
    assert not seen.can_acknowledge


def test_comments_are_listed(services: AppServices, clock: FixedClock) -> None:
    review_id = drive(services, clock, "C1", "B1", Status.COMPLETED)
    comments = detail(services, review_id, "C1").comments
    assert [(c.author, c.text) for c in comments] == [("Berta Lead", "Good talk")]


def test_viewable_list_per_person(services: AppServices, clock: FixedClock) -> None:
    cycle = services.cycles.get_open_cycle()
    assert cycle is not None
    drive(services, clock, "C1", "B1", Status.COMPLETED)
    drive(services, clock, "B1", "A1", Status.INITIATED)
    drive(services, clock, "C1", "B2", Status.INITIATED)

    def names(emp: str) -> list[tuple[str, str]]:
        items = services.details.viewable(cycle.cycle_id, viewer(services, emp))
        return [(i.reviewee, i.relation.value) for i in items]

    assert names("B1") == [("Berta Lead", "reviewee"), ("Anna Arbeit", "reviewer")]
    assert names("A1") == [("Anna Arbeit", "reviewee")]
    assert names("A2") == []
    assert [n for n, _ in names("C1")] == ["Berta Lead", "Bodo Lead", "Anna Arbeit"]
    assert len(names("H1")) == 3


def test_history_is_hr_only(services: AppServices, clock: FixedClock) -> None:
    review_id = drive(services, clock, "C1", "B1", Status.SELF_ASSESSMENT_SUBMITTED)
    entries = services.details.history(review_id, viewer(services, "H1"))
    assert {e["field_changed"] for e in entries} >= {"initiated_at", "self_submitted_at"}
    with pytest.raises(DomainError):
        services.details.history(review_id, viewer(services, "C1"))


def test_side_lookup(services: AppServices, clock: FixedClock) -> None:
    review_id = drive(services, clock, "C1", "B1", Status.INITIATED)
    d = detail(services, review_id, "B1")
    assert d.side(RespondentRole.SELF) is d.self_side
    assert d.side(RespondentRole.MANAGER) is d.manager_side
