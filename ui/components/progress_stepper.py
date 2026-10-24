"""Horizontal step indicator for one review."""

from __future__ import annotations

import streamlit as st

from core.models import Status
from core.workflow import STEP_ORDER, step_index
from ui.components.status_badge import STATUS_STYLE
from ui.texts import t

STEPS: tuple[Status, ...] = STEP_ORDER[1:]  # Initiated … Completed


def step_states(status: Status) -> list[tuple[Status, str]]:
    """Each step with 'done', 'current' or 'todo'.

    ACKNOWLEDGED means only one side has confirmed, so that step is still current.
    """
    reached = step_index(status)
    states: list[tuple[Status, str]] = []
    current_given = False
    for step in STEPS:
        idx = step_index(step)
        done = idx < reached or (idx == reached and status is not Status.ACKNOWLEDGED)
        if done:
            states.append((step, "done"))
        elif not current_given:
            states.append((step, "current"))
            current_given = True
        else:
            states.append((step, "todo"))
    return states


def progress_stepper(status: Status) -> None:
    columns = st.columns(len(STEPS))
    for column, (step, state) in zip(columns, step_states(status), strict=True):
        if state == "done":
            icon, colour = ":material/check_circle:", "green"
        elif state == "current":
            icon, colour = STATUS_STYLE[step][0], "blue"
        else:
            icon, colour = ":material/radio_button_unchecked:", "gray"
        label = t(f"steps.{step.value}")
        column.markdown(f":{colour}[{icon} **{label}**]  \n:{colour}[{t(f'steps.{state}')}]")
