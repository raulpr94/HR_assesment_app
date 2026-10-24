"""Zapier sender: POSTs each message as JSON to a Zapier catch-hook webhook."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable

import requests

from core.models import NotificationStatus
from services.notifications.base import OutboxMessage, SendResult

log = logging.getLogger(__name__)

RETRYABLE_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})


class ZapierSender:
    name = "zapier"

    def __init__(
        self,
        webhook_url: str,
        *,
        timeout_seconds: float = 10.0,
        max_retries: int = 3,
        backoff_seconds: float = 1.0,
        session: requests.Session | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.webhook_url = webhook_url
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self.backoff_seconds = backoff_seconds
        self.session = session or requests.Session()
        self.sleep = sleep

    def build_body(self, message: OutboxMessage) -> dict[str, object]:
        return {
            "id": message.id,
            "type": message.type,
            "to": message.recipient_email,
            "subject": message.subject,
            "body": message.body,
            "payload": message.payload,
        }

    def send(self, message: OutboxMessage) -> SendResult:
        body = self.build_body(message)
        attempts = self.max_retries + 1
        last_error = "not sent"
        for attempt in range(1, attempts + 1):
            try:
                response = self.session.post(
                    self.webhook_url, json=body, timeout=self.timeout_seconds
                )
            except (requests.ConnectionError, requests.Timeout) as exc:
                last_error = f"{type(exc).__name__}: {exc}"
            except requests.RequestException as exc:  # invalid URL etc.: retrying won't help
                return SendResult(NotificationStatus.FAILED, f"{type(exc).__name__}: {exc}")
            else:
                if 200 <= response.status_code < 300:
                    return SendResult(NotificationStatus.SENT)
                last_error = f"HTTP {response.status_code}: {response.text[:200]}"
                if response.status_code not in RETRYABLE_STATUS:
                    break
            if attempt < attempts:
                delay = self.backoff_seconds * 2 ** (attempt - 1)
                log.warning(
                    "Zapier attempt %d/%d failed (%s), retrying in %.1fs",
                    attempt,
                    attempts,
                    last_error,
                    delay,
                )
                self.sleep(delay)
        log.error("Zapier delivery failed for message #%d: %s", message.id, last_error)
        return SendResult(NotificationStatus.FAILED, last_error)
