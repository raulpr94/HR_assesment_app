"""Plain-language description of an HR override, built from a transition preview."""

from __future__ import annotations

from core.models import Action, RespondentRole
from core.workflow import TransitionOutcome
from ui.components.status_badge import status_label
from ui.texts import t

COLUMN_LABELS = {
    "initiated_at": "table.initiated",
    "self_submitted_at": "table.self_submitted",
    "manager_submitted_at": "table.manager_submitted",
    "meeting_at": "table.meeting",
    "reviewee_ack_at": "table.reviewee_ack",
    "reviewer_ack_at": "table.reviewer_ack",
    "completed_at": "table.completed",
}


def describe_override(outcome: TransitionOutcome, name: str) -> list[str]:
    """Sentences stating exactly what the override will change."""
    key = "hr.preview_forward" if outcome.action is Action.HR_ADVANCE else "hr.preview_back"
    lines = [
        t(
            key,
            name=name,
            old=status_label(outcome.old_status),
            new=status_label(outcome.new_status),
        )
    ]
    if outcome.delete_row:
        lines.append(t("hr.preview_deleted"))
        return lines
    cleared = [
        t(COLUMN_LABELS[col])
        for col, value in outcome.changes.items()
        if value is None and col in COLUMN_LABELS
    ]
    if cleared:
        lines.append(t("hr.preview_cleared", fields=", ".join(cleared)))
    if outcome.unlock_answers:
        sides = [
            t("hr.side_self") if side is RespondentRole.SELF else t("hr.side_manager")
            for side in outcome.unlock_answers
        ]
        lines.append(t("hr.preview_unlocked", sides=", ".join(sides)))
    recipients = sum(len(n.recipient_ids) for n in outcome.notifications)
    if recipients:
        lines.append(t("hr.preview_notifications", count=recipients))
    return lines
