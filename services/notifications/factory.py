"""Choose the notification sender from config, in one place."""

from __future__ import annotations

from config.settings import ConfigError, NotifyMode, Settings
from services.notifications.base import NotificationSender
from services.notifications.dry_run import DryRunSender
from services.notifications.zapier import ZapierSender


def build_sender(settings: Settings) -> NotificationSender:
    if settings.notify_mode is NotifyMode.ZAPIER:
        if not settings.zapier_webhook_url:
            raise ConfigError("NOTIFY_MODE=zapier needs ZAPIER_WEBHOOK_URL")
        return ZapierSender(
            settings.zapier_webhook_url,
            timeout_seconds=settings.zapier_timeout_seconds,
            max_retries=settings.zapier_max_retries,
        )
    return DryRunSender()
