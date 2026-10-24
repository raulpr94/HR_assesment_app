"""Turn a ``NextStep`` into the words shown to the user."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import tzinfo

from core.models import Action, Level, Party, Status
from core.next_step import NextKind
from core.timeutil import to_local
from services.overview_service import OverviewRow
from ui.texts import t


@dataclass(frozen=True)
class Guidance:
    kind: str  # "act" | "info" | "done"
    text: str
    action: Action | None = None


def _meeting_parts(row: OverviewRow, tz: tzinfo) -> tuple[str, str]:
    if row.progress is None or row.progress.meeting_at is None:
        return "", ""
    local = to_local(row.progress.meeting_at, tz)
    return local.strftime("%d.%m.%Y"), local.strftime("%H:%M")


def my_guidance(row: OverviewRow, tz: tzinfo) -> Guidance:
    """'What's next' for the person being reviewed."""
    manager = row.reviewer.name if row.reviewer else t("next_table.manager")
    step = row.next
    if step.kind is NextKind.DONE:
        return Guidance("done", t("next_mine.done"))
    if step.kind is NextKind.ACT and step.action is not None:
        return Guidance("act", t(f"next_mine.act_{step.action.value}"), step.action)
    if step.kind is NextKind.MEETING:
        day, time = _meeting_parts(row, tz)
        return Guidance("info", t("next_mine.meeting", date=day, time=time))
    if step.status in (Status.MEETING_SCHEDULED, Status.ACKNOWLEDGED):
        return Guidance("info", t("next_mine.wait_other", manager=manager))
    return Guidance("info", t(f"next_mine.{step.status.value}", manager=manager))


def table_next_text(row: OverviewRow, viewer_id: str, tz: tzinfo) -> str:
    """Short 'who: what' text for the Next step column."""
    step = row.next
    if step.kind is NextKind.DONE:
        return t("next_table.done")
    if step.kind is NextKind.MEETING:
        day, time = _meeting_parts(row, tz)
        return t("next_table.meeting", date=f"{day} {time}")
    if step.status is Status.NOT_STARTED and Action.INITIATE not in row.actions:
        reviewer = row.reviewer
        if (
            reviewer
            and reviewer.level is Level.B
            and row.reviewer_own_status is not Status.COMPLETED
        ):
            return t("next_table.locked")
    is_reviewer = row.reviewer is not None and row.reviewer.employee_id == viewer_id
    if step.kind is NextKind.ACT:
        who = t("next_table.you")
    elif step.waiting_on is Party.REVIEWER:
        who = t("next_table.you") if is_reviewer else t("next_table.manager")
    elif step.waiting_on is Party.REVIEWEE:
        who = t("next_table.employee")
    else:
        who = t("next_table.both")
    what = t(f"next_table.{step.action.value}") if step.action else ""
    return f"{who}: {what}"
