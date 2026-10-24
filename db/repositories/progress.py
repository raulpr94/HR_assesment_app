"""Repository for the ``review_progress`` writeback table."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import fields

from sqlalchemy import RowMapping, delete, insert, select, update
from sqlalchemy.orm import Session

from core.models import ReviewProgress
from core.workflow import PROGRESS_COLUMNS, apply_changes, check_column_order
from db.schema import review_progress

_FIELDS = tuple(f.name for f in fields(ReviewProgress))
_WRITABLE = frozenset(PROGRESS_COLUMNS) | {"updated_at", "updated_by"}


def _to_progress(row: RowMapping) -> ReviewProgress:
    return ReviewProgress(**{name: row[name] for name in _FIELDS})


class ProgressRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, review_id: int) -> ReviewProgress | None:
        row = (
            self.session.execute(
                select(review_progress).where(review_progress.c.review_id == review_id)
            )
            .mappings()
            .first()
        )
        return _to_progress(row) if row else None

    def get_for(self, cycle_id: int, reviewee_id: str) -> ReviewProgress | None:
        row = (
            self.session.execute(
                select(review_progress).where(
                    review_progress.c.cycle_id == cycle_id,
                    review_progress.c.reviewee_id == reviewee_id,
                )
            )
            .mappings()
            .first()
        )
        return _to_progress(row) if row else None

    def list_for_cycle(
        self, cycle_id: int, reviewee_ids: Iterable[str] | None = None
    ) -> dict[str, ReviewProgress]:
        """Rows of a cycle keyed by reviewee_id (optionally limited to some reviewees)."""
        stmt = select(review_progress).where(review_progress.c.cycle_id == cycle_id)
        if reviewee_ids is not None:
            stmt = stmt.where(review_progress.c.reviewee_id.in_(list(reviewee_ids)))
        return {r["reviewee_id"]: _to_progress(r) for r in self.session.execute(stmt).mappings()}

    def count(self) -> int:
        return len(self.session.execute(select(review_progress.c.review_id)).all())

    def insert(self, progress: ReviewProgress) -> int:
        check_column_order(progress)
        values = {name: getattr(progress, name) for name in _FIELDS if name != "review_id"}
        result = self.session.execute(insert(review_progress).values(**values))
        return int(result.inserted_primary_key[0])

    def update(self, review_id: int, changes: Mapping[str, object]) -> ReviewProgress:
        """Apply column changes. Columns must stay filled in order (checked here too)."""
        unknown = set(changes) - _WRITABLE
        if unknown:
            raise ValueError(f"Not writable progress columns: {sorted(unknown)}")
        current = self.get(review_id)
        if current is None:
            raise LookupError(f"review {review_id} not found")
        updated = apply_changes(current, dict(changes))
        check_column_order(updated)
        self.session.execute(
            update(review_progress)
            .where(review_progress.c.review_id == review_id)
            .values(**changes)
        )
        return updated

    def delete(self, review_id: int) -> None:
        self.session.execute(
            delete(review_progress).where(review_progress.c.review_id == review_id)
        )
