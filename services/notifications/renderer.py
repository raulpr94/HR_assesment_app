"""Render notification templates and queue them in the outbox.

Templates live in templates/<notification type>.txt.j2. The first line must be
``Subject: ...``; the rest is the plain-text body.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from config.settings import Settings
from core.models import Employee, NotificationIntent, NotificationType, ReviewCycle, ReviewProgress
from core.timeutil import format_date, to_local
from db.repositories import Repositories

SUBJECT_PREFIX = "Subject:"


@dataclass(frozen=True)
class RenderedMessage:
    subject: str
    body: str


class TemplateError(ValueError):
    """A notification template is missing its subject line."""


class NotificationRenderer:
    def __init__(self, templates_dir: Path) -> None:
        self.env = Environment(
            loader=FileSystemLoader(str(templates_dir)),
            undefined=StrictUndefined,  # a typo in a template fails loudly in tests
            autoescape=False,  # plain-text emails
            trim_blocks=True,
            lstrip_blocks=True,
        )

    def render(self, kind: NotificationType, context: Mapping[str, Any]) -> RenderedMessage:
        text = self.env.get_template(f"{kind.value}.txt.j2").render(**context)
        first, _, rest = text.lstrip().partition("\n")
        if not first.startswith(SUBJECT_PREFIX):
            raise TemplateError(f"Template {kind.value} must start with '{SUBJECT_PREFIX}'")
        return RenderedMessage(
            subject=first[len(SUBJECT_PREFIX) :].strip(), body=rest.strip() + "\n"
        )


def _person(emp: Employee | None) -> dict[str, str]:
    if emp is None:
        return {"id": "", "name": "", "first_name": "", "email": ""}
    return {
        "id": emp.employee_id,
        "name": emp.name,
        "first_name": emp.name.split()[0] if emp.name else "",
        "email": emp.email,
    }


def _ics_time(value_utc: datetime) -> str:
    return value_utc.strftime("%Y%m%dT%H%M%SZ")


def build_ics(
    *,
    review_id: int | None,
    start_utc: datetime,
    duration_minutes: int,
    summary: str,
    organizer: Employee,
    attendees: Iterable[Employee],
    now: datetime,
) -> str:
    """A minimal iCalendar invite. Re-sending with the same UID updates the event."""
    end_utc = start_utc + timedelta(minutes=duration_minutes)
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Performance Review Assistant//EN",
        "METHOD:REQUEST",
        "BEGIN:VEVENT",
        f"UID:review-{review_id}@perf-review",
        f"SEQUENCE:{int(now.timestamp()) // 60}",
        f"DTSTAMP:{_ics_time(now)}",
        f"DTSTART:{_ics_time(start_utc)}",
        f"DTEND:{_ics_time(end_utc)}",
        f"SUMMARY:{summary}",
        f"ORGANIZER;CN={organizer.name}:mailto:{organizer.email}",
        *(
            f"ATTENDEE;CN={a.name};ROLE=REQ-PARTICIPANT;RSVP=TRUE:mailto:{a.email}"
            for a in attendees
        ),
        "END:VEVENT",
        "END:VCALENDAR",
    ]
    return "\r\n".join(lines) + "\r\n"


class NotificationQueue:
    """Turns workflow NotificationIntents into outbox rows (one per recipient)."""

    def __init__(self, renderer: NotificationRenderer, settings: Settings) -> None:
        self.renderer = renderer
        self.settings = settings

    def enqueue(
        self,
        repos: Repositories,
        intents: Iterable[NotificationIntent],
        *,
        cycle: ReviewCycle,
        progress: ReviewProgress | None,
        people: Mapping[str, Employee],
        now: datetime,
    ) -> int:
        queued = 0
        for intent in intents:
            for recipient_id in intent.recipient_ids:
                recipient = people.get(recipient_id)
                if recipient is None:
                    continue
                context = self.context(intent, recipient, cycle, progress, people, now)
                message = self.renderer.render(intent.type, context)
                payload = {
                    "type": intent.type.value,
                    "recipient": _person(recipient),
                    "cycle": context["cycle"],
                    "review_id": progress.review_id if progress else None,
                    "reviewer": context["reviewer"],
                    "reviewee": context["reviewee"],
                    "meeting": context["meeting"],
                    "app_url": context["app_url"],
                }
                repos.outbox.add(
                    type=intent.type.value,
                    recipient_email=recipient.email,
                    subject=message.subject,
                    body=message.body,
                    payload=payload,
                    now=now,
                )
                queued += 1
        return queued

    def context(
        self,
        intent: NotificationIntent,
        recipient: Employee,
        cycle: ReviewCycle,
        progress: ReviewProgress | None,
        people: Mapping[str, Employee],
        now: datetime,
    ) -> dict[str, Any]:
        tz = self.settings.tz
        reviewer = people.get(progress.reviewer_id) if progress else None
        reviewee = people.get(progress.reviewee_id) if progress else None
        meeting: dict[str, Any] | None = None
        if progress and progress.meeting_at and reviewer and reviewee:
            local = to_local(progress.meeting_at, tz)
            duration = self.settings.meeting_duration_minutes
            meeting = {
                "date": local.strftime("%d.%m.%Y"),
                "time": local.strftime("%H:%M"),
                "timezone": self.settings.timezone,
                "duration_minutes": duration,
                "start_utc": progress.meeting_at.isoformat() + "Z",
                "end_utc": (progress.meeting_at + timedelta(minutes=duration)).isoformat() + "Z",
                "participants": [_person(reviewer), _person(reviewee)],
                "ics": build_ics(
                    review_id=progress.review_id,
                    start_utc=progress.meeting_at,
                    duration_minutes=duration,
                    summary=f"Performance review: {reviewee.name}",
                    organizer=reviewer,
                    attendees=(reviewer, reviewee),
                    now=now,
                ),
            }
        app_url = self.settings.app_base_url
        return {
            "recipient": _person(recipient),
            "reviewer": _person(reviewer),
            "reviewee": _person(reviewee),
            "is_reviewer": reviewer is not None and recipient.employee_id == reviewer.employee_id,
            "cycle": {
                "id": cycle.cycle_id,
                "name": cycle.name,
                "start": format_date(cycle.start_date),
                "due": format_date(cycle.due_date),
            },
            "meeting": meeting,
            "app_url": app_url,
            "review_url": f"{app_url}/review?review_id={progress.review_id}"
            if progress
            else app_url,
            **intent.context,
        }
