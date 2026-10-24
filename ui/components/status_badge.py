"""The one central mapping of status → label, icon and colour, used on every page."""

from __future__ import annotations

from core.models import Status
from ui.texts import t

# (Material icon for markdown, colour, plain-text symbol for tables).
# Icon and text always accompany the colour, so nothing relies on colour alone.
STATUS_STYLE: dict[Status, tuple[str, str, str]] = {
    Status.NOT_STARTED: (":material/radio_button_unchecked:", "gray", "○"),
    Status.INITIATED: (":material/play_circle:", "blue", "▶"),
    Status.SELF_ASSESSMENT_SUBMITTED: (":material/person_check:", "violet", "✎"),
    Status.MANAGER_ASSESSMENT_SUBMITTED: (":material/rate_review:", "orange", "✎✎"),
    Status.MEETING_SCHEDULED: (":material/event:", "orange", "📅"),
    Status.ACKNOWLEDGED: (":material/how_to_reg:", "green", "◐"),
    Status.COMPLETED: (":material/check_circle:", "green", "✔"),
}


def status_label(status: Status) -> str:
    return t(f"status.{status.value}")


def status_badge(status: Status) -> str:
    """Markdown for an inline coloured badge with icon and label."""
    icon, colour, _ = STATUS_STYLE[status]
    return f":{colour}-badge[{icon} {status_label(status)}]"


def status_text(status: Status) -> str:
    """Plain text with a symbol, for table cells (which can't render markdown)."""
    return f"{STATUS_STYLE[status][2]} {status_label(status)}"
