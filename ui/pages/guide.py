"""Process guide, tailored to the viewer's level: A reviewee, B both roles, C manager + timeline."""

from __future__ import annotations

from datetime import date

import pandas as pd
import streamlit as st

from core.models import Level, Page
from core.timeline import suggested_timeline
from core.timeutil import format_date
from services.dashboard_service import MIN_GROUP_SIZE
from ui import queries
from ui.state import require_page
from ui.texts import t, t_raw


def render() -> None:
    viewer = require_page(Page.GUIDE)
    st.title(t("nav.guide"))
    role = t("auth.hr_badge") if viewer.is_hr else t(f"levels.{viewer.level.value}")
    st.markdown(t("guide.intro", role=role))

    cycle = queries.open_cycle()
    if cycle is not None:
        st.info(
            t(
                "guide.cycle_line",
                name=cycle.name,
                start=format_date(cycle.start_date),
                due=format_date(cycle.due_date),
            ),
            icon=":material/event:",
        )

    _steps()

    # A and B are reviewed; B and C review others; C (and HR) follow the timeline.
    if viewer.level in (Level.A, Level.B):
        st.subheader(f":material/person: {t('guide.reviewee_title')}")
        st.markdown(t("guide.reviewee"))
    if viewer.level in (Level.B, Level.C) or viewer.is_hr:
        st.subheader(f":material/supervisor_account: {t('guide.manager_title')}")
        st.markdown(t("guide.manager"))
        if viewer.level is Level.B:
            st.info(t("guide.line_manager_note"), icon=":material/lock_clock:")
        if viewer.level is Level.C and not viewer.is_hr:
            st.info(t("guide.department_note"), icon=":material/account_tree:")
    if viewer.level is Level.C and cycle is not None:
        _timeline(cycle.start_date, cycle.due_date)
    if viewer.is_hr:
        st.subheader(f":material/admin_panel_settings: {t('guide.hr_title')}")
        st.markdown(t("guide.hr"))

    st.subheader(f":material/visibility: {t('guide.privacy_title')}")
    st.markdown(t("guide.privacy", min=MIN_GROUP_SIZE))


def _steps() -> None:
    st.subheader(f":material/route: {t('guide.steps_title')}")
    rows = t_raw("guide.step_rows") or []
    frame = pd.DataFrame(
        rows, columns=[t("guide.col_step"), t("guide.col_who"), t("guide.col_what")]
    )
    st.table(frame.set_index(t("guide.col_step")))


def _timeline(start: date, due: date) -> None:
    st.subheader(f":material/timeline: {t('guide.timeline_title')}")
    st.caption(t("guide.timeline_intro"))
    frame = pd.DataFrame(
        [
            {
                t("guide.col_step"): t(f"guide.timeline_steps.{action.value}"),
                t("guide.col_by"): format_date(by),
            }
            for action, by in suggested_timeline(start, due)
        ]
    )
    st.table(frame.set_index(t("guide.col_step")))
