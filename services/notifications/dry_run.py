"""Dry-run sender: logs messages and marks them ``skipped``. Nothing leaves the app."""

from __future__ import annotations

import logging

from core.models import NotificationStatus
from services.notifications.base import OutboxMessage, SendResult

log = logging.getLogger(__name__)


class DryRunSender:
    name = "dry_run"

    def send(self, message: OutboxMessage) -> SendResult:
        log.info(
            "[dry-run] #%d %s → %s: %s",
            message.id,
            message.type,
            message.recipient_email,
            message.subject,
        )
        return SendResult(NotificationStatus.SKIPPED)
