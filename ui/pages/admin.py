"""Admin (HR only): cycles, employee import, audit log and notification outbox."""

from __future__ import annotations

import csv
import io
from collections.abc import Callable
from datetime import tzinfo

import pandas as pd
import streamlit as st

from core.errors import DomainError
from core.models import CycleStatus, Level, NotificationStatus, Page
from core.permissions import Viewer
from core.timeutil import format_date, format_local_datetime
from services.import_service import COLUMNS
from ui import queries
from ui.components.confirm_dialog import ask_confirmation
from ui.state import clear_caches, flash, get_services, require_page
from ui.texts import error_text, t

DATE_INPUT_FORMAT = "DD.MM.YYYY"


def render() -> None:
    viewer = require_page(Page.ADMIN)
    tz = get_services().settings.tz
    st.title(t("nav.admin"))
    cycles, employees, audit, outbox = st.tabs(
        [
            f":material/event_repeat: {t('admin.tab_cycles')}",
            f":material/groups: {t('admin.tab_employees')}",
            f":material/history: {t('admin.tab_audit')}",
            f":material/outgoing_mail: {t('admin.tab_outbox')}",
        ]
    )
    with cycles:
        _cycles(viewer)
    with employees:
        _employees(viewer)
    with audit:
        _audit(viewer, tz)
    with outbox:
        _outbox(viewer, tz)


def _run(action: Callable[[], object], success: str | None = None) -> None:
    """Run a write, report the result as a toast, refresh cached data."""
    try:
        action()
    except DomainError as exc:
        flash(error_text(exc), ok=False)
    else:
        if success:
            flash(success)
    clear_caches()


# ---- cycles --------------------------------------------------------------------------


def _cycles(viewer: Viewer) -> None:
    services = get_services()
    st.subheader(t("admin.cycles_title"))
    summaries = services.admin.cycles(viewer)
    if summaries:
        frame = pd.DataFrame(
            [
                {
                    t("admin.col_name"): s.cycle.name,
                    t("admin.col_start"): format_date(s.cycle.start_date),
                    t("admin.col_due"): format_date(s.cycle.due_date),
                    t("admin.col_status"): t(f"admin.status_{s.cycle.status.value}"),
                    t("admin.col_started"): f"{s.started} / {s.eligible}"
                    if s.cycle.status is CycleStatus.OPEN
                    else str(s.started),
                    t("admin.col_completed"): s.completed,
                }
                for s in summaries
            ]
        )
        st.dataframe(frame, hide_index=True, width="stretch")
    else:
        st.info(t("admin.no_cycles"), icon=":material/info:")

    current = next((s.cycle for s in summaries if s.cycle.status is CycleStatus.OPEN), None)
    left, right = st.columns(2, gap="large")
    with left:
        st.markdown(f"##### {t('admin.open_title')}")
        if current is not None:
            st.caption(t("admin.open_blocked", name=current.name))
        else:
            _open_cycle_form(viewer)
    with right:
        if current is not None:
            st.markdown(f"##### {t('admin.close_title')}")
            st.caption(t("admin.close_help"))
            if st.button(t("admin.close_button"), icon=":material/lock:", key="close_cycle"):
                ask_confirmation(
                    viewer.employee_id,
                    [t("admin.confirm_close", name=current.name)],
                    lambda: _run(
                        lambda: services.cycles.close_cycle(current.cycle_id, viewer.employee_id),
                        t("admin.closed", name=current.name),
                    ),
                    confirm_label=t("admin.close_button").rstrip("…"),
                )


def _open_cycle_form(viewer: Viewer) -> None:
    services = get_services()
    today = services.cycles.today()
    with st.form("open_cycle", enter_to_submit=False):
        name = st.text_input(t("admin.cycle_name"), placeholder=t("admin.cycle_name_placeholder"))
        start_col, due_col = st.columns(2)
        start = start_col.date_input(t("admin.start"), value=today, format=DATE_INPUT_FORMAT)
        due = due_col.date_input(
            t("admin.due"), value=today + pd.Timedelta(days=42), format=DATE_INPUT_FORMAT
        )
        pressed = st.form_submit_button(
            t("admin.open_button"), type="primary", icon=":material/play_arrow:"
        )
    if not pressed:
        return
    clean = name.strip()
    if not clean:
        st.error(t("errors.cycle_name_required"))
        return
    if due < start:
        st.error(t("errors.cycle_invalid_dates"))
        return
    ask_confirmation(
        viewer.employee_id,
        [t("admin.confirm_open", name=clean, start=format_date(start), due=format_date(due))],
        lambda: _run(
            lambda: services.cycles.open_cycle(clean, start, due, actor_id=viewer.employee_id),
            t("admin.opened", name=clean),
        ),
        confirm_label=t("admin.open_button").rstrip("…"),
    )


