"""Admin page in the real Streamlit app (AppTest)."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from config.settings import PROJECT_ROOT, load_settings
from services.container import AppServices, build_services
from tests.conftest import SMALL_CSV

APP = str(PROJECT_ROOT / "app.py")


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
    st.cache_resource.clear()
    st.cache_data.clear()
    yield services
    st.cache_resource.clear()
    st.cache_data.clear()
    services.engine.dispose()


def _admin_script(employee_id: str) -> None:
    """Render only the Admin page for one person, as app.py would."""
    from ui.components.confirm_dialog import render_pending_confirmation
    from ui.pages import admin
    from ui.state import get_services, set_viewer, show_flashes

    set_viewer(get_services().directory.viewer(employee_id))
    show_flashes()
    admin.render()
    render_pending_confirmation(employee_id)


def admin_as(employee_id: str) -> AppTest:
    at = AppTest.from_function(_admin_script, args=(employee_id,), default_timeout=30)
    at.run()
    return at


def press(at: AppTest, key: str) -> None:
    at.button(key=key).click().run()
    assert not at.exception, at.exception


def confirm(at: AppTest) -> None:
    press(at, "confirm_yes")


def test_admin_page_renders_all_tabs(app_db: AppServices) -> None:
    at = admin_as("H1")
    assert not at.exception, at.exception
    assert [tab.label.split(" ", 1)[1] for tab in at.tabs] == [
        "Cycles",
        "Employees",
        "Audit log",
        "Notifications",
    ]
    assert any("dry run" in m.value for m in at.markdown)
    assert len(at.dataframe) >= 3  # cycles, audit log, outbox


def test_admin_page_hidden_and_refused_for_others(app_db: AppServices) -> None:
    at = AppTest.from_file(APP, default_timeout=30)
    at.session_state["view_as_id"] = "C1"
    at.run()
    with pytest.raises(ValueError, match="Known pages"):
        at.switch_page("ui/pages/admin.py")  # not in C1's navigation at all
    direct = admin_as("C1")  # and refused when rendered directly
    assert [e.value for e in direct.error] == ["You don't have access to this page."]
    assert not direct.tabs


def test_close_then_open_cycle(app_db: AppServices) -> None:
    at = admin_as("H1")
    press(at, "close_cycle")
    confirm(at)
    assert app_db.cycles.get_open_cycle() is None

    at.text_input[0].input("2027 Annual")
    next(b for b in at.button if b.label == "Open cycle…").click().run()
    confirm(at)
    opened = app_db.cycles.get_open_cycle()
    assert opened is not None and opened.name == "2027 Annual"


def test_process_outbox_in_dry_run(app_db: AppServices) -> None:
    at = admin_as("H1")
    press(at, "outbox_process")  # dry run: no confirmation needed
    hr = app_db.directory.viewer("H1")
    assert hr is not None
    assert app_db.admin.outbox_counts(hr)["pending"] == 0
