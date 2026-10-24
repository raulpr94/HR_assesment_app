"""Render the real Streamlit app (AppTest) as different people."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from config.settings import PROJECT_ROOT, load_settings
from core.models import Action, Level, Status
from core.workflow import derive_status
from services.container import AppServices, build_services
from tests.conftest import SMALL_CSV

APP = str(PROJECT_ROOT / "app.py")


@pytest.fixture
def demo_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[AppServices]:
    """A file database with reviews at different steps, using the real clock."""
    db = tmp_path / "ui.db"
    monkeypatch.setenv("PERF_DB_PATH", str(db))
    monkeypatch.setenv("NOTIFY_MODE", "dry_run")
    monkeypatch.setenv("ZAPIER_WEBHOOK_URL", "")
    settings = load_settings(env_file=None, use_streamlit_secrets=False)
    services = build_services(settings, ensure_schema=True)
    csv_path = tmp_path / "employees.csv"
    csv_path.write_text(SMALL_CSV, encoding="utf-8")
    assert services.imports.import_file(csv_path).ok
    services.cycles.open_cycle("UI cycle", actor_id="H1")
    reviews = services.reviews
    b1 = reviews.transition(None, "initiate", "C1", {"reviewee_id": "B1"}).review_id
    for _ in range(5):
        reviews.transition(b1, "hr_advance", "H1", {"reason": "set-up"})
    reviews.transition(None, "initiate", "B1", {"reviewee_id": "A1"})
    b2 = reviews.transition(None, "initiate", "C1", {"reviewee_id": "B2"}).review_id
    for _ in range(2):
        reviews.transition(b2, "hr_advance", "H1", {"reason": "set-up"})
    st.cache_resource.clear()
    st.cache_data.clear()
    yield services
    st.cache_resource.clear()
    st.cache_data.clear()
    services.engine.dispose()


def run_as(employee_id: str) -> AppTest:
    at = AppTest.from_file(APP, default_timeout=30)
    at.session_state["view_as_id"] = employee_id
    at.run()
    assert not at.exception, at.exception
    return at


def texts(elements: object) -> list[str]:
    return [e.value for e in elements]  # type: ignore[attr-defined]


def test_employee_sees_stepper_and_call_to_action(demo_db: AppServices) -> None:
    at = run_as("A1")
    assert texts(at.subheader) == ["My review"]
    labels = [b.label for b in at.button]
    assert "Complete your self-assessment" in labels
    next(b for b in at.button if b.label == "Complete your self-assessment").click().run()
    assert not at.exception
    assert "Review detail" in texts(at.title)


def test_employee_not_started_gets_guidance(demo_db: AppServices) -> None:
    at = run_as("A2")
    assert any("hasn't started your review" in i for i in texts(at.info))


def test_line_manager_sees_own_review_and_team(demo_db: AppServices) -> None:
    at = run_as("B1")
    assert texts(at.subheader) == ["My review", "My team"]
    assert any("complete" in s for s in texts(at.success))
    assert len(at.dataframe) == 1  # the writeback table


def test_department_manager_sees_team_only(demo_db: AppServices) -> None:
    at = run_as("C1")
    assert texts(at.subheader) == ["My team"]
    assert any("2 direct reports" in c for c in texts(at.caption))


def test_hr_sees_company_view_and_override_preview(demo_db: AppServices) -> None:
    at = run_as("H1")
    assert "All reviews (HR)" in texts(at.subheader)
    assert any("Showing 5 of 5" in c for c in texts(at.caption))
    assert any("moves the review" in m for m in texts(at.markdown))

    at.multiselect(key="f_level").set_value([Level.A])
    at.run()
    assert any("Showing 3 of 5" in c for c in texts(at.caption))


def test_hr_override_through_dialog(demo_db: AppServices) -> None:
    at = run_as("H1")
    review = demo_db.reviews.get_progress_for(1, "B2")
    assert review is not None
    at.selectbox(key="ovr_review").set_value(review.review_id)
    at.radio(key="ovr_direction").set_value(Action.HR_REVERT)
    at.text_input(key="ovr_reason").input("Assessment entered for the wrong person")
    at.run()
    at.button(key="ovr_apply").click().run()
    confirm = [b for b in at.button if b.label == "Move one step back"]
    assert confirm, [b.label for b in at.button]
    confirm[0].click().run()
    assert not at.exception
    progress = demo_db.reviews.get_progress(review.review_id)
    assert derive_status(progress) is Status.SELF_ASSESSMENT_SUBMITTED
