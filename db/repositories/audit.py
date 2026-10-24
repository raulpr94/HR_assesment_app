"""Audit log repository (append-only)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import insert, select
from sqlalchemy.orm import Session

from core.models import AuditEntry
from db.schema import audit_log


class AuditRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def add(
        self, entry: AuditEntry, *, actor_id: str, review_id: int | None, now: datetime
    ) -> None:
        self.session.execute(
            insert(audit_log).values(
                timestamp=now,
                actor_id=actor_id,
                review_id=review_id,
                action=entry.action,
                field_changed=entry.field_changed,
                old_value=entry.old_value,
                new_value=entry.new_value,
                reason=entry.reason,
            )
        )

    def list(
        self,
        *,
        review_id: int | None = None,
        actor_id: str | None = None,
        actions: list[str] | None = None,
        limit: int = 500,
    ) -> list[dict[str, object]]:
        stmt = select(audit_log).order_by(audit_log.c.id.desc()).limit(limit)
        if review_id is not None:
            stmt = stmt.where(audit_log.c.review_id == review_id)
        if actor_id:
            stmt = stmt.where(audit_log.c.actor_id == actor_id)
        if actions:
            stmt = stmt.where(audit_log.c.action.in_(actions))
        return [dict(r) for r in self.session.execute(stmt).mappings()]

    def actions(self) -> list[str]:
        """Distinct action names, for filters."""
        stmt = select(audit_log.c.action).distinct().order_by(audit_log.c.action)
        return list(self.session.execute(stmt).scalars())
