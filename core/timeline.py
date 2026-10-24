"""Suggested milestones within a review cycle. Pure Python."""

from __future__ import annotations

from datetime import date, timedelta

from core.models import Action

# Share of the cycle by which each step should be done (assumption: an even pace that
# leaves the last stretch for meetings and confirmations).
MILESTONE_SHARES: tuple[tuple[Action, float], ...] = (
    (Action.INITIATE, 0.15),
    (Action.SUBMIT_SELF, 0.40),
    (Action.SUBMIT_MANAGER, 0.60),
    (Action.SCHEDULE_MEETING, 0.70),
    (Action.ACKNOWLEDGE, 1.00),
)


def suggested_timeline(start: date, due: date) -> list[tuple[Action, date]]:
    """Target date per step. Every target is within [start, due] and in order."""
    days = max((due - start).days, 0)
    return [
        (action, start + timedelta(days=round(days * share))) for action, share in MILESTONE_SHARES
    ]
