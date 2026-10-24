"""Review detail page in the real Streamlit app (AppTest): drafts, submit, reviewer, ack."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from config.settings import PROJECT_ROOT, load_settings
from core.models import RespondentRole, Status
from core.workflow import derive_status
from services.container import AppServices, build_services
from tests.conftest import FULL_ANSWERS, SMALL_CSV

APP = str(PROJECT_ROOT / "app.py")


@pytest.fixture
def app_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[AppServices]:
    """B1's own review completed; A1 and A2 reviews started by B1."""
    monkeypatch.setenv("PERF_DB_PATH", str(tmp_path / "ui.db"))
    monkeypatch.setenv("NOTIFY_MODE", "dry_run")
    monkeypatch.setenv("ZAPIER_WEBHOOK_URL", "")
    services = build_services(
        load_settings(env_file=None, use_streamlit_secrets=False), ensure_schema=True
    )
    csv_path = tmp_path / "employees.csv"
    csv_path.write_text(SMALL_CSV, encoding="utf-8")
    assert services.imports.import_file(csv_path).ok
    services.cycles.open_cycle("UI cycle", actor_id="H1")
    reviews = services.reviews
    b1 = reviews.transition(None, "initiate", "C1", {"reviewee_id": "B1"}).review_id
    for _ in range(5):
        reviews.transition(b1, "hr_advance", "H1", {"reason": "set-up"})
    reviews.transition(None, "initiate", "B1", {"reviewee_id": "A1"})
    reviews.transition(None, "initiate", "B1", {"reviewee_id": "A2"})
    st.cache_resource.clear()
    st.cache_data.clear()
    yield services
    st.cache_resource.clear()
    st.cache_data.clear()
    services.engine.dispose()


def open_review_as(employee_id: str, *, via: str) -> AppTest:
    """Log in, then use a button on the Overview page to reach the review page."""
    at = AppTest.from_file(APP, default_timeout=30)
    at.session_state["view_as_id"] = employee_id
    at.run()
    button = next(b for b in at.button if b.label == via or b.key == via)
    button.click().run()
    assert not at.exception, at.exception
    assert "Review detail" in [t.value for t in at.title]
    return at


def review_id_of(services: AppServices, reviewee: str) -> int:
    progress = services.reviews.get_progress_for(1, reviewee)
    assert progress is not None and progress.review_id is not None
    return progress.review_id


def fill(at: AppTest, prefix: str, answers: dict) -> None:
    for qid, value in answers.items():
        if qid.endswith("__comment"):
            at.text_area(key=f"{prefix}_{qid.removesuffix('__comment')}_comment").input(value)
        elif isinstance(value, int):
            at.radio(key=f"{prefix}_{qid}").set_value(value)
        else:
            at.text_area(key=f"{prefix}_{qid}").input(value)


def press(at: AppTest, label: str) -> None:
    next(b for b in at.button if b.label == label).click().run()
    assert not at.exception, at.exception


def test_employee_saves_draft_then_submits(app_db: AppServices) -> None:
    review_id = review_id_of(app_db, "A1")
    at = open_review_as("A1", via="Complete your self-assessment")
    prefix = f"survey_{review_id}_self"

    fill(at, prefix, {"goals": 3, "strengths": "Curious"})
    press(at, "Save draft")
    drafts = app_db.reviews.get_answers(review_id, RespondentRole.SELF)
    assert drafts["goals"].value == "3" and drafts["goals"].is_draft

    press(at, "Submit…")  # incomplete: error, no dialog
    assert any("Please answer these" in e.value for e in at.error)
    assert derive_status(app_db.reviews.get_progress(review_id)) is Status.INITIATED

    fill(at, prefix, FULL_ANSWERS)
    press(at, "Submit…")
    press(at, "Submit self-assessment")  # inside the confirmation dialog
    progress = app_db.reviews.get_progress(review_id)
    assert derive_status(progress) is Status.SELF_ASSESSMENT_SUBMITTED
    answers = app_db.reviews.get_answers(review_id, RespondentRole.SELF)
    assert all(not a.is_draft for a in answers.values())
    # The page now shows the locked answers instead of a form.
    assert not any(b.label == "Save draft" for b in at.button)


def test_reviewer_sees_self_assessment_next_to_own_form(app_db: AppServices) -> None:
    review_id = review_id_of(app_db, "A1")
    app_db.reviews.transition(review_id, "submit_self", "A1", {"answers": FULL_ANSWERS})
    st.cache_data.clear()
    at = AppTest.from_file(APP, default_timeout=30)
    at.session_state["view_as_id"] = "B1"
    at.run()
    at.selectbox(key="open_team").set_value(review_id)
    at.button(key="open_btn_team").click().run()
    assert not at.exception, at.exception
    page_text = " ".join(m.value for m in at.markdown)
    assert "Lead the Q1 launch" in page_text  # A1's submitted answer, read-only
    assert any(b.label == "Save draft" for b in at.button)  # B1's own form


def test_reviewer_waits_for_self_assessment(app_db: AppServices) -> None:
    at = AppTest.from_file(APP, default_timeout=30)
    at.session_state["view_as_id"] = "B1"
    at.run()
    at.selectbox(key="open_team").set_value(review_id_of(app_db, "A2"))
    at.button(key="open_btn_team").click().run()
    assert any("hasn't submitted the self-assessment" in i.value for i in at.info)
    assert not any(b.label == "Save draft" for b in at.button)


def test_acknowledgement_with_comment(app_db: AppServices) -> None:
    review_id = review_id_of(app_db, "A2")
    app_db.reviews.transition(review_id, "submit_self", "A2", {"answers": FULL_ANSWERS})
    app_db.reviews.transition(review_id, "submit_manager", "B1", {"answers": FULL_ANSWERS})
    # HR records the meeting as "now", so today is the meeting day.
    app_db.reviews.transition(review_id, "hr_advance", "H1", {"reason": "met in person"})
    st.cache_data.clear()
    at = open_review_as("A2", via="Confirm the review was discussed")
    assert any("Both sides" in m.value for m in at.markdown)  # meeting day: both visible

    at.text_area(key=f"ack_comment_{review_id}").input("Fair and helpful")
    press(at, "Confirm")  # checkbox not ticked yet
    assert app_db.reviews.get_progress(review_id).reviewee_ack_at is None  # type: ignore[union-attr]

    at.checkbox(key=f"ack_box_{review_id}").check()
    press(at, "Confirm")
    progress = app_db.reviews.get_progress(review_id)
    assert progress is not None and progress.reviewee_ack_at is not None
    assert derive_status(progress) is Status.ACKNOWLEDGED
    assert any("Fair and helpful" in m.value for m in at.markdown)


def test_hr_sees_history(app_db: AppServices) -> None:
    at = AppTest.from_file(APP, default_timeout=30)
    at.session_state["view_as_id"] = "H1"
    at.run()
    at.selectbox(key="open_company").set_value(review_id_of(app_db, "B1"))
    at.button(key="open_btn_company").click().run()
    assert not at.exception, at.exception
    assert any("History" in e.label for e in at.expander)