# ---- employees -----------------------------------------------------------------------


def _csv_bytes(rows: list[dict[str, str]]) -> bytes:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=list(COLUMNS))
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue().encode("utf-8")


def _employees(viewer: Viewer) -> None:
    services = get_services()
    st.subheader(t("admin.employees_title"))
    people = queries.login_options()
    st.caption(
        t(
            "admin.employees_count",
            total=len(people),
            hr=sum(p.is_hr_admin for p in people),
            c=sum(p.level is Level.C for p in people),
            b=sum(p.level is Level.B for p in people),
            a=sum(p.level is Level.A for p in people),
        )
    )
    left, right = st.columns(2)
    left.download_button(
        t("admin.template"),
        _csv_bytes(
            [
                {
                    "employee_id": "H001",
                    "name": "Example HR Director",
                    "email": "hr@example.com",
                    "level": "C",
                    "role_title": "HR Director",
                    "department": "People",
                    "manager_id": "",
                    "is_hr_admin": "true",
                },
            ]
        ),
        file_name="employees_template.csv",
        mime="text/csv",
        icon=":material/download:",
    )
    right.download_button(
        t("admin.export"),
        _csv_bytes(
            [
                {
                    "employee_id": p.employee_id,
                    "name": p.name,
                    "email": p.email,
                    "level": p.level.value,
                    "role_title": p.role_title,
                    "department": p.department,
                    "manager_id": p.manager_id or "",
                    "is_hr_admin": "true" if p.is_hr_admin else "false",
                }
                for p in people
            ]
        ),
        file_name="employees_current.csv",
        mime="text/csv",
        icon=":material/download:",
    )

    upload = st.file_uploader(t("admin.upload"), type=["csv"], help=t("admin.upload_help"))
    if upload is None:
        return
    raw = upload.getvalue()
    _, report = services.imports.validate_bytes(raw, upload.name)
    if not report.ok:
        st.error(t("admin.invalid", count=len(report.issues)), icon=":material/error:")
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        t("admin.issue_row"): issue.row or "",
                        t("admin.issue_id"): issue.employee_id or "",
                        t("admin.issue_field"): issue.field or "",
                        t("admin.issue_message"): issue.message,
                    }
                    for issue in report.issues
                ]
            ),
            hide_index=True,
            width="stretch",
        )
        return
    st.success(t("admin.valid", count=report.total_rows), icon=":material/task_alt:")
    if st.button(
        t("admin.import_button"), type="primary", icon=":material/upload:", key="import_csv"
    ):

        def do_import() -> None:
            done = services.imports.import_bytes(raw, upload.name, actor_id=viewer.employee_id)
            if done.ok:
                flash(
                    t(
                        "admin.imported",
                        new=done.inserted,
                        updated=done.updated,
                        deactivated=done.deactivated,
                    )
                )
            else:
                flash(t("admin.invalid", count=len(done.issues)), ok=False)

        ask_confirmation(
            viewer.employee_id,
            [t("admin.confirm_import", count=report.total_rows, file=upload.name)],
            lambda: _run(do_import),
            confirm_label=t("admin.import_button").rstrip("…"),
        )


# ---- audit log -----------------------------------------------------------------------


def _audit(viewer: Viewer, tz: tzinfo) -> None:
    admin = get_services().admin
    st.subheader(t("admin.audit_title"))
    people = {p.employee_id: p.name for p in queries.login_options()}
    c1, c2, c3, c4 = st.columns([3, 2, 1, 1])
    actions = c1.multiselect(
        t("admin.audit_actions"), admin.audit_actions(viewer), key="audit_actions"
    )
    actor = c2.selectbox(
        t("admin.audit_actor"),
        ["", *sorted(people, key=lambda p: people[p]), "system"],
        format_func=lambda p: people.get(p, p) if p else t("admin.audit_anyone"),
        key="audit_actor",
    )
    review_id = c3.number_input(t("admin.audit_review"), min_value=0, step=1, key="audit_review")
    limit = c4.selectbox(t("admin.audit_limit"), [100, 500, 2000], index=1, key="audit_limit")
    entries = admin.audit(
        viewer,
        actor_id=actor or None,
        actions=actions,
        review_id=int(review_id) or None,
        limit=int(limit),
    )
    if not entries:
        st.info(t("admin.audit_empty"), icon=":material/search_off:")
        return
    columns = [
        "timestamp",
        "actor_name",
        "review_id",
        "action",
        "field_changed",
        "old_value",
        "new_value",
        "reason",
    ]
    frame = pd.DataFrame(entries)[columns]
    frame["timestamp"] = [format_local_datetime(ts, tz) for ts in frame["timestamp"]]
    labels = {c: t(f"review.history_columns.{c}") for c in columns}
    labels.update(actor_name=t("admin.audit_actor"), review_id=t("admin.col_id"))
    frame = frame.rename(columns=labels)
    st.dataframe(frame, hide_index=True, width="stretch")
    st.download_button(
        t("admin.audit_download"),
        frame.to_csv(index=False).encode("utf-8"),
        file_name="audit_log.csv",
        mime="text/csv",
        icon=":material/download:",
    )


