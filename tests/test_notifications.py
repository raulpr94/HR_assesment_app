"""Outbox processing, senders, templates and notification settings."""

from __future__ import annotations

import pytest
import requests

from config.settings import ConfigError, load_settings
from core.models import NotificationStatus, NotificationType
from db.repositories import Repositories
from services.container import AppServices
from services.notifications.base import OutboxMessage
from services.notifications.zapier import ZapierSender

MESSAGE = OutboxMessage(1, "cycle_started", "a@example.com", "Hi", "Body", {"k": "v"})


class FakeResponse:
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code
        self.text = f"status {status_code}"


class FakeSession:
    def __init__(self, *outcomes: int | Exception) -> None:
        self.outcomes = list(outcomes)
        self.calls: list[dict] = []

    def post(self, url: str, json: dict, timeout: float) -> FakeResponse:
        self.calls.append({"url": url, "json": json, "timeout": timeout})
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return FakeResponse(outcome)


def zapier(session: FakeSession, retries: int = 2) -> tuple[ZapierSender, list[float]]:
    sleeps: list[float] = []
    sender = ZapierSender(
        "https://hooks.zapier.com/hooks/catch/1/abc",
        max_retries=retries,
        session=session,
        sleep=sleeps.append,  # type: ignore[arg-type]
    )
    return sender, sleeps


def test_zapier_success_posts_json() -> None:
    session = FakeSession(200)
    sender, _ = zapier(session)
    assert sender.send(MESSAGE).status is NotificationStatus.SENT
    body = session.calls[0]["json"]
    assert body["to"] == "a@example.com" and body["payload"] == {"k": "v"}
    assert session.calls[0]["timeout"] == 10.0


def test_zapier_retries_server_errors_with_backoff() -> None:
    session = FakeSession(500, requests.ConnectionError("down"), 200)
    sender, sleeps = zapier(session)
    assert sender.send(MESSAGE).status is NotificationStatus.SENT
    assert sleeps == [1.0, 2.0]


def test_zapier_gives_up_after_retries() -> None:
    sender, sleeps = zapier(FakeSession(503, 503, 503))
    result = sender.send(MESSAGE)
    assert result.status is NotificationStatus.FAILED and "HTTP 503" in (result.error or "")
    assert len(sleeps) == 2


def test_zapier_does_not_retry_client_errors() -> None:
    session = FakeSession(400)
    sender, sleeps = zapier(session)
    assert sender.send(MESSAGE).status is NotificationStatus.FAILED
    assert len(session.calls) == 1 and sleeps == []


def test_dry_run_marks_pending_rows_skipped(services: AppServices) -> None:
    report = services.send_notifications()
    assert report.counts == {"skipped": 2}
    with services.sessions() as session:
        outbox = Repositories.for_session(session).outbox
        assert outbox.counts() == {"skipped": 2}
    assert services.send_notifications().processed == 0  # nothing pending any more


def test_outbox_with_zapier_marks_sent_and_failed(services: AppServices) -> None:
    sender, _ = zapier(FakeSession(200, 400), retries=0)
    report = services.send_notifications(sender=sender)
    assert report.counts == {"sent": 1, "failed": 1}
    with services.sessions() as session:
        rows = Repositories.for_session(session).outbox.list()
    failed = next(r for r in rows if r["status"] == "failed")
    assert failed["attempts"] == 1 and "HTTP 400" in failed["last_error"]
    sent = next(r for r in rows if r["status"] == "sent")
    assert sent["sent_at"] is not None

    retry, _ = zapier(FakeSession(200), retries=0)
    assert services.send_notifications(sender=retry, retry_failed=True).counts == {"sent": 1}


@pytest.mark.parametrize("kind", list(NotificationType))
def test_every_notification_type_has_a_template(
    services: AppServices, kind: NotificationType
) -> None:
    context = {
        "recipient": {"first_name": "Anna", "name": "Anna A", "email": "a@example.com"},
        "reviewer": {"name": "Carl"},
        "reviewee": {"name": "Berta"},
        "is_reviewer": False,
        "rescheduled": True,
        "cycle": {"name": "2026 Annual", "start": "01.10.2026", "due": "30.11.2026"},
        "meeting": {
            "date": "05.10.2026",
            "time": "14:00",
            "timezone": "Europe/Berlin",
            "duration_minutes": 60,
        },
        "app_url": "http://localhost:8501",
        "review_url": "http://localhost:8501/review",
    }
    message = services.queue.renderer.render(kind, context)
    assert message.subject and not message.subject.startswith("Subject")
    assert message.body.strip()


def test_zapier_mode_needs_webhook() -> None:
    with pytest.raises(ConfigError, match="ZAPIER_WEBHOOK_URL"):
        load_settings(
            {"NOTIFY_MODE": "zapier", "ZAPIER_WEBHOOK_URL": ""},
            env_file=None,
            use_streamlit_secrets=False,
        )
    with pytest.raises(ConfigError, match="https"):
        load_settings(
            {"NOTIFY_MODE": "zapier", "ZAPIER_WEBHOOK_URL": "http://x"},
            env_file=None,
            use_streamlit_secrets=False,
        )
    with pytest.raises(ConfigError, match="NOTIFY_MODE"):
        load_settings({"NOTIFY_MODE": "smtp"}, env_file=None, use_streamlit_secrets=False)
