"""Who may see and edit what. Pure functions over a ``Viewer``."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, tzinfo

from core.models import (
    Action,
    Employee,
    Level,
    Page,
    RespondentRole,
    RevealPolicy,
    ReviewProgress,
    Status,
)
from core.timeutil import local_date
from core.workflow import derive_status


@dataclass(frozen=True)
class Viewer:
    """The current user plus their reporting line."""

    employee: Employee
    direct_report_ids: frozenset[str] = frozenset()
    subtree_ids: frozenset[str] = frozenset()  # everyone below them, transitively

    @property
    def employee_id(self) -> str:
        return self.employee.employee_id

    @property
    def level(self) -> Level:
        return self.employee.level

    @property
    def is_hr(self) -> bool:
        return self.employee.is_hr_admin

    @property
    def is_manager(self) -> bool:
        return bool(self.direct_report_ids)


# ---- Pages ---------------------------------------------------------------------------


def can_access_page(viewer: Viewer, page: Page) -> bool:
    if page in (Page.OVERVIEW, Page.REVIEW, Page.GUIDE):
        return True
    if page is Page.DASHBOARD:
        return viewer.is_hr or viewer.level is Level.C
    if page is Page.ADMIN:
        return viewer.is_hr
    return False


def visible_pages(viewer: Viewer) -> list[Page]:
    return [page for page in Page if can_access_page(viewer, page)]


# ---- Reviews -------------------------------------------------------------------------


def can_view_review(viewer: Viewer, reviewer_id: str, reviewee_id: str) -> bool:
    """HR, the two parties, or anyone above the reviewee in the reporting line.

    Assumption: skip-level managers see progress but not answers (see can_see_answers).
    """
    if viewer.is_hr:
        return True
    if viewer.employee_id in (reviewer_id, reviewee_id):
        return True
    return reviewee_id in viewer.subtree_ids


def can_see_answers(
    viewer: Viewer,
    progress: ReviewProgress,
    side: RespondentRole,
    now: datetime,
    tz: tzinfo,
    policy: RevealPolicy = RevealPolicy.MEETING_DATE,
) -> bool:
    """Visibility of one side of the survey (self or manager answers)."""
    if viewer.is_hr:
        return True
    is_reviewee = viewer.employee_id == progress.reviewee_id
    is_reviewer = viewer.employee_id == progress.reviewer_id
    if side is RespondentRole.SELF:
        if is_reviewee:
            return True
        # Reviewer sees the self-assessment only after it was submitted.
        return is_reviewer and progress.self_submitted_at is not None
    # Manager side
    if is_reviewer:
        return True
    if is_reviewee:
        return manager_answers_revealed(progress, now, tz, policy)
    return False


def manager_answers_revealed(
    progress: ReviewProgress, now: datetime, tz: tzinfo, policy: RevealPolicy
) -> bool:
    if progress.manager_submitted_at is None:
        return False
    if policy is RevealPolicy.COMPLETED:
        return progress.completed_at is not None
    if progress.meeting_at is None:
        return False
    if policy is RevealPolicy.MEETING_TIME:
        return now >= progress.meeting_at
    return local_date(now, tz) >= local_date(progress.meeting_at, tz)


def can_edit_answers(viewer: Viewer, progress: ReviewProgress, side: RespondentRole) -> bool:
    """Answers are editable only by their owner, only at that owner's step."""
    status = derive_status(progress)
    if side is RespondentRole.SELF:
        return viewer.employee_id == progress.reviewee_id and status is Status.INITIATED
    return viewer.employee_id == progress.reviewer_id and status is Status.SELF_ASSESSMENT_SUBMITTED


def can_override(viewer: Viewer) -> bool:
    return viewer.is_hr


def writeback_actions(
    viewer: Viewer,
    reviewer_id: str,
    reviewer_level: Level,
    reviewer_own_status: Status,
    progress: ReviewProgress | None,
    now: datetime,
) -> frozenset[Action]:
    """Which writeback-table cells this viewer may edit for one row.

    Mirrors the workflow guards so the table only invites possible edits;
    ``ReviewService.transition`` still checks everything again on save.
    """
    if not (viewer.is_hr or viewer.employee_id == reviewer_id):
        return frozenset()
    status = derive_status(progress)
    actions: set[Action] = set()
    unlocked = reviewer_level is not Level.B or reviewer_own_status is Status.COMPLETED
    if status is Status.NOT_STARTED and unlocked:
        actions.add(Action.INITIATE)
    if status is Status.MANAGER_ASSESSMENT_SUBMITTED or (
        status is Status.MEETING_SCHEDULED
        and progress is not None
        and progress.meeting_at is not None
        and now < progress.meeting_at
    ):
        actions.add(Action.SCHEDULE_MEETING)
    return frozenset(actions)
