"""Overview: my review, my team (writeback table) and the HR company view."""

from __future__ import annotations

from datetime import tzinfo

import streamlit as st

from core.errors import DomainError
from core.models import Action, Level, Page, ReviewCycle, Status
from core.permissions import Viewer
from core.timeutil import format_date
from services.overview_service import OverviewRow
from ui import queries
from ui.components.confirm_dialog import ask_confirmation
from ui.components.next_step import my_guidance
from ui.components.override_summary import describe_override
from ui.components.progress_stepper import progress_stepper
from ui.components.status_badge import status_badge, status_label
from ui.components.writeback_table import PendingChange, render_writeback_table, reset_table
from ui.state import REVIEW_ID_KEY, clear_caches, flash, get_services, go_to, require_page
from ui.texts import error_text, t


def render() -> None:
    viewer = require_page(Page.OVERVIEW)
    tz = get_services().settings.tz
    st.title(t("nav.overview"))

    cycle = queries.open_cycle()
    if cycle is None:
        st.info(t("common.no_open_cycle"), icon=":material/info:")
        return
    st.caption(
        t(
            "common.cycle_line",
            name=cycle.name,
            start=format_date(cycle.start_date),
            due=format_date(cycle.due_date),
        )
    )

    if viewer.level is not Level.C:
        _my_review(viewer, cycle, tz)
    team = queries.team_rows(cycle.cycle_id, viewer.employee_id)
    if team:
        _my_team(viewer, cycle, team, tz)
    elif viewer.level is Level.B:
        st.subheader(t("overview.my_team"))
        st.info(t("overview.no_reports"), icon=":material/group_off:")
    if viewer.is_hr:
        _company(viewer, cycle, tz)


# ---- My review ----------------------------------------------------------------------


def _my_review(viewer: Viewer, cycle: ReviewCycle, tz: tzinfo) -> None:
    row = queries.my_review(cycle.cycle_id, viewer.employee_id)
    if row is None:
        return
    st.subheader(t("overview.my_review"))
    with st.container(border=True):
        st.markdown(t("overview.your_status", status=status_badge(row.status)))
        progress_stepper(row.status)
        guidance = my_guidance(row, tz)
        if guidance.kind == "done":
            st.success(guidance.text, icon=":material/celebration:")
        elif guidance.kind == "act" and row.review_id is not None:
            if st.button(guidance.text, type="primary", icon=":material/arrow_forward:"):
                go_to(Page.REVIEW, **{REVIEW_ID_KEY: row.review_id})
        else:
            st.info(guidance.text, icon=":material/schedule:")


# ---- My team ------------------------------------------------------------------------


def _my_team(viewer: Viewer, cycle: ReviewCycle, rows: list[OverviewRow], tz: tzinfo) -> None:
    st.subheader(t("overview.my_team"))
    started = sum(r.status is not Status.NOT_STARTED for r in rows)
    completed = sum(r.status is Status.COMPLETED for r in rows)
    st.caption(t("overview.team_summary", count=len(rows), started=started, completed=completed))
    if queries.team_locked(cycle.cycle_id, viewer.employee_id):
        st.info(t("overview.team_locked"), icon=":material/lock:")
    elif started == 0:
        st.info(t("overview.no_reviews_started"), icon=":material/play_circle:")
    with st.expander(":material/help: " + t("overview.table_help_title")):
        st.markdown(t("overview.team_help"))
    render_writeback_table(
        rows,
        viewer.employee_id,
        tz,
        get_services().clock.now(),
        key="team",
        on_save=lambda changes: _save(changes, viewer, cycle, "team"),
    )
    _open_review_picker(rows, "team")


def _save(changes: list[PendingChange], viewer: Viewer, cycle: ReviewCycle, key: str) -> None:
    """Send each changed cell through the workflow's single entry point."""
    reviews = get_services().reviews
    for change in changes:
        name = change.row.employee.name
        try:
            if change.action is Action.INITIATE:
                reviews.transition(
                    None,
                    Action.INITIATE,
                    viewer.employee_id,
                    {"reviewee_id": change.row.employee.employee_id, "cycle_id": cycle.cycle_id},
                )
            else:
                reviews.transition(
                    change.row.review_id,
                    change.action,
                    viewer.employee_id,
                    {"meeting_at": change.meeting_at},
                )
        except DomainError as exc:
            flash(t("confirm.failed", name=name, message=error_text(exc)), ok=False)
        else:
            flash(t("confirm.saved", name=name, action=t(f"actions.{change.action.value}")))
    clear_caches()
    reset_table(key)


