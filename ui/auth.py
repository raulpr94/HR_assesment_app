"""Authentication behind an interface.

Today: a simulated "View as" dropdown. Later: a real provider (SSO, OIDC) that
implements the same protocol, chosen in ``get_auth_provider`` from settings.
Pages never touch this module; they only see the current Viewer.
"""

from __future__ import annotations

from typing import Protocol

import streamlit as st

from config.settings import Settings
from core.models import Employee
from services.directory_service import DirectoryService
from ui.queries import login_options
from ui.state import VIEW_AS_KEY
from ui.texts import t


class AuthProvider(Protocol):
    def render_controls(self) -> None:
        """Draw any login UI (e.g. in the sidebar)."""
        ...

    def current_employee_id(self) -> str | None:
        """The logged-in employee's id, or None."""
        ...


def _label(emp: Employee) -> str:
    prefix = "HR" if emp.is_hr_admin else emp.level.value
    title = f" — {emp.role_title}" if emp.role_title else ""
    return f"{prefix} · {emp.name}{title}"


class SimulatedAuthProvider:
    """Pick any employee from a searchable dropdown, grouped by level (HR, C, B, A)."""

    def __init__(self, directory: DirectoryService) -> None:
        self.directory = directory

    def render_controls(self) -> None:
        people = login_options()
        if not people:
            st.sidebar.info(t("auth.no_employees"))
            st.session_state.pop(VIEW_AS_KEY, None)
            return
        by_id = {e.employee_id: e for e in people}
        if st.session_state.get(VIEW_AS_KEY) not in by_id:
            st.session_state[VIEW_AS_KEY] = people[0].employee_id
        st.sidebar.selectbox(
            t("auth.view_as"),
            options=list(by_id),
            format_func=lambda emp_id: _label(by_id[emp_id]),
            key=VIEW_AS_KEY,
            help=t("auth.view_as_help"),
        )

    def current_employee_id(self) -> str | None:
        return st.session_state.get(VIEW_AS_KEY)


def get_auth_provider(settings: Settings, directory: DirectoryService) -> AuthProvider:
    # Only "simulated" exists today; settings validation rejects anything else.
    return SimulatedAuthProvider(directory)
