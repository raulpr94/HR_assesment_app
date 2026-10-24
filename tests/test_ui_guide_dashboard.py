"""Process guide and Dashboard pages rendered in Streamlit (AppTest)."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from config.settings import load_settings
from services.container import AppServices, build_services
from tests.conftest import FULL_ANSWERS, SMALL_CSV


@pytest.fixture
def app_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[AppServices]:
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
    reviews.transition(b1, "submit_self", "B1", {"answers": FULL_ANSWERS})
    st.cache_resource.clear()
    st.cache_data.clear()
    yield services
    st.cache_resource.clear()
    st.cache_data.clear()
    services.engine.dispose()


def _page_script(page_name: str, employee_id: str) -> None:
    import importlib

    from ui.state import get_services, set_viewer

    set_viewer(get_services().directory.viewer(employee_id))
    importlib.import_module(f"ui.pages.{page_name}").render()


def render(page: str, employee_id: str) -> AppTest:
    at = AppTest.from_function(_page_script, args=(page, employee_id), default_timeout=30)
    at.run()
    assert not at.exception, at.exception
    return at


def headings(at: AppTest) -> list[str]:
    return [h.value.split(": ", 1)[-1] for h in at.subheader]


@pytest.mark.parametrize(
    ("employee_id", "expected", "absent"),
    [
        ("A1", ["Your own review"], ["Reviewing your team", "Suggested timeline", "HR tasks"]),
        ("B1", ["Your own review", "Reviewing your team"], ["Suggested timeline", "HR tasks"]),
        ("C1", ["Reviewing your team", "Suggested timeline"], ["Your own review", "HR tasks"]),
        ("H1", ["Reviewing your team", "Suggested timeline", "HR tasks"], ["Your own review"]),
    ],
)
def test_guide_is_tailored(
    app_db: AppServices, employee_id: str, expected: list[str], absent: list[str]
) -> None:
    found = headings(render("guide", employee_id))
    assert "The six steps" in found and "Who sees what" in found
    for heading in expected:
        assert heading in found
    for heading in absent:
        assert heading not in found


def test_line_manager_sees_unlock_rule(app_db: AppServices) -> None:
    at = render("guide", "B1")
    assert any("only once your own review is completed" in i.value for i in at.info)


@pytest.mark.parametrize("employee_id", ["C1", "H1"])
def test_dashboard_renders(app_db: AppServices, employee_id: str) -> None:
    at = render("dashboard", employee_id)
    assert at.metric and at.metric[0].label == "Completion"
    assert "Reviews per status" in headings(at)
    assert "Teams" in headings(at)


def test_dashboard_team_drill_down(app_db: AppServices) -> None:
    at = render("dashboard", "C1")
    at.selectbox(key="dash_team").set_value("C1").run()
    assert not at.exception, at.exception
    assert "Teams" not in headings(at)  # team table only on the overview of all teams


@pytest.mark.parametrize("employee_id", ["A1", "B1"])
def test_dashboard_refused_for_a_and_b(app_db: AppServices, employee_id: str) -> None:
    at = render("dashboard", employee_id)
    assert [e.value for e in at.error] == ["You don't have access to this page."]
