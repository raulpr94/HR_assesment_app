"""Cached read-only lookups for the UI. ``ui.state.clear_caches()`` resets them after writes.

Arguments are plain ids so they hash cheaply; results are domain dataclasses.
"""

from __future__ import annotations

import streamlit as st

from core.errors import DomainError, ErrorCode
from core.models import Employee, ReviewCycle
from core.permissions import Viewer
from services.overview_service import OverviewRow
from services.review_detail_service import ReviewDetail, ReviewListItem
from ui.state import get_services

SHORT_TTL = 30  # rows include time-dependent "next step" hints


@st.cache_data(ttl=300, show_spinner=False)
def login_options() -> list[Employee]:
    return get_services().directory.login_options()


@st.cache_data(ttl=300, show_spinner=False)
def viewer(employee_id: str) -> Viewer | None:
    return get_services().directory.viewer(employee_id)


@st.cache_data(ttl=SHORT_TTL, show_spinner=False)
def open_cycle() -> ReviewCycle | None:
    return get_services().cycles.get_open_cycle()


@st.cache_data(ttl=SHORT_TTL, show_spinner=False)
def my_review(cycle_id: int, employee_id: str) -> OverviewRow | None:
    me = viewer(employee_id)
    return get_services().overview.my_review(cycle_id, me) if me else None


@st.cache_data(ttl=SHORT_TTL, show_spinner=False)
def team_rows(cycle_id: int, employee_id: str) -> list[OverviewRow]:
    me = viewer(employee_id)
    return get_services().overview.team_rows(cycle_id, me) if me else []


@st.cache_data(ttl=SHORT_TTL, show_spinner=False)
def team_locked(cycle_id: int, employee_id: str) -> bool:
    me = viewer(employee_id)
    return get_services().overview.team_locked(cycle_id, me) if me else False


@st.cache_data(ttl=SHORT_TTL, show_spinner=False)
def company_rows(cycle_id: int, employee_id: str) -> list[OverviewRow]:
    me = viewer(employee_id)
    return get_services().overview.company_rows(cycle_id, me) if me else []


@st.cache_data(ttl=SHORT_TTL, show_spinner=False)
def viewable_reviews(cycle_id: int, employee_id: str) -> list[ReviewListItem]:
    me = viewer(employee_id)
    return get_services().details.viewable(cycle_id, me) if me else []


@st.cache_data(ttl=SHORT_TTL, show_spinner=False)
def review_detail(review_id: int, employee_id: str) -> ReviewDetail:
    """Raises DomainError (not cached) when the review is hidden or missing."""
    me = viewer(employee_id)
    if me is None:
        raise DomainError(ErrorCode.UNKNOWN_EMPLOYEE, employee_id=employee_id)
    return get_services().details.get(review_id, me)
