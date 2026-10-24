"""Permissions per role: pages, review visibility, answer visibility and editing."""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from core.models import Page, RespondentRole, RevealPolicy, ReviewProgress
from core.permissions import (
    Viewer,
    can_access_page,
    can_edit_answers,
    can_override,
    can_see_answers,
    can_view_review,
    visible_pages,
)
from services.container import AppServices

TZ = ZoneInfo("Europe/Berlin")
T0 = datetime(2026, 10, 1, 8, 0)
MEETING = datetime(2026, 10, 10, 12, 0)  # 14:00 Berlin


@pytest.fixture
def viewers(services: AppServices) -> dict[str, Viewer]:
    result = {}
    for emp_id in ("H1", "C1", "B1", "B2", "A1", "A3"):
        viewer = services.directory.viewer(emp_id)
        assert viewer is not None
        result[emp_id] = viewer
    return result


def progress(**filled: object) -> ReviewProgress:
    return ReviewProgress(review_id=1, cycle_id=1, reviewer_id="B1", reviewee_id="A1", **filled)


STAGES = {
    "initiated": progress(initiated_at=T0),
    "self": progress(initiated_at=T0, self_submitted_at=T0),
    "manager": progress(initiated_at=T0, self_submitted_at=T0, manager_submitted_at=T0),
    "meeting": progress(
        initiated_at=T0, self_submitted_at=T0, manager_submitted_at=T0, meeting_at=MEETING
    ),
    "completed": progress(
        initiated_at=T0,
        self_submitted_at=T0,
        manager_submitted_at=T0,
        meeting_at=MEETING,
        reviewee_ack_at=MEETING,
        reviewer_ack_at=MEETING,
        completed_at=MEETING,
    ),
}


def test_reporting_lines(viewers: dict[str, Viewer]) -> None:
    assert viewers["C1"].direct_report_ids == {"B1", "B2"}
    assert viewers["C1"].subtree_ids == {"B1", "B2", "A1", "A2", "A3"}
    assert viewers["B1"].subtree_ids == {"A1", "A2"}
    assert viewers["A1"].subtree_ids == frozenset() and not viewers["A1"].is_manager


@pytest.mark.parametrize(
    ("emp_id", "pages"),
    [
        ("A1", [Page.OVERVIEW, Page.REVIEW, Page.GUIDE]),
        ("B1", [Page.OVERVIEW, Page.REVIEW, Page.GUIDE]),
        ("C1", [Page.OVERVIEW, Page.REVIEW, Page.GUIDE, Page.DASHBOARD]),
        ("H1", [Page.OVERVIEW, Page.REVIEW, Page.GUIDE, Page.DASHBOARD, Page.ADMIN]),
    ],
)
def test_visible_pages_per_role(viewers: dict[str, Viewer], emp_id: str, pages: list[Page]) -> None:
    viewer = viewers[emp_id]
    assert visible_pages(viewer) == pages
    for page in Page:  # direct access follows the same rule as navigation
        assert can_access_page(viewer, page) is (page in pages)


def test_only_hr_can_override(viewers: dict[str, Viewer]) -> None:
    assert [e for e, v in viewers.items() if can_override(v)] == ["H1"]


@pytest.mark.parametrize(
    ("emp_id", "allowed"),
    [("H1", True), ("C1", True), ("B1", True), ("A1", True), ("B2", False), ("A3", False)],
)
def test_review_visibility_follows_reporting_line(
    viewers: dict[str, Viewer], emp_id: str, allowed: bool
) -> None:
    # Review B1 → A1: HR, both parties and the skip-level C1 may see it; peers may not.
    assert can_view_review(viewers[emp_id], "B1", "A1") is allowed


@pytest.mark.parametrize(
    ("stage", "who", "side", "now", "expected"),
    [
        # reviewee always sees own answers
        ("initiated", "A1", RespondentRole.SELF, T0, True),
        # reviewer sees self-assessment only once submitted
        ("initiated", "B1", RespondentRole.SELF, T0, False),
        ("self", "B1", RespondentRole.SELF, T0, True),
        # reviewer always sees own (manager) side
        ("self", "B1", RespondentRole.MANAGER, T0, True),
        # reviewee sees manager side only from the meeting date
        ("manager", "A1", RespondentRole.MANAGER, T0, False),
        ("meeting", "A1", RespondentRole.MANAGER, MEETING - timedelta(days=1), False),
        ("meeting", "A1", RespondentRole.MANAGER, MEETING - timedelta(hours=3), True),
        ("completed", "A1", RespondentRole.MANAGER, MEETING + timedelta(days=1), True),
        # skip-level manager and peers see no answers; HR sees everything
        ("completed", "C1", RespondentRole.SELF, MEETING, False),
        ("completed", "C1", RespondentRole.MANAGER, MEETING, False),
        ("completed", "B2", RespondentRole.SELF, MEETING, False),
        ("initiated", "H1", RespondentRole.SELF, T0, True),
        ("initiated", "H1", RespondentRole.MANAGER, T0, True),
    ],
)
def test_answer_visibility(
    viewers: dict[str, Viewer],
    stage: str,
    who: str,
    side: RespondentRole,
    now: datetime,
    expected: bool,
) -> None:
    assert can_see_answers(viewers[who], STAGES[stage], side, now, TZ) is expected


def test_reveal_policies(viewers: dict[str, Viewer]) -> None:
    a1, meeting = viewers["A1"], STAGES["meeting"]
    same_day_before = MEETING - timedelta(hours=3)
    side = RespondentRole.MANAGER
    assert can_see_answers(a1, meeting, side, same_day_before, TZ, RevealPolicy.MEETING_DATE)
    assert not can_see_answers(a1, meeting, side, same_day_before, TZ, RevealPolicy.MEETING_TIME)
    assert can_see_answers(a1, meeting, side, MEETING, TZ, RevealPolicy.MEETING_TIME)
    assert not can_see_answers(a1, meeting, side, MEETING, TZ, RevealPolicy.COMPLETED)
    assert can_see_answers(a1, STAGES["completed"], side, MEETING, TZ, RevealPolicy.COMPLETED)


@pytest.mark.parametrize(
    ("stage", "who", "side", "expected"),
    [
        ("initiated", "A1", RespondentRole.SELF, True),
        ("self", "A1", RespondentRole.SELF, False),  # locked after submission
        ("initiated", "B1", RespondentRole.MANAGER, False),  # waits for self-assessment
        ("self", "B1", RespondentRole.MANAGER, True),
        ("manager", "B1", RespondentRole.MANAGER, False),  # locked after submission
        ("initiated", "B1", RespondentRole.SELF, False),
        ("initiated", "H1", RespondentRole.SELF, False),
    ],
)
def test_answer_editing(
    viewers: dict[str, Viewer], stage: str, who: str, side: RespondentRole, expected: bool
) -> None:
    assert can_edit_answers(viewers[who], STAGES[stage], side) is expected