def _open_review_picker(rows: list[OverviewRow], key: str) -> None:
    started = [r for r in rows if r.review_id is not None]
    if not started:
        return
    left, right = st.columns([3, 1], vertical_alignment="bottom")
    choice = left.selectbox(
        t("overview.open_review"),
        options=[r.review_id for r in started],
        format_func=lambda rid: next(
            f"{r.employee.name} — {status_label(r.status)}" for r in started if r.review_id == rid
        ),
        key=f"open_{key}",
    )
    if right.button(
        t("overview.open_review"), key=f"open_btn_{key}", icon=":material/open_in_new:"
    ):
        go_to(Page.REVIEW, **{REVIEW_ID_KEY: choice})


# ---- HR: company view ---------------------------------------------------------------


def _company(viewer: Viewer, cycle: ReviewCycle, tz: tzinfo) -> None:
    services = get_services()
    st.divider()
    st.subheader(t("overview.company"))
    rows = queries.company_rows(cycle.cycle_id, viewer.employee_id)

    with st.expander(":material/filter_list: " + t("hr.filters"), expanded=True):
        c1, c2, c3, c4 = st.columns(4)
        departments = c1.multiselect(
            t("hr.department"), sorted({r.employee.department for r in rows}), key="f_dept"
        )
        levels = c2.multiselect(
            t("hr.level"),
            [Level.B, Level.A],
            format_func=lambda lv: f"{lv.value} · {t(f'levels.{lv.value}')}",
            key="f_level",
        )
        statuses = c3.multiselect(
            t("hr.status"), list(Status), format_func=status_label, key="f_status"
        )
        managers = {r.reviewer.employee_id: r.reviewer.name for r in rows if r.reviewer}
        manager_ids = c4.multiselect(
            t("hr.manager"),
            sorted(managers, key=lambda m: managers[m]),
            format_func=lambda m: managers[m],
            key="f_manager",
        )
    filtered = services.overview.filter_rows(
        rows, departments=departments, levels=levels, statuses=statuses, manager_ids=manager_ids
    )
    st.caption(t("hr.showing", shown=len(filtered), total=len(rows)))
    if not filtered:
        st.info(t("hr.no_match"), icon=":material/search_off:")
        return
    render_writeback_table(
        filtered,
        viewer.employee_id,
        tz,
        services.clock.now(),
        key="company",
        company=True,
        on_save=lambda changes: _save(changes, viewer, cycle, "company"),
    )
    _open_review_picker(filtered, "company")
    _override_panel(viewer, filtered)


def _override_panel(viewer: Viewer, rows: list[OverviewRow]) -> None:
    st.markdown(f"#### :material/admin_panel_settings: {t('hr.overrides')}")
    st.caption(t("hr.override_help"))
    started = [r for r in rows if r.review_id is not None]
    if not started:
        st.caption(t("hr.no_started"))
        return
    by_id = {r.review_id: r for r in started}
    with st.container(border=True):
        left, right = st.columns([2, 1])
        review_id = left.selectbox(
            t("hr.pick_review"),
            list(by_id),
            format_func=lambda rid: (
                f"{by_id[rid].employee.name} — {status_label(by_id[rid].status)}"
            ),
            key="ovr_review",
        )
        direction = right.radio(
            t("hr.direction"),
            [Action.HR_ADVANCE, Action.HR_REVERT],
            format_func=lambda a: t("hr.forward") if a is Action.HR_ADVANCE else t("hr.back"),
            horizontal=True,
            key="ovr_direction",
        )
        reason = st.text_input(
            t("hr.reason"), placeholder=t("hr.reason_placeholder"), key="ovr_reason"
        )
        row = by_id[review_id]
        try:
            outcome = get_services().reviews.preview(
                review_id, direction, viewer.employee_id, {"reason": reason.strip() or "-"}
            )
        except DomainError as exc:
            st.warning(error_text(exc), icon=":material/block:")
            return
        lines = describe_override(outcome, row.employee.name)
        st.markdown("\n".join(f"- {line}" for line in lines))
        if st.button(
            t("hr.apply"),
            type="primary",
            disabled=not reason.strip(),
            icon=":material/gavel:",
            key="ovr_apply",
        ):
            ask_confirmation(
                viewer.employee_id,
                lines,
                lambda: _apply_override(viewer, row, direction, reason.strip()),
                confirm_label=t(f"actions.{direction.value}"),
            )


def _apply_override(viewer: Viewer, row: OverviewRow, action: Action, reason: str) -> None:
    try:
        result = get_services().reviews.transition(
            row.review_id, action, viewer.employee_id, {"reason": reason}
        )
    except DomainError as exc:
        flash(t("confirm.failed", name=row.employee.name, message=error_text(exc)), ok=False)
    else:
        flash(t("hr.done", name=row.employee.name, status=status_label(result.new_status)))
        st.session_state.pop("ovr_reason", None)
    clear_caches()
    reset_table("company")
