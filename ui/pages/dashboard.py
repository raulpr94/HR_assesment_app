"""Dashboard (C level and HR): progress, overdue reviews, ratings, drill-down by team."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from core.errors import DomainError
from core.models import Page, Status
from core.timeutil import format_date
from services.dashboard_service import Dashboard
from ui import queries
from ui.components.status_badge import status_label, status_text
from ui.state import get_services, require_page
from ui.texts import error_text, t

SELF_COLOUR, MANAGER_COLOUR = "#4C78A8", "#F58518"  # distinct, colour-blind safe pair


def render() -> None:
    viewer = require_page(Page.DASHBOARD)
    st.title(t("nav.dashboard"))
    cycle = queries.open_cycle()
    if cycle is None:
        st.info(t("common.no_open_cycle"), icon=":material/info:")
        return
    services = get_services()

    overall = services.dashboard.build(cycle.cycle_id, viewer)
    st.caption(
        (t("dashboard.scope_company") if overall.company_wide else t("dashboard.scope_tree"))
        + f" · {cycle.name}"
    )
    teams = {team.manager.employee_id: team.manager.name for team in overall.teams}
    team_id = st.selectbox(
        t("dashboard.team"),
        ["", *teams],
        format_func=lambda m: teams.get(m, t("dashboard.all_teams")),
        key="dash_team",
    )
    try:
        data = (
            services.dashboard.build(cycle.cycle_id, viewer, team_manager_id=team_id)
            if team_id
            else overall
        )
    except DomainError as exc:
        st.error(error_text(exc))
        return
    if data.eligible == 0:
        st.info(t("dashboard.empty"), icon=":material/group_off:")
        return

    _metrics(data)
    left, right = st.columns(2, gap="large")
    with left:
        _status_chart(data)
    with right:
        _overdue(data)
    _ratings(data)
    if not team_id:
        _teams(overall)


def _metrics(data: Dashboard) -> None:
    started = data.eligible - data.status_counts[Status.NOT_STARTED]
    c1, c2, c3, c4 = st.columns(4)
    c1.metric(
        t("dashboard.completion"),
        f"{data.completion_rate:.0%}",
        help=t("dashboard.completion_help"),
    )
    c1.caption(t("dashboard.completed_of", done=data.completed, total=data.eligible))
    c2.metric(t("dashboard.in_progress"), started - data.completed)
    c3.metric(t("dashboard.not_started"), data.status_counts[Status.NOT_STARTED])
    if data.days_to_due > 0:
        due_text = t("dashboard.days_left", days=data.days_to_due)
    elif data.days_to_due == 0:
        due_text = t("dashboard.due_today")
    else:
        due_text = t("dashboard.days_over", days=-data.days_to_due)
    c4.metric(t("dashboard.due"), format_date(data.cycle.due_date))
    c4.caption(due_text)


def _status_chart(data: Dashboard) -> None:
    st.subheader(t("dashboard.status_title"))
    frame = pd.DataFrame(
        {
            "status": [status_text(s) for s in Status],
            "count": [data.status_counts[s] for s in Status],
        }
    )
    # Bars are labelled with status text and symbol; colour carries no extra meaning.
    st.bar_chart(
        frame,
        x="status",
        y="count",
        horizontal=True,
        sort=False,
        x_label="",
        y_label="",
        height=260,
    )


def _overdue(data: Dashboard) -> None:
    st.subheader(t("dashboard.overdue_title"))
    if data.days_to_due >= 0:
        st.caption(t("dashboard.overdue_not_yet", due=format_date(data.cycle.due_date)))
        return
    if not data.overdue:
        st.success(t("dashboard.overdue_none"), icon=":material/task_alt:")
        return
    st.dataframe(
        pd.DataFrame(
            [
                {
                    t("dashboard.col_employee"): item.employee.name,
                    t("dashboard.col_manager"): item.manager.name if item.manager else "",
                    t("dashboard.col_status"): status_label(item.status),
                    t("dashboard.col_days"): item.days_overdue,
                }
                for item in data.overdue
            ]
        ),
        hide_index=True,
        width="stretch",
        height=260,
    )


def _ratings(data: Dashboard) -> None:
    st.subheader(t("dashboard.ratings_title"))
    st.caption(t("dashboard.ratings_help", min=data.min_group_size))
    shown = [r for r in data.ratings if r.self_avg is not None or r.manager_avg is not None]
    if not shown:
        st.info(t("dashboard.ratings_none"), icon=":material/hourglass_empty:")
        return
    self_label, manager_label = t("dashboard.col_self"), t("dashboard.col_manager_avg")
    chart = pd.DataFrame(
        {
            "question": [r.question for r in shown],
            self_label: [r.self_avg for r in shown],
            manager_label: [r.manager_avg for r in shown],
        }
    )
    st.bar_chart(
        chart,
        x="question",
        y=[self_label, manager_label],
        color=[SELF_COLOUR, MANAGER_COLOUR],
        horizontal=True,
        stack=False,
        sort=False,
        x_label="",
        y_label="",
        height=360,
    )
    hidden = t("dashboard.hidden", min=data.min_group_size)
    st.dataframe(
        pd.DataFrame(
            [
                {
                    t("dashboard.col_question"): r.question,
                    self_label: f"{r.self_avg:.2f}" if r.self_avg is not None else hidden,
                    manager_label: f"{r.manager_avg:.2f}" if r.manager_avg is not None else hidden,
                    t("dashboard.col_n_self"): r.self_n,
                    t("dashboard.col_n_manager"): r.manager_n,
                }
                for r in data.ratings
            ]
        ),
        hide_index=True,
        width="stretch",
    )


def _teams(data: Dashboard) -> None:
    st.subheader(t("dashboard.teams_title"))
    st.dataframe(
        pd.DataFrame(
            [
                {
                    t("dashboard.col_team"): team.manager.name,
                    t("dashboard.col_size"): team.size,
                    t("dashboard.col_started"): team.started,
                    t("dashboard.col_completed"): team.completed,
                    t("dashboard.col_rate"): team.completion_rate,
                    t("dashboard.col_overdue"): team.overdue,
                }
                for team in data.teams
            ]
        ),
        hide_index=True,
        width="stretch",
        column_config={
            t("dashboard.col_rate"): st.column_config.ProgressColumn(
                t("dashboard.col_rate"), format="percent", min_value=0, max_value=1
            )
        },
    )
