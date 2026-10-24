"""Shared fixtures: in-memory SQLite, a fixed clock and a small hierarchy.

Hierarchy:  H1 (HR, C) → C1 (C) → B1, B2 (B);  B1 → A1, A2;  B2 → A3
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

from config.settings import Settings, load_settings
from core.models import Employee, Level, ReviewCycle, Status
from db.engine import make_engine
from services.clock import FixedClock
from services.container import AppServices, build_services

SMALL_CSV = """employee_id,name,email,level,role_title,department,manager_id,is_hr_admin
H1,Hilde Hartmann,hilde@example.com,C,HR Director,People,,true
C1,Carl Chef,carl@example.com,C,Head of Sales,Sales,H1,false
B1,Berta Lead,berta@example.com,B,Team Lead,Sales,C1,false
B2,Bodo Lead,bodo@example.com,B,Team Lead,Sales,C1,false
A1,Anna Arbeit,anna@example.com,A,Specialist,Sales,B1,false
A2,Arne Arbeit,arne@example.com,A,Specialist,Sales,B1,false
A3,Ali Arbeit,ali@example.com,A,Specialist,Sales,B2,false
"""

START = datetime(2026, 10, 1, 8, 0)  # naive UTC = 10:00 in Berlin (CEST)
CYCLE_START = date(2026, 10, 1)
CYCLE_DUE = date(2026, 11, 30)

FULL_ANSWERS = {
    "goals": 4,
    "goals__comment": "Hit most targets",
    "quality": 4,
    "collaboration": 5,
    "communication": 3,
    "initiative": 4,
    "strengths": "Reliable",
    "development": "Delegation",
    "next_goals": "Lead the Q1 launch",
    "overall": 4,
}


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return load_settings(
        {
            "PERF_DB_PATH": str(tmp_path / "test.db"),
            "NOTIFY_MODE": "dry_run",
            "ZAPIER_WEBHOOK_URL": "",
            "APP_TIMEZONE": "Europe/Berlin",
            "REVEAL_MANAGER_ANSWERS": "meeting_date",
        },
        env_file=None,
        use_streamlit_secrets=False,
    )


@pytest.fixture
def clock() -> FixedClock:
    return FixedClock(START)


@pytest.fixture
def bare_services(settings: Settings, clock: FixedClock) -> AppServices:
    """Schema only: no employees, no cycle."""
    return build_services(
        settings, engine=make_engine("sqlite://"), clock=clock, ensure_schema=True
    )


@pytest.fixture
def services(bare_services: AppServices, tmp_path: Path) -> AppServices:
    """Small hierarchy imported and one open cycle."""
    csv_path = tmp_path / "employees.csv"
    csv_path.write_text(SMALL_CSV, encoding="utf-8")
    report = bare_services.imports.import_file(csv_path)
    assert report.ok, report.issues
    bare_services.cycles.open_cycle("2026 Annual", CYCLE_START, CYCLE_DUE, actor_id="H1")
    return bare_services


@pytest.fixture
def cycle(services: AppServices) -> ReviewCycle:
    current = services.cycles.get_open_cycle()
    assert current is not None
    return current


def drive(
    services: AppServices,
    clock: FixedClock,
    reviewer: str,
    reviewee: str,
    target: Status,
) -> int:
    """Run a review through the real service up to ``target``; returns review_id."""
    reviews = services.reviews
    result = reviews.transition(None, "initiate", reviewer, {"reviewee_id": reviewee})
    review_id = result.review_id
    assert review_id is not None
    steps: list[tuple[Status, str, str, dict]] = [
        (Status.SELF_ASSESSMENT_SUBMITTED, "submit_self", reviewee, {"answers": FULL_ANSWERS}),
        (
            Status.MANAGER_ASSESSMENT_SUBMITTED,
            "submit_manager",
            reviewer,
            {"answers": FULL_ANSWERS},
        ),
        (Status.MEETING_SCHEDULED, "schedule_meeting", reviewer, {"meeting_at": None}),
        (Status.ACKNOWLEDGED, "acknowledge", reviewee, {"comment": "Good talk"}),
        (Status.COMPLETED, "acknowledge", reviewer, {}),
    ]
    if target is Status.INITIATED:
        return review_id
    for status, action, actor, payload in steps:
        if action == "schedule_meeting":
            # Naive datetimes are read as local (Berlin) time: tomorrow 12:00 local.
            payload = {"meeting_at": clock.now().replace(hour=12, minute=0) + _ONE_DAY}
        if action == "acknowledge" and status is Status.ACKNOWLEDGED:
            progress = reviews.get_progress(review_id)
            assert progress is not None and progress.meeting_at is not None
            clock.set(progress.meeting_at + _ONE_HOUR)
        reviews.transition(review_id, action, actor, payload)
        if status is target:
            break
    return review_id


_ONE_DAY = timedelta(days=1)
_ONE_HOUR = timedelta(hours=1)


def emp(employee_id: str, level: Level, manager_id: str | None = None, **kw: object) -> Employee:
    return Employee(
        employee_id=employee_id,
        name=kw.pop("name", employee_id),  # type: ignore[arg-type]
        email=f"{employee_id.lower()}@example.com",
        level=level,
        manager_id=manager_id,
        **kw,  # type: ignore[arg-type]
    )
