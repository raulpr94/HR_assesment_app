"""Domain dataclasses and enums. Pure Python: no Streamlit, no SQL."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from typing import Any


class Level(str, Enum):
    A = "A"  # employee
    B = "B"  # line manager
    C = "C"  # department manager / HR director


class Status(str, Enum):
    """Review status. Never stored: always derived from the filled columns."""

    NOT_STARTED = "NOT_STARTED"
    INITIATED = "INITIATED"
    SELF_ASSESSMENT_SUBMITTED = "SELF_ASSESSMENT_SUBMITTED"
    MANAGER_ASSESSMENT_SUBMITTED = "MANAGER_ASSESSMENT_SUBMITTED"
    MEETING_SCHEDULED = "MEETING_SCHEDULED"
    ACKNOWLEDGED = "ACKNOWLEDGED"  # at least one party has acknowledged
    COMPLETED = "COMPLETED"


class Action(str, Enum):
    INITIATE = "initiate"
    SUBMIT_SELF = "submit_self"
    SUBMIT_MANAGER = "submit_manager"
    SCHEDULE_MEETING = "schedule_meeting"
    ACKNOWLEDGE = "acknowledge"
    HR_ADVANCE = "hr_advance"
    HR_REVERT = "hr_revert"


class Party(str, Enum):
    """Who may trigger a transition."""

    REVIEWER = "reviewer"
    REVIEWEE = "reviewee"
    EITHER = "either"  # reviewer or reviewee
    REVIEWER_OR_HR = "reviewer_or_hr"
    HR = "hr"


class RespondentRole(str, Enum):
    SELF = "self"
    MANAGER = "manager"


class CycleStatus(str, Enum):
    OPEN = "open"
    CLOSED = "closed"


class NotificationType(str, Enum):
    """Each value is also the name of its Jinja2 template in templates/."""

    CYCLE_STARTED = "cycle_started"
    SELF_ASSESSMENT_REQUESTED = "self_assessment_requested"
    SELF_ASSESSMENT_READY = "self_assessment_ready"
    MEETING_INVITE = "meeting_invite"
    REVIEW_COMPLETED = "review_completed"


class NotificationStatus(str, Enum):
    PENDING = "pending"
    SENT = "sent"
    FAILED = "failed"
    SKIPPED = "skipped"


class Page(str, Enum):
    OVERVIEW = "overview"
    REVIEW = "review"
    GUIDE = "guide"
    DASHBOARD = "dashboard"
    ADMIN = "admin"


class RevealPolicy(str, Enum):
    """When the reviewee may see the manager's answers."""

    MEETING_DATE = "meeting_date"  # from the (local) meeting day onward
    MEETING_TIME = "meeting_time"  # from the meeting start time onward
    COMPLETED = "completed"  # only once the review is completed


@dataclass(frozen=True)
class Employee:
    employee_id: str
    name: str
    email: str
    level: Level
    role_title: str = ""
    department: str = ""
    manager_id: str | None = None
    is_hr_admin: bool = False
    active: bool = True


@dataclass(frozen=True)
class ReviewCycle:
    cycle_id: int
    name: str
    start_date: date
    due_date: date
    status: CycleStatus = CycleStatus.OPEN
    created_by: str | None = None
    created_at: datetime | None = None


@dataclass
class ReviewProgress:
    """One row of the ``review_progress`` writeback table.

    All datetimes are naive UTC. Each workflow step fills its own column(s).
    """

    review_id: int | None
    cycle_id: int
    reviewer_id: str
    reviewee_id: str
    survey_version: str = ""
    initiated_at: datetime | None = None
    initiated_by: str | None = None
    self_submitted_at: datetime | None = None
    manager_submitted_at: datetime | None = None
    meeting_at: datetime | None = None
    meeting_set_by: str | None = None
    reviewee_ack_at: datetime | None = None
    reviewer_ack_at: datetime | None = None
    completed_at: datetime | None = None
    updated_at: datetime | None = None
    updated_by: str | None = None


@dataclass(frozen=True)
class Answer:
    question_id: str
    value: str | None
    is_draft: bool = True
    submitted_at: datetime | None = None


@dataclass(frozen=True)
class AuditEntry:
    action: str
    field_changed: str | None = None
    old_value: str | None = None
    new_value: str | None = None
    reason: str | None = None


@dataclass(frozen=True)
class NotificationIntent:
    """A notification the workflow wants sent. The service renders and queues it."""

    type: NotificationType
    recipient_ids: tuple[str, ...]
    context: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ValidationIssue:
    """A problem found while validating imported data."""

    message: str
    row: int | None = None  # 1-based data row in the file (header is row 1)
    employee_id: str | None = None
    field: str | None = None

    def __str__(self) -> str:
        where = []
        if self.row is not None:
            where.append(f"row {self.row}")
        if self.employee_id:
            where.append(f"id {self.employee_id}")
        if self.field:
            where.append(f"column {self.field}")
        prefix = f"[{', '.join(where)}] " if where else ""
        return f"{prefix}{self.message}"
