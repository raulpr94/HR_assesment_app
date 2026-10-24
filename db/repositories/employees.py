"""Employee repository."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import datetime

from sqlalchemy import RowMapping, insert, select, update
from sqlalchemy.orm import Session

from core.models import Employee, Level
from db.schema import employees


def _to_employee(row: RowMapping) -> Employee:
    return Employee(
        employee_id=row["employee_id"],
        name=row["name"],
        email=row["email"],
        level=Level(row["level"]),
        role_title=row["role_title"] or "",
        department=row["department"] or "",
        manager_id=row["manager_id"],
        is_hr_admin=bool(row["is_hr_admin"]),
        active=bool(row["active"]),
    )


class EmployeeRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, employee_id: str) -> Employee | None:
        row = (
            self.session.execute(select(employees).where(employees.c.employee_id == employee_id))
            .mappings()
            .first()
        )
        return _to_employee(row) if row else None

    def get_many(self, ids: Iterable[str]) -> dict[str, Employee]:
        id_list = list(set(ids))
        if not id_list:
            return {}
        rows = self.session.execute(
            select(employees).where(employees.c.employee_id.in_(id_list))
        ).mappings()
        return {r["employee_id"]: _to_employee(r) for r in rows}

    def list(self, *, active_only: bool = True) -> list[Employee]:
        stmt = select(employees).order_by(employees.c.name)
        if active_only:
            stmt = stmt.where(employees.c.active.is_(True))
        return [_to_employee(r) for r in self.session.execute(stmt).mappings()]

    def direct_reports(self, manager_id: str, *, active_only: bool = True) -> list[Employee]:
        stmt = select(employees).where(employees.c.manager_id == manager_id)
        if active_only:
            stmt = stmt.where(employees.c.active.is_(True))
        return [
            _to_employee(r)
            for r in self.session.execute(stmt.order_by(employees.c.name)).mappings()
        ]

    def list_by_level(self, level: Level) -> list[Employee]:
        stmt = select(employees).where(
            employees.c.level == level.value, employees.c.active.is_(True)
        )
        return [_to_employee(r) for r in self.session.execute(stmt).mappings()]

    def count(self) -> int:
        return len(self.session.execute(select(employees.c.employee_id)).all())

    def upsert_many(self, records: Sequence[Employee], now: datetime) -> tuple[int, int]:
        """Insert new employees and update existing ones by id. Returns (inserted, updated)."""
        existing = set(self.session.execute(select(employees.c.employee_id)).scalars())
        inserted = updated = 0
        # Emails are unique. Park every stored email that the import is about to use
        # (people swapping addresses, or a newcomer reusing a leaver's address) on a
        # placeholder first; rows in the file get their real address back below.
        incoming = [r.email for r in records]
        if incoming:
            self.session.execute(
                update(employees)
                .where(employees.c.email.in_(incoming))
                .values(email=employees.c.employee_id + "@reassigned.invalid")
            )
        for rec in records:
            values = {
                "name": rec.name,
                "email": rec.email,
                "level": rec.level.value,
                "role_title": rec.role_title,
                "department": rec.department,
                "manager_id": rec.manager_id,
                "is_hr_admin": rec.is_hr_admin,
                "active": True,
                "updated_at": now,
            }
            if rec.employee_id in existing:
                self.session.execute(
                    update(employees)
                    .where(employees.c.employee_id == rec.employee_id)
                    .values(**values)
                )
                updated += 1
            else:
                self.session.execute(
                    insert(employees).values(employee_id=rec.employee_id, **values)
                )
                inserted += 1
        return inserted, updated

    def deactivate_missing(self, keep_ids: Iterable[str], now: datetime) -> int:
        """Mark employees not in ``keep_ids`` inactive. Returns how many changed."""
        keep = list(keep_ids)
        result = self.session.execute(
            update(employees)
            .where(employees.c.employee_id.not_in(keep), employees.c.active.is_(True))
            .values(active=False, updated_at=now)
        )
        return int(result.rowcount or 0)
