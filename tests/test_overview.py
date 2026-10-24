"""Overview logic: next steps, editable cells, rows, filters, previews and table diffs."""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from core.models import Action, Level, ReviewProgress, Status
from core.next_step import NextKind, next_step
from core.permissions import Viewer, writeback_actions
from db.repositories import Repositories
from services.clock import FixedClock
from services.container import AppServices
from tests.conftest import drive, emp
from ui.components.next_step import my_guidance, table_next_text
from ui.components.progress_stepper import step_states
from ui.components.writeback_table import build_frame, detect_changes

TZ = ZoneInfo("Europe/Berlin")
NOW = datetime(2026, 10, 10, 8, 0)
T = NOW - timedelta(days=1)


def prog(status: Status, meeting: datetime | None = None, acked: str = "") -> ReviewProgress:
    values: dict[str, object] = {}
    order = [
        ("initiated_at", Status.INITIATED),
        ("self_submitted_at", Status.SELF_ASSESSMENT_SUBMITTED),
        ("manager_submitted_at", Status.MANAGER_ASSESSMENT_SUBMITTED),
        ("meeting_at", Status.MEETING_SCHEDULED),
    ]
    for column, step in order:
        values[column] = meeting if column == "meeting_at" else T
        if step is status:
            break
    if "e" in acked:
        values["reviewee_ack_at"] = NOW
    if "r" in acked:
        values["reviewer_ack_at"] = NOW
    if acked == "er":
        values["completed_at"] = NOW
    return ReviewProgress(1, 1, "B1", "A1", **values)  # type: ignore[arg-type]


# ---- core.next_step ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("progress", "viewer", "kind", "action", "waiting"),
    [
        (None, "B1", NextKind.ACT, Action.INITIATE, None),
        (None, "A1", NextKind.WAIT, Action.INITIATE, "reviewer"),
        (prog(Status.INITIATED), "A1", NextKind.ACT, Action.SUBMIT_SELF, None),
        (prog(Status.INITIATED), "B1", NextKind.WAIT, Action.SUBMIT_SELF, "reviewee"),
        (prog(Status.SELF_ASSESSMENT_SUBMITTED), "B1", NextKind.ACT, Action.SUBMIT_MANAGER, None),
        (
            prog(Status.MANAGER_ASSESSMENT_SUBMITTED),
            "A1",
            NextKind.WAIT,
            Action.SCHEDULE_MEETING,
            "reviewer",
        ),
        (
            prog(Status.MEETING_SCHEDULED, NOW + timedelta(days=2)),
            "A1",
            NextKind.MEETING,
            Action.ACKNOWLEDGE,
            None,
        ),
        (prog(Status.MEETING_SCHEDULED, NOW), "A1", NextKind.ACT, Action.ACKNOWLEDGE, None),
        (prog(Status.MEETING_SCHEDULED, NOW), "H1", NextKind.WAIT, Action.ACKNOWLEDGE, "either"),
        (
            prog(Status.MEETING_SCHEDULED, NOW, "e"),
            "A1",
            NextKind.WAIT,
            Action.ACKNOWLEDGE,
            "reviewer",
        ),
        (prog(Status.MEETING_SCHEDULED, NOW, "e"), "B1", NextKind.ACT, Action.ACKNOWLEDGE, None),
        (prog(Status.MEETING_SCHEDULED, NOW, "er"), "A1", NextKind.DONE, None, None),
    ],
)
def test_next_step(
    progress: ReviewProgress | None,
    viewer: str,
    kind: NextKind,
    action: Action | None,
    waiting: str | None,
) -> None:
    step = next_step(viewer, "B1", "A1", progress, NOW, TZ)
    assert step.kind is kind and step.action is action
    assert (step.waiting_on.value if step.waiting_on else None) == waiting


# ---- core.permissions.writeback_actions ---------------------------------------------

B1 = Viewer(emp("B1", Level.B, "C1"), frozenset({"A1"}), frozenset({"A1"}))
HR = Viewer(emp("H1", Level.C, is_hr_admin=True))
OTHER = Viewer(emp("B2", Level.B, "C1"))


