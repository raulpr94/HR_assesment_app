"""Domain errors with stable codes, so the UI can show friendly translated text."""

from __future__ import annotations

from enum import Enum
from typing import Any


class ErrorCode(str, Enum):
    UNKNOWN_EMPLOYEE = "unknown_employee"
    EMPLOYEE_INACTIVE = "employee_inactive"
    REVIEW_NOT_FOUND = "review_not_found"
    CYCLE_NOT_FOUND = "cycle_not_found"
    NO_OPEN_CYCLE = "no_open_cycle"
    CYCLE_CLOSED = "cycle_closed"
    CYCLE_ALREADY_OPEN = "cycle_already_open"
    CYCLE_INVALID_DATES = "cycle_invalid_dates"
    CYCLE_NAME_TAKEN = "cycle_name_taken"
    CYCLE_NAME_REQUIRED = "cycle_name_required"
    INVALID_TRANSITION = "invalid_transition"
    ALREADY_INITIATED = "already_initiated"
    NOT_REVIEWABLE = "not_reviewable"
    NO_MANAGER = "no_manager"
    NOT_DIRECT_MANAGER = "not_direct_manager"
    OWN_REVIEW_NOT_COMPLETED = "own_review_not_completed"
    NOT_REVIEWEE = "not_reviewee"
    NOT_REVIEWER = "not_reviewer"
    NOT_A_PARTY = "not_a_party"
    ANSWERS_INCOMPLETE = "answers_incomplete"
    ANSWERS_INVALID = "answers_invalid"
    ANSWERS_LOCKED = "answers_locked"
    CANNOT_EDIT_ANSWERS = "cannot_edit_answers"
    MEETING_REQUIRED = "meeting_required"
    MEETING_IN_PAST = "meeting_in_past"
    MEETING_OUTSIDE_CYCLE = "meeting_outside_cycle"
    MEETING_ALREADY_HELD = "meeting_already_held"
    MEETING_NOT_YET_REACHED = "meeting_not_yet_reached"
    ALREADY_ACKNOWLEDGED = "already_acknowledged"
    HR_ONLY = "hr_only"
    REASON_REQUIRED = "reason_required"
    INCONSISTENT_ROW = "inconsistent_row"
    MISSING_PAYLOAD = "missing_payload"
    REVIEW_HIDDEN = "review_hidden"
    PAGE_FORBIDDEN = "page_forbidden"


# Plain English fallbacks (CLI, logs). The UI uses config/texts_en.yaml.
DEFAULT_MESSAGES: dict[ErrorCode, str] = {
    ErrorCode.UNKNOWN_EMPLOYEE: "Employee {employee_id} was not found.",
    ErrorCode.EMPLOYEE_INACTIVE: "{name} is no longer active.",
    ErrorCode.REVIEW_NOT_FOUND: "Review {review_id} was not found.",
    ErrorCode.CYCLE_NOT_FOUND: "Review cycle {cycle_id} was not found.",
    ErrorCode.NO_OPEN_CYCLE: "There is no open review cycle.",
    ErrorCode.CYCLE_CLOSED: 'The review cycle "{cycle}" is closed.',
    ErrorCode.CYCLE_ALREADY_OPEN: 'Close the open cycle "{cycle}" before opening a new one.',
    ErrorCode.CYCLE_INVALID_DATES: "The due date must be on or after the start date.",
    ErrorCode.CYCLE_NAME_TAKEN: 'A cycle named "{name}" already exists.',
    ErrorCode.CYCLE_NAME_REQUIRED: "Please give the cycle a name.",
    ErrorCode.INVALID_TRANSITION: "Action {action} is not possible at status {status}.",
    ErrorCode.ALREADY_INITIATED: "This review has already been initiated.",
    ErrorCode.NOT_REVIEWABLE: "{name} is not reviewed in this cycle.",
    ErrorCode.NO_MANAGER: "{name} has no manager, so nobody can review them.",
    ErrorCode.NOT_DIRECT_MANAGER: "You can only start reviews for your own direct reports.",
    ErrorCode.OWN_REVIEW_NOT_COMPLETED: (
        "Your own review must be completed before you can start reviews for your team."
    ),
    ErrorCode.NOT_REVIEWEE: "Only the person being reviewed can do this.",
    ErrorCode.NOT_REVIEWER: "Only the reviewing manager can do this.",
    ErrorCode.NOT_A_PARTY: "Only the reviewer or the reviewee can do this.",
    ErrorCode.ANSWERS_INCOMPLETE: "Please answer all required questions first ({missing}).",
    ErrorCode.ANSWERS_INVALID: "Some answers are not valid: {details}",
    ErrorCode.ANSWERS_LOCKED: "These answers have been submitted and can no longer be changed.",
    ErrorCode.CANNOT_EDIT_ANSWERS: "You can't edit these answers at this step.",
    ErrorCode.MEETING_REQUIRED: "Please choose a meeting date and time.",
    ErrorCode.MEETING_IN_PAST: "The meeting must be in the future.",
    ErrorCode.MEETING_OUTSIDE_CYCLE: "The meeting must be between {start} and {due}.",
    ErrorCode.MEETING_ALREADY_HELD: "The meeting has already taken place and can't be moved.",
    ErrorCode.MEETING_NOT_YET_REACHED: "You can confirm from the meeting date ({date}) onward.",
    ErrorCode.ALREADY_ACKNOWLEDGED: "You have already confirmed this review.",
    ErrorCode.HR_ONLY: "Only HR can do this.",
    ErrorCode.REASON_REQUIRED: "Please give a reason for the override.",
    ErrorCode.INCONSISTENT_ROW: "Review {review_id} has steps filled out of order ({detail}).",
    ErrorCode.MISSING_PAYLOAD: "Missing information: {field}.",
    ErrorCode.REVIEW_HIDDEN: "You don't have access to this review.",
    ErrorCode.PAGE_FORBIDDEN: "You don't have access to this page.",
}


class DomainError(Exception):
    """A business-rule violation. Carries a code plus parameters for the message."""

    def __init__(self, code: ErrorCode, **params: Any) -> None:
        self.code = code
        self.params = params
        super().__init__(self.format(DEFAULT_MESSAGES.get(code, code.value)))

    def format(self, template: str) -> str:
        """Fill a message template with this error's parameters (missing keys stay visible)."""
        try:
            return template.format(**self.params)
        except (KeyError, IndexError, ValueError):
            return template

    @property
    def message(self) -> str:
        return str(self)
