"""People lookups: login options and the current user's reporting line."""

from __future__ import annotations

from sqlalchemy.orm import Session, sessionmaker

from core.hierarchy import children_map, descendants
from core.models import Employee, Level
from core.permissions import Viewer
from db.repositories import Repositories

_LEVEL_ORDER = {Level.C: 0, Level.B: 1, Level.A: 2}


class DirectoryService:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._sessions = session_factory

    def list_people(self) -> list[Employee]:
        with self._sessions() as session:
            return Repositories.for_session(session).employees.list()

    def login_options(self) -> list[Employee]:
        """Active employees: HR first, then C, B, A, each alphabetical."""
        people = self.list_people()
        return sorted(
            people, key=lambda e: (not e.is_hr_admin, _LEVEL_ORDER[e.level], e.name.lower())
        )

    def get(self, employee_id: str) -> Employee | None:
        with self._sessions() as session:
            return Repositories.for_session(session).employees.get(employee_id)

    def viewer(self, employee_id: str) -> Viewer | None:
        """The employee plus direct reports and full subtree (active people only)."""
        people = self.list_people()
        me = next((e for e in people if e.employee_id == employee_id), None)
        if me is None:
            return None
        children = children_map(people)
        return Viewer(
            employee=me,
            direct_report_ids=frozenset(children.get(employee_id, ())),
            subtree_ids=frozenset(descendants(children, employee_id)),
        )
