"""Review cycle repository."""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import RowMapping, insert, select, update
from sqlalchemy.orm import Session

from core.models import CycleStatus, ReviewCycle
from db.schema import review_cycles


def _to_cycle(row: RowMapping) -> ReviewCycle:
    return ReviewCycle(
        cycle_id=row["cycle_id"],
        name=row["name"],
        start_date=row["start_date"],
        due_date=row["due_date"],
        status=CycleStatus(row["status"]),
        created_by=row["created_by"],
        created_at=row["created_at"],
    )


class CycleRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, cycle_id: int) -> ReviewCycle | None:
        row = (
            self.session.execute(select(review_cycles).where(review_cycles.c.cycle_id == cycle_id))
            .mappings()
            .first()
        )
        return _to_cycle(row) if row else None

    def get_by_name(self, name: str) -> ReviewCycle | None:
        row = (
            self.session.execute(select(review_cycles).where(review_cycles.c.name == name))
            .mappings()
            .first()
        )
        return _to_cycle(row) if row else None

    def get_open(self) -> ReviewCycle | None:
        row = (
            self.session.execute(
                select(review_cycles)
                .where(review_cycles.c.status == CycleStatus.OPEN.value)
                .order_by(review_cycles.c.cycle_id.desc())
            )
            .mappings()
            .first()
        )
        return _to_cycle(row) if row else None

    def list(self) -> list[ReviewCycle]:
        rows = self.session.execute(
            select(review_cycles).order_by(review_cycles.c.cycle_id.desc())
        ).mappings()
        return [_to_cycle(r) for r in rows]

    def add(
        self, name: str, start_date: date, due_date: date, created_by: str | None, now: datetime
    ) -> ReviewCycle:
        result = self.session.execute(
            insert(review_cycles).values(
                name=name,
                start_date=start_date,
                due_date=due_date,
                status=CycleStatus.OPEN.value,
                created_by=created_by,
                created_at=now,
            )
        )
        cycle_id = int(result.inserted_primary_key[0])
        cycle = self.get(cycle_id)
        assert cycle is not None
        return cycle

    def set_status(self, cycle_id: int, status: CycleStatus, now: datetime) -> None:
        values: dict[str, object] = {"status": status.value}
        values["closed_at"] = now if status is CycleStatus.CLOSED else None
        self.session.execute(
            update(review_cycles).where(review_cycles.c.cycle_id == cycle_id).values(**values)
        )
