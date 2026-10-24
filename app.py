"""Streamlit entry point: config, current user and role-based navigation.

Start with `python manage.py run` (or scripts/run_app.sh / .bat).
"""

from __future__ import annotations

from collections.abc import Callable

import streamlit as st

from config.settings import ConfigError
from core.models import Page
from core.permissions import visible_pages
from ui import queries
from ui.auth import get_auth_provider
from ui.components.confirm_dialog import render_pending_confirmation
from ui.pages import admin, dashboard, guide, overview, review
from ui.state import get_services, get_settings, register_pages, set_viewer, show_flashes
from ui.texts import t

PAGE_SPECS: dict[Page, tuple[Callable[[], None], str]] = {
    Page.OVERVIEW: (overview.render, ":material/dashboard:"),
    Page.REVIEW: (review.render, ":material/assignment:"),
    Page.GUIDE: (guide.render, ":material/menu_book:"),
    Page.DASHBOARD: (dashboard.render, ":material/monitoring:"),
    Page.ADMIN: (admin.render, ":material/admin_panel_settings:"),
}


def main() -> None:
    st.set_page_config(page_title=t("app.title"), page_icon=":material/fact_check:", layout="wide")
    try:
        settings = get_settings()
    except ConfigError as exc:
        st.error(f"Configuration problem: {exc}")
        st.stop()
    services = get_services()
    if not settings.db_path.exists() or not services.schema_ready():
        st.error(t("common.db_missing"))
        st.stop()

    st.sidebar.title(t("app.title"))
    auth = get_auth_provider(settings, services.directory)
    auth.render_controls()
    employee_id = auth.current_employee_id()
    viewer = queries.viewer(employee_id) if employee_id else None
    set_viewer(viewer)
    if viewer is None:
        st.info(t("auth.no_employees"))
        st.stop()
    st.sidebar.caption(
        t(f"levels.{viewer.level.value}") + (f" · {t('auth.hr_badge')}" if viewer.is_hr else "")
    )

    pages = {
        page: st.Page(
            PAGE_SPECS[page][0],
            title=t(f"nav.{page.value}"),
            icon=PAGE_SPECS[page][1],
            url_path=page.value,
            default=page is Page.OVERVIEW,
        )
        for page in visible_pages(viewer)
    }
    register_pages(pages)
    show_flashes()
    st.navigation(list(pages.values())).run()
    render_pending_confirmation(viewer.employee_id)


main()