@pytest.mark.parametrize(
    ("viewer", "own", "progress", "expected"),
    [
        (B1, Status.COMPLETED, None, {Action.INITIATE}),
        (B1, Status.MEETING_SCHEDULED, None, set()),  # unlock rule
        (HR, Status.COMPLETED, None, {Action.INITIATE}),
        (OTHER, Status.COMPLETED, None, set()),  # not their report
        (B1, Status.COMPLETED, prog(Status.INITIATED), set()),
        (
            B1,
            Status.COMPLETED,
            prog(Status.MANAGER_ASSESSMENT_SUBMITTED),
            {Action.SCHEDULE_MEETING},
        ),
        (
            B1,
            Status.COMPLETED,
            prog(Status.MEETING_SCHEDULED, NOW + timedelta(hours=1)),
            {Action.SCHEDULE_MEETING},
        ),  # can still reschedule
        (B1, Status.COMPLETED, prog(Status.MEETING_SCHEDULED, NOW - timedelta(hours=1)), set()),
    ],
)
def test_writeback_actions(
    viewer: Viewer, own: Status, progress: ReviewProgress | None, expected: set[Action]
) -> None:
    assert writeback_actions(viewer, "B1", Level.B, own, progress, NOW) == expected


# ---- services.overview_service ------------------------------------------------------


def viewer_for(services: AppServices, employee_id: str) -> Viewer:
    found = services.directory.viewer(employee_id)
    assert found is not None
    return found


def test_team_rows_join_without_inserting(services: AppServices, clock: FixedClock) -> None:
    cycle = services.cycles.get_open_cycle()
    assert cycle is not None
    drive(services, clock, "C1", "B1", Status.INITIATED)
    rows = services.overview.team_rows(cycle.cycle_id, viewer_for(services, "C1"))
    assert [(r.employee.employee_id, r.status) for r in rows] == [
        ("B1", Status.INITIATED),
        ("B2", Status.NOT_STARTED),
    ]
    assert rows[0].actions == frozenset() and rows[1].actions == {Action.INITIATE}
    with services.sessions() as session:
        assert Repositories.for_session(session).progress.count() == 1


def test_b_manager_team_locked_until_own_review_completed(
    services: AppServices, clock: FixedClock
) -> None:
    cycle = services.cycles.get_open_cycle()
    assert cycle is not None
    b1 = viewer_for(services, "B1")
    assert services.overview.team_locked(cycle.cycle_id, b1)
    assert all(not r.actions for r in services.overview.team_rows(cycle.cycle_id, b1))
    drive(services, clock, "C1", "B1", Status.COMPLETED)
    assert not services.overview.team_locked(cycle.cycle_id, b1)
    assert all(
        r.actions == {Action.INITIATE} for r in services.overview.team_rows(cycle.cycle_id, b1)
    )


def test_my_review_only_for_reviewable_levels(services: AppServices) -> None:
    cycle = services.cycles.get_open_cycle()
    assert cycle is not None
    assert services.overview.my_review(cycle.cycle_id, viewer_for(services, "C1")) is None
    mine = services.overview.my_review(cycle.cycle_id, viewer_for(services, "A1"))
    assert mine is not None and mine.status is Status.NOT_STARTED


def test_company_rows_are_hr_only_and_filterable(services: AppServices, clock: FixedClock) -> None:
    from core.errors import DomainError

    cycle = services.cycles.get_open_cycle()
    assert cycle is not None
    with pytest.raises(DomainError):
        services.overview.company_rows(cycle.cycle_id, viewer_for(services, "C1"))
    drive(services, clock, "C1", "B2", Status.SELF_ASSESSMENT_SUBMITTED)
    rows = services.overview.company_rows(cycle.cycle_id, viewer_for(services, "H1"))
    assert {r.employee.employee_id for r in rows} == {"B1", "B2", "A1", "A2", "A3"}
    flt = services.overview.filter_rows
    assert {r.employee.employee_id for r in flt(rows, levels=[Level.B])} == {"B1", "B2"}
    assert [
        r.employee.employee_id for r in flt(rows, statuses=[Status.SELF_ASSESSMENT_SUBMITTED])
    ] == ["B2"]
    assert {r.employee.employee_id for r in flt(rows, manager_ids=["B1"])} == {"A1", "A2"}
    assert flt(rows, departments=["Nope"]) == []
    assert len(flt(rows)) == len(rows)