# ---- outbox --------------------------------------------------------------------------


def _outbox(viewer: Viewer, tz: tzinfo) -> None:
    admin = get_services().admin
    st.subheader(t("admin.outbox_title"))
    zapier = admin.notify_mode == "zapier"
    st.markdown(t("admin.mode_zapier" if zapier else "admin.mode_dry_run"))

    counts = admin.outbox_counts(viewer)
    for column, status in zip(st.columns(len(counts)), counts, strict=True):
        column.metric(t(f"admin.status_{status}"), counts[status])

    def process(retry: bool) -> None:
        report = admin.send_pending(viewer, retry_failed=retry)
        if report.processed == 0:
            flash(t("admin.nothing_pending"))
            return
        details = ", ".join(f"{n} {t(f'admin.status_{s}')}" for s, n in report.counts.items())
        flash(
            t("admin.processed", count=report.processed, details=details),
            ok=not report.counts.get("failed"),
        )

    left, right = st.columns(2)
    label = t("admin.process_zapier") if zapier else t("admin.process")
    if left.button(
        label,
        type="primary",
        icon=":material/send:",
        key="outbox_process",
        disabled=counts["pending"] == 0,
    ):
        if zapier:  # real emails: confirm first
            ask_confirmation(
                viewer.employee_id,
                [t("admin.confirm_send", count=counts["pending"])],
                lambda: _run(lambda: process(False)),
            )
        else:
            _run(lambda: process(False))
            st.rerun()
    if right.button(
        t("admin.retry"),
        icon=":material/replay:",
        key="outbox_retry",
        disabled=counts["failed"] == 0,
    ):
        ask_confirmation(
            viewer.employee_id,
            [t("admin.confirm_retry")],
            lambda: _run(lambda: process(True)),
        )

    choice = st.segmented_control(
        t("admin.outbox_filter"),
        ["all", *(s.value for s in NotificationStatus)],
        format_func=lambda v: t("admin.outbox_all") if v == "all" else t(f"admin.status_{v}"),
        default="all",
        key="outbox_status",
    )
    status = NotificationStatus(choice) if choice and choice != "all" else None
    rows = admin.outbox(viewer, status=status)
    if not rows:
        st.info(t("admin.outbox_empty"), icon=":material/inbox:")
        return
    frame = pd.DataFrame(
        [
            {
                t("admin.col_id"): r["id"],
                t("admin.col_type"): r["type"],
                t("admin.col_to"): r["recipient_email"],
                t("admin.col_subject"): r["subject"],
                t("admin.col_status"): t(f"admin.status_{r['status']}"),
                t("admin.col_attempts"): r["attempts"],
                t("admin.col_created"): format_local_datetime(r["created_at"], tz),
                t("admin.col_sent"): format_local_datetime(r["sent_at"], tz),
                t("admin.col_error"): r["last_error"] or "",
            }
            for r in rows
        ]
    )
    st.dataframe(frame, hide_index=True, width="stretch")

    by_id = {r["id"]: r for r in rows}
    chosen = st.selectbox(
        t("admin.preview"),
        list(by_id),
        format_func=lambda i: f"#{i} · {by_id[i]['recipient_email']} · {by_id[i]['subject']}",
        key="outbox_preview",
    )
    message = admin.outbox_message(viewer, chosen)
    if message:
        with st.container(border=True):
            st.markdown(f"**{message['subject']}**  \n{message['recipient_email']}")
            st.caption(t("admin.preview_body"))
            st.code(message["body"], language=None, wrap_lines=True)
            with st.expander(t("admin.preview_payload")):
                st.json(message["payload"], expanded=False)
