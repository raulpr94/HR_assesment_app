"""What happens next in a review, from one person's point of view. Pure Python."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, tzinfo
from enum import Enum

from core.models import Action, Party, ReviewProgress, Status
from core.timeutil import local_date
from core.workflow import NEXT_STEP, derive_status


class NextKind(str, Enum):
    ACT = "act"  # the viewer should do ``action`` now
    WAIT = "wait"  # someone else (``waiting_on``) has to do ``action``
    MEETING = "meeting"  # everyone waits for the meeting date
    DONE = "done"


@dataclass(frozen=True)
class NextStep:
    kind: NextKind
    status: Status
    action: Action | None = None
    waiting_on: Party | None = None


def next_step(
    viewer_id: str,
    reviewer_id: str,
    reviewee_id: str,
    progress: ReviewProgress | None,
    now: datetime,
    tz: tzinfo,
) -> NextStep:
    """The next step of one review as seen by ``viewer_id`` (party or not)."""
    status = derive_status(progress)
    if status is Status.COMPLETED:
        return NextStep(NextKind.DONE, status)

    is_reviewer = viewer_id == reviewer_id
    is_reviewee = viewer_id == reviewee_id

    if status in (Status.MEETING_SCHEDULED, Status.ACKNOWLEDGED):
        assert progress is not None and progress.meeting_at is not None
        if local_date(now, tz) < local_date(progress.meeting_at, tz):
            return NextStep(NextKind.MEETING, status, Action.ACKNOWLEDGE)
        reviewee_done = progress.reviewee_ack_at is not None
        reviewer_done = progress.reviewer_ack_at is not None
        if (is_reviewee and not reviewee_done) or (is_reviewer and not reviewer_done):
            return NextStep(NextKind.ACT, status, Action.ACKNOWLEDGE)
        if not reviewee_done and not reviewer_done:
            missing = Party.EITHER
        else:
            missing = Party.REVIEWER if reviewee_done else Party.REVIEWEE
        return NextStep(NextKind.WAIT, status, Action.ACKNOWLEDGE, missing)

    step = NEXT_STEP[status]
    assert step is not None
    party, action = step
    if (party is Party.REVIEWER and is_reviewer) or (party is Party.REVIEWEE and is_reviewee):
        return NextStep(NextKind.ACT, status, action)
    return NextStep(NextKind.WAIT, status, action, party)
