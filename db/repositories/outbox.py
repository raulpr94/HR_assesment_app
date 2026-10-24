"""Notification outbox repository (outbox pattern)."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from sqlalchemy import func, insert, select, update
from sqlalchemy.orm import Session

from core.models import NotificationStatus
from db.schema import notification_outbox


class OutboxRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def add(
        self,
        *,
        type: str,
        recipient_email: str,
        subject: str,
        body: str,
        payload: dict[str, Any],
        now: datetime,
    ) -> int:
        result = self.session.execute(
            insert(notification_outbox).values(
                type=type,
                recipient_email=recipient_email,
                subject=subject,
                body=body,
                payload_json=json.dumps(payload, default=str, ensure_ascii=False),
                status=NotificationStatus.PENDING.value,
                attempts=0,
                created_at=now,
            )
        )
        return int(result.inserted_primary_key[0])

    def list(
        self, *, status: NotificationStatus | None = None, limit: int = 500
    ) -> list[dict[str, Any]]:
        stmt = select(notification_outbox).order_by(notification_outbox.c.id.desc()).limit(limit)
        if status is not None:
            stmt = stmt.where(notification_outbox.c.status == status.value)
        return [dict(r) for r in self.session.execute(stmt).mappings()]

    def get(self, message_id: int) -> dict[str, Any] | None:
        row = (
            self.session.execute(
                select(notification_outbox).where(notification_outbox.c.id == message_id)
            )
            .mappings()
            .first()
        )
        return dict(row) if row else None

    def pending(self, limit: int = 100) -> list[dict[str, Any]]:
        stmt = (
            select(notification_outbox)
            .where(notification_outbox.c.status == NotificationStatus.PENDING.value)
            .order_by(notification_outbox.c.id)
            .limit(limit)
        )
        return [dict(r) for r in self.session.execute(stmt).mappings()]

    def mark(
        self,
        message_id: int,
        status: NotificationStatus,
        now: datetime,
        error: str | None = None,
    ) -> None:
        values: dict[str, Any] = {
            "status": status.value,
            "last_error": error,
            "attempts": notification_outbox.c.attempts + 1,
        }
        if status is NotificationStatus.SENT:
            values["sent_at"] = now
        self.session.execute(
            update(notification_outbox)
            .where(notification_outbox.c.id == message_id)
            .values(**values)
        )

    def requeue_failed(self) -> int:
        result = self.session.execute(
            update(notification_outbox)
            .where(notification_outbox.c.status == NotificationStatus.FAILED.value)
            .values(status=NotificationStatus.PENDING.value)
        )
        return int(result.rowcount or 0)

    def counts(self) -> dict[str, int]:
        rows = self.session.execute(
            select(notification_outbox.c.status, func.count()).group_by(
                notification_outbox.c.status
            )
        ).all()
        return {status: int(n) for status, n in rows}
