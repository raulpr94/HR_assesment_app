"""derive_status: the single source of truth for a review's status."""

from __future__ import annotations

from datetime import datetime

import pytest

from core.errors import DomainError, ErrorCode
from core.models import ReviewProgress, Status
from core.workflow import check_column_order, derive_status, step_index

T = datetime(2026, 10, 1, 8, 0)


def row(**filled: object) -> ReviewProgress:
    return ReviewProgress(review_id=1, cycle_id=1, reviewer_id="C1", reviewee_id="B1", **filled)


def test_no_row_means_not_started() -> None:
    assert derive_status(None) is Status.NOT_STARTED


@pytest.mark.parametrize(
    ("filled", "expected"),
    [
        ({}, Status.NOT_STARTED),
        ({"initiated_at": T}, Status.INITIATED),
        ({"initiated_at": T, "self_submitted_at": T}, Status.SELF_ASSESSMENT_SUBMITTED),
        (
            {"initiated_at": T, "self_submitted_at": T, "manager_submitted_at": T},
            Status.MANAGER_ASSESSMENT_SUBMITTED,
        ),
        (
            {"initiated_at": T, "self_submitted_at": T, "manager_submitted_at": T, "meeting_at": T},
            Status.MEETING_SCHEDULED,
        ),
        (
            {
                "initiated_at": T,
                "self_submitted_at": T,
                "manager_submitted_at": T,
                "meeting_at": T,
                "reviewee_ack_at": T,
            },
            Status.ACKNOWLEDGED,
        ),
        (
            {
                "initiated_at": T,
                "self_submitted_at": T,
                "manager_submitted_at": T,
                "meeting_at": T,
                "reviewer_ack_at": T,
            },
            Status.ACKNOWLEDGED,
        ),
        (
            {
                "initiated_at": T,
                "self_submitted_at": T,
                "manager_submitted_at": T,
                "meeting_at": T,
                "reviewee_ack_at": T,
                "reviewer_ack_at": T,
            },
            Status.ACKNOWLEDGED,  # both acks but completion not yet recorded
        ),
        (
            {
                "initiated_at": T,
                "self_submitted_at": T,
                "manager_submitted_at": T,
                "meeting_at": T,
                "reviewee_ack_at": T,
                "reviewer_ack_at": T,
                "completed_at": T,
            },
            Status.COMPLETED,
        ),
    ],
)
def test_status_follows_filled_columns(filled: dict, expected: Status) -> None:
    assert derive_status(row(**filled)) is expected


@pytest.mark.parametrize(
    "filled",
    [
        {"self_submitted_at": T},  # self-assessment without initiation
        {"initiated_at": T, "manager_submitted_at": T},  # skipped the self-assessment
        {"initiated_at": T, "self_submitted_at": T, "meeting_at": T},  # meeting w/o manager
        {
            "initiated_at": T,
            "self_submitted_at": T,
            "manager_submitted_at": T,
            "reviewee_ack_at": T,
        },  # ack without meeting
        {
            "initiated_at": T,
            "self_submitted_at": T,
            "manager_submitted_at": T,
            "meeting_at": T,
            "reviewee_ack_at": T,
            "completed_at": T,
        },  # one ack only
    ],
)
def test_out_of_order_rows_are_rejected(filled: dict) -> None:
    with pytest.raises(DomainError) as err:
        derive_status(row(**filled))
    assert err.value.code is ErrorCode.INCONSISTENT_ROW
    with pytest.raises(DomainError):
        check_column_order(row(**filled))


def test_step_index_orders_the_process() -> None:
    order = [step_index(s) for s in Status]
    assert order == sorted(order) == list(range(len(Status)))