def test_preview_writes_nothing(services: AppServices, clock: FixedClock) -> None:
    review_id = drive(services, clock, "C1", "B1", Status.MEETING_SCHEDULED)
    before = services.reviews.get_progress(review_id)
    outcome = services.reviews.preview(review_id, Action.HR_REVERT, "H1", {"reason": "x"})
    assert outcome.new_status is Status.MANAGER_ASSESSMENT_SUBMITTED
    assert outcome.changes["meeting_at"] is None
    assert services.reviews.get_progress(review_id) == before


# ---- UI helpers (pure parts) --------------------------------------------------------


def test_stepper_states() -> None:
    assert [s for _, s in step_states(Status.NOT_STARTED)] == ["current"] + ["todo"] * 5
    assert [s for _, s in step_states(Status.SELF_ASSESSMENT_SUBMITTED)] == [
        "done",
        "done",
        "current",
        "todo",
        "todo",
        "todo",
    ]
    # One confirmation in: the confirmation step is still current.
    assert [s for _, s in step_states(Status.ACKNOWLEDGED)][-2:] == ["current", "todo"]
    assert [s for _, s in step_states(Status.COMPLETED)] == ["done"] * 6


def test_guidance_and_next_text(services: AppServices, clock: FixedClock) -> None:
    cycle = services.cycles.get_open_cycle()
    assert cycle is not None
    drive(services, clock, "C1", "B1", Status.INITIATED)
    tz = services.settings.tz
    mine = services.overview.my_review(cycle.cycle_id, viewer_for(services, "B1"))
    assert mine is not None
    guidance = my_guidance(mine, tz)
    assert guidance.kind == "act" and guidance.action is Action.SUBMIT_SELF
    team = services.overview.team_rows(cycle.cycle_id, viewer_for(services, "C1"))
    assert table_next_text(team[0], "C1", tz) == "Employee: self-assessment"
    assert table_next_text(team[1], "C1", tz) == "You: tick Initiate"
    b1_team = services.overview.team_rows(cycle.cycle_id, viewer_for(services, "B1"))
    assert "locked" in table_next_text(b1_team[0], "B1", tz)


def test_detect_changes(services: AppServices, clock: FixedClock) -> None:
    cycle = services.cycles.get_open_cycle()
    assert cycle is not None
    drive(services, clock, "C1", "B1", Status.MANAGER_ASSESSMENT_SUBMITTED)
    tz = services.settings.tz
    rows = services.overview.team_rows(cycle.cycle_id, viewer_for(services, "C1"))
    original = build_frame(rows, "C1", tz)
    assert list(original.index) == ["B1", "B2"]
    assert original.at["B1", "initiate"] and not original.at["B2", "initiate"]

    edited = original.copy()
    edited.at["B2", "initiate"] = True  # allowed: start B2's review
    edited.at["B1", "meeting"] = datetime(2026, 10, 20, 14, 0)  # allowed: schedule B1
    pending, rejected = detect_changes(rows, original, edited)
    assert [(c.row.employee.employee_id, c.action) for c in pending] == [
        ("B1", Action.SCHEDULE_MEETING),
        ("B2", Action.INITIATE),
    ]
    assert pending[0].meeting_at == datetime(2026, 10, 20, 14, 0) and rejected == []
    assert "20.10.2026 14:00" in pending[0].describe()

    edited = original.copy()
    edited.at["B1", "initiate"] = False  # can't un-initiate
    edited.at["B2", "meeting"] = datetime(2026, 10, 20, 14, 0)  # B2 not even started
    pending, rejected = detect_changes(rows, original, edited)
    assert pending == [] and len(rejected) == 2

    assert detect_changes(rows, original, original.copy()) == ([], [])


def test_empty_frame_builds() -> None:
    frame = build_frame([], "C1", TZ, company=True)
    assert frame.empty and "manager" in frame.columns
