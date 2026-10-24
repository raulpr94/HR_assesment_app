"""Notification sender protocol and the outbox processor.

Workflow transitions only INSERT rows into ``notification_outbox``. A sender
(dry-run or Zapier) is chosen from config and processes pending rows separately.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Protocol

from sqlalchemy.orm import Session, sessionmaker

from core.models import NotificationStatus
from db.repositories import OutboxRepository
from services.clock import Clock

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class OutboxMessage:
    id: int
    type: str
    recipient_email: str
    subject: str
    body: str
    payload: dict[str, Any]


@dataclass(frozen=True)
class SendResult:
    status: NotificationStatus
    error: str | None = None


class NotificationSender(Protocol):
    name: str

    def send(self, message: OutboxMessage) -> SendResult: ...


@dataclass
class ProcessReport:
    processed: int = 0
    counts: dict[str, int] = field(default_factory=dict)

    def add(self, status: NotificationStatus) -> None:
        self.processed += 1
        self.counts[status.value] = self.counts.get(status.value, 0) + 1


def _to_message(row: dict[str, Any]) -> OutboxMessage:
    try:
        payload = json.loads(row["payload_json"] or "{}")
    except json.JSONDecodeError:
        payload = {}
    return OutboxMessage(
        id=row["id"],
        type=row["type"],
        recipient_email=row["recipient_email"],
        subject=row["subject"],
        body=row["body"],
        payload=payload,
    )


def process_outbox(
    session_factory: sessionmaker[Session],
    sender: NotificationSender,
    clock: Clock,
    *,
    limit: int = 100,
    retry_failed: bool = False,
) -> ProcessReport:
    """Send pending messages. Each message is marked in its own small transaction,
    so one failure never rolls back the others."""
    report = ProcessReport()
    with session_factory.begin() as session:
        outbox = OutboxRepository(session)
        if retry_failed:
            requeued = outbox.requeue_failed()
            log.info("Re-queued %d failed message(s)", requeued)
        rows = outbox.pending(limit)
    for row in rows:
        message = _to_message(row)
        result = sender.send(message)
        with session_factory.begin() as session:
            OutboxRepository(session).mark(message.id, result.status, clock.now(), result.error)
        report.add(result.status)
    log.info(
        "Processed %d message(s) with %s sender: %s", report.processed, sender.name, report.counts
    )
    return report
