"""The writeback table: one row per reviewee, step dates filled in as they happen.

Only two cells are ever editable: *Initiate* (step 1) and *Meeting* (step 4).
``st.data_editor`` can only lock whole columns, so a column is unlocked when at least
one row may edit it, and every edit is then checked per row against
``OverviewRow.actions``: edits a row doesn't allow are listed and never saved.
``ReviewService.transition`` checks everything once more on save.

This component never touches the database: saving is the caller's ``on_save``.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, tzinfo

import pandas as pd
import streamlit as st

from core.models import Action, Status
from core.timeutil import to_local
from services.overview_service import OverviewRow
from ui.components.confirm_dialog import ask_confirmation
from ui.components.next_step import table_next_text
from ui.components.status_badge import status_label, status_text
from ui.texts import t

STEP_COLUMNS = {
    "initiated": "initiated_at",
    "self_submitted": "self_submitted_at",
    "manager_submitted": "manager_submitted_at",
    "meeting": "meeting_at",
    "reviewee_ack": "reviewee_ack_at",
    "reviewer_ack": "reviewer_ack_at",
    "completed": "completed_at",
}
EDITABLE = ("initiate", "meeting")
MEETING_STEP = timedelta(minutes=15)


@dataclass(frozen=True)
class PendingChange:
    row: OverviewRow
    action: Action
    meeting_at: datetime | None = None  # naive local time

    def describe(self) -> str:
        name = self.row.employee.name
        if self.action is Action.INITIATE:
            return t("confirm.initiate", name=name)
        when = self.meeting_at.strftime("%d.%m.%Y %H:%M") if self.meeting_at else ""
        key = (
            "reschedule_meeting"
            if self.row.status is Status.MEETING_SCHEDULED
            else ("schedule_meeting")
        )
        return t(f"confirm.{key}", name=name, when=when)


def _local(value: datetime | None, tz: tzinfo) -> datetime | None:
    return to_local(value, tz).replace(tzinfo=None) if value else None


def build_frame(
    rows: Sequence[OverviewRow], viewer_id: str, tz: tzinfo, *, company: bool = False
) -> pd.DataFrame:
    """One row per reviewee, indexed by employee_id. Times are local and naive."""
    records = []
    for row in rows:
        record: dict[str, object] = {
            "name": row.employee.name,
            "role": row.employee.role_title,
        }
        if company:
            record["department"] = row.employee.department
            record["level"] = row.employee.level.value
            record["manager"] = row.reviewer.name if row.reviewer else ""
        record["status"] = status_text(row.status)
        record["next"] = table_next_text(row, viewer_id, tz)
        record["initiate"] = row.progress is not None
        for column, field in STEP_COLUMNS.items():
            record[column] = _local(getattr(row.progress, field), tz) if row.progress else None
        records.append(record)
    columns = ["name", "role"]
    if company:
        columns += ["department", "level", "manager"]
    columns += ["status", "next", "initiate", *STEP_COLUMNS]
    frame = pd.DataFrame.from_records(
        records, index=[r.employee.employee_id for r in rows], columns=columns
    )
    for column in STEP_COLUMNS:
        frame[column] = pd.to_datetime(frame[column])
    frame["initiate"] = frame["initiate"].astype(bool)
    return frame


def _is_missing(value: object) -> bool:
    return value is None or bool(pd.isna(value))  # type: ignore[arg-type]


def _as_datetime(value: object) -> datetime:
    return value.to_pydatetime() if isinstance(value, pd.Timestamp) else value  # type: ignore[return-value]


def detect_changes(
    rows: Sequence[OverviewRow], original: pd.DataFrame, edited: pd.DataFrame
) -> tuple[list[PendingChange], list[str]]:
    """Compare the edited table with the original: (allowed changes, rejected edits)."""
    pending: list[PendingChange] = []
    rejected: list[str] = []
    for row in rows:
        emp_id, name = row.employee.employee_id, row.employee.name
        before, after = bool(original.at[emp_id, "initiate"]), bool(edited.at[emp_id, "initiate"])
        if before != after:
            if before:
                rejected.append(t("table.reject_uninitiate", name=name))
            elif Action.INITIATE in row.actions:
                pending.append(PendingChange(row, Action.INITIATE))
            else:
                locked = row.status is Status.NOT_STARTED
                reason = (
                    t("table.reason_locked")
                    if locked
                    else t("table.reason_status", status=status_label(row.status))
                )
                rejected.append(t("table.reject_initiate", name=name, reason=reason))

        old_meeting, new_meeting = original.at[emp_id, "meeting"], edited.at[emp_id, "meeting"]
        both_missing = _is_missing(old_meeting) and _is_missing(new_meeting)
        same = (
            not _is_missing(old_meeting)
            and not _is_missing(new_meeting)
            and _as_datetime(old_meeting) == _as_datetime(new_meeting)
        )
        if both_missing or same:
            continue
        if _is_missing(new_meeting):
            rejected.append(t("table.reject_meeting_cleared", name=name))
        elif Action.SCHEDULE_MEETING in row.actions:
            meeting = _as_datetime(new_meeting).replace(second=0, microsecond=0)
            pending.append(PendingChange(row, Action.SCHEDULE_MEETING, meeting))
        else:
            rejected.append(t("table.reject_meeting", name=name))
    return pending, rejected


def _version_key(key: str) -> str:
    return f"_wb_version_{key}"


def reset_table(key: str) -> None:
    """Drop unsaved edits (a fresh editor key makes Streamlit forget them)."""
    st.session_state[_version_key(key)] = st.session_state.get(_version_key(key), 0) + 1


def _column_config(company: bool, now_local: datetime) -> dict[str, object]:
    date_col = st.column_config.DatetimeColumn
    config: dict[str, object] = {
        "name": st.column_config.TextColumn(t("table.name"), pinned=True),
        "role": st.column_config.TextColumn(t("table.role")),
        "status": st.column_config.TextColumn(t("table.status")),
        "next": st.column_config.TextColumn(t("table.next")),
        "initiate": st.column_config.CheckboxColumn(
            t("table.initiate"), help=t("table.initiate_help")
        ),
        "meeting": date_col(
            t("table.meeting"),
            help=t("table.meeting_help"),
            format="DD.MM.YYYY HH:mm",
            step=MEETING_STEP,
            min_value=now_local,
        ),
    }
    for column in STEP_COLUMNS:
        if column != "meeting":
            config[column] = date_col(t(f"table.{column}"), format="DD.MM.YYYY")
    if company:
        config["department"] = st.column_config.TextColumn(t("table.department"))
        config["level"] = st.column_config.TextColumn(t("table.level"), width="small")
        config["manager"] = st.column_config.TextColumn(t("table.manager"))
    return config


def render_writeback_table(
    rows: Sequence[OverviewRow],
    viewer_id: str,
    tz: tzinfo,
    now: datetime,
    *,
    key: str,
    on_save: Callable[[list[PendingChange]], None],
    company: bool = False,
) -> None:
    original = build_frame(rows, viewer_id, tz, company=company)
    order = list(original.columns)

    can_initiate = any(Action.INITIATE in r.actions for r in rows)
    can_schedule = any(Action.SCHEDULE_MEETING in r.actions for r in rows)
    disabled = [c for c in order if c not in EDITABLE]
    if not can_initiate:
        disabled.append("initiate")
    if not can_schedule:
        disabled.append("meeting")

    # The key changes with the row set (filters) and after each save/discard, so
    # edits are never applied to a different row than the one the user edited.
    ids = "|".join(r.employee.employee_id for r in rows)
    digest = hashlib.sha1(ids.encode()).hexdigest()[:8]
    editor_key = f"wb_{key}_{digest}_{st.session_state.get(_version_key(key), 0)}"
    edited = st.data_editor(
        original,
        key=editor_key,
        hide_index=True,
        num_rows="fixed",
        column_order=order,
        column_config=_column_config(company, to_local(now, tz).replace(tzinfo=None)),
        disabled=disabled,
        width="stretch",
    )

    pending, rejected = detect_changes(rows, original, edited)
    if rejected:
        st.warning(
            t("table.rejected_title") + "\n" + "\n".join(f"- {r}" for r in rejected),
            icon=":material/block:",
        )
    left, right, info = st.columns([1, 1, 3])
    if left.button(
        t("table.save"),
        type="primary",
        disabled=not pending,
        key=f"{editor_key}_save",
        icon=":material/save:",
    ):
        ask_confirmation(
            viewer_id, [change.describe() for change in pending], lambda: on_save(pending)
        )
    if right.button(
        t("table.discard"),
        disabled=not (pending or rejected),
        key=f"{editor_key}_discard",
        icon=":material/undo:",
    ):
        reset_table(key)
        st.rerun()
    if pending:
        info.caption(t("table.pending", count=len(pending)))
