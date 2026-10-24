"""DashboardService figures and the suggested timeline."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

import pytest

from core.errors import DomainError, ErrorCode
from core.models import Action, Status
from core.permissions import Viewer
from core.timeline import suggested_timeline
from services.clock import FixedClock
from services.container import AppServices
from tests.conftest import CYCLE_DUE, drive


def viewer(services: AppServices, employee_id: str) -> Viewer:
    found = services.directory.viewer(employee_id)
    assert found is not None
    return found


def cycle_id(services: AppServices) -> int:
    cycle = services.cycles.get_open_cycle()
    assert cycle is not None
    return cycle.cycle_id


def test_timeline_is_ordered_and_inside_the_cycle() -> None:
    start, due = date(2026, 10, 1), date(2026, 11, 30)
    plan = suggested_timeline(start, due)
    assert [a for a, _ in plan] == [
        Action.INITIATE,
        Action.SUBMIT_SELF,
        Action.SUBMIT_MANAGER,
        Action.SCHEDULE_MEETING,
        Action.ACKNOWLEDGE,
    ]
    dates = [d for _, d in plan]
    assert dates == sorted(dates) and dates[0] >= start and dates[-1] == due
    assert suggested_timeline(start, start) == [(a, start) for a, _ in plan]


@pytest.mark.parametrize("employee_id", ["A1", "B1"])
def test_only_c_level_and_hr(services: AppServices, employee_id: str) -> None:
    with pytest.raises(DomainError) as err:
        services.dashboard.build(cycle_id(services), viewer(services, employee_id))
    assert err.value.code is ErrorCode.PAGE_FORBIDDEN


def test_counts_and_completion(services: AppServices, clock: FixedClock) -> None:
    drive(services, clock, "C1", "B1", Status.COMPLETED)
    drive(services, clock, "B1", "A1", Status.SELF_ASSESSMENT_SUBMITTED)
    data = services.dashboard.build(cycle_id(services), viewer(services, "C1"))
    assert not data.company_wide
    assert data.eligible == 5  # B1, B2, A1, A2, A3 are all in C1's tree
    assert data.status_counts[Status.COMPLETED] == 1
    assert data.status_counts[Status.SELF_ASSESSMENT_SUBMITTED] == 1
    assert data.status_counts[Status.NOT_STARTED] == 3
    assert sum(data.status_counts.values()) == 5
    assert data.completion_rate == pytest.approx(0.2)


def test_hr_sees_company_and_scope_excludes_c_level(services: AppServices) -> None:
    data = services.dashboard.build(cycle_id(services), viewer(services, "H1"))
    assert data.company_wide and data.eligible == 5


def test_team_drill_down(services: AppServices, clock: FixedClock) -> None:
    drive(services, clock, "C1", "B1", Status.COMPLETED)
    c1 = viewer(services, "C1")
    data = services.dashboard.build(cycle_id(services), c1)
    teams = {t.manager.employee_id: (t.size, t.started, t.completed) for t in data.teams}
    assert teams == {"C1": (2, 1, 1), "B1": (2, 0, 0), "B2": (1, 0, 0)}
    team_b1 = services.dashboard.build(cycle_id(services), c1, team_manager_id="B1")
    assert team_b1.eligible == 2 and team_b1.team is not None
    assert team_b1.team.employee_id == "B1"


def test_overdue_only_after_due_date(services: AppServices, clock: FixedClock) -> None:
    drive(services, clock, "C1", "B1", Status.COMPLETED)
    hr = viewer(services, "H1")
    before = services.dashboard.build(cycle_id(services), hr)
    assert before.overdue == [] and before.days_to_due > 0

    clock.set(datetime.combine(CYCLE_DUE + timedelta(days=3), time(12, 0)))
    after = services.dashboard.build(cycle_id(services), hr)
    assert after.days_to_due == -3
    assert {o.employee.employee_id for o in after.overdue} == {"B2", "A1", "A2", "A3"}
    assert all(o.days_overdue == 3 for o in after.overdue)
    teams = {t.manager.employee_id: t.overdue for t in after.teams}
    assert teams == {"C1": 1, "B1": 2, "B2": 1}


def test_rating_averages_hide_small_groups(services: AppServices, clock: FixedClock) -> None:
    hr = viewer(services, "H1")
    drive(services, clock, "C1", "B1", Status.COMPLETED)
    drive(services, clock, "C1", "B2", Status.MANAGER_ASSESSMENT_SUBMITTED)
    data = services.dashboard.build(cycle_id(services), hr)
    overall = next(r for r in data.ratings if r.question_id == "overall")
    assert (overall.self_n, overall.manager_n) == (2, 2)
    assert overall.self_avg is None and overall.manager_avg is None  # 2 < 3

    services.dashboard.min_group_size = 2
    data = services.dashboard.build(cycle_id(services), hr)
    overall = next(r for r in data.ratings if r.question_id == "overall")
    assert overall.self_avg == 4.0 and overall.manager_avg == 4.0
    assert {r.question_id for r in data.ratings} == {
        "goals",
        "quality",
        "collaboration",
        "communication",
        "initiative",
        "overall",
    }


def test_drafts_do_not_count(services: AppServices, clock: FixedClock) -> None:
    services.dashboard.min_group_size = 1
    review_id = drive(services, clock, "C1", "B1", Status.INITIATED)
    services.reviews.save_answers(review_id, "B1", "self", {"overall": 1})
    data = services.dashboard.build(cycle_id(services), viewer(services, "H1"))
    assert all(r.self_n == 0 for r in data.ratings)
