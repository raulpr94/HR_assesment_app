"""Application settings, loaded once into a single ``Settings`` object.

Precedence (highest first): explicit overrides > environment variables >
``st.secrets`` (only when running inside Streamlit) > ``.env`` file > defaults.
Secrets are never hard-coded.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dotenv import dotenv_values

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = PROJECT_ROOT / "config"
TEMPLATES_DIR = PROJECT_ROOT / "templates"
DATA_DIR = PROJECT_ROOT / "data"
DEFAULT_EMPLOYEES_CSV = DATA_DIR / "employees.csv"

REVEAL_POLICIES = ("meeting_date", "meeting_time", "completed")
AUTH_PROVIDERS = ("simulated",)


class ConfigError(ValueError):
    """Raised when a setting is missing or has an invalid value."""


class NotifyMode(str, Enum):
    DRY_RUN = "dry_run"
    ZAPIER = "zapier"


@dataclass(frozen=True)
class Settings:
    db_path: Path
    notify_mode: NotifyMode = NotifyMode.DRY_RUN
    zapier_webhook_url: str | None = None
    zapier_timeout_seconds: float = 10.0
    zapier_max_retries: int = 3
    timezone: str = "Europe/Berlin"
    reveal_manager_answers: str = "meeting_date"
    meeting_duration_minutes: int = 60
    auth_provider: str = "simulated"
    app_base_url: str = "http://localhost:8501"
    log_level: str = "INFO"
    survey_path: Path = CONFIG_DIR / "survey.yaml"
    survey_archive_dir: Path = CONFIG_DIR / "survey_archive"
    texts_path: Path = CONFIG_DIR / "texts_en.yaml"
    templates_dir: Path = TEMPLATES_DIR

    @property
    def db_url(self) -> str:
        return f"sqlite:///{self.db_path.as_posix()}"

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)


def _streamlit_secrets() -> dict[str, str]:
    """Top-level ``st.secrets`` values, but only when running inside Streamlit."""
    try:
        from streamlit import runtime
    except ImportError:
        return {}
    if not runtime.exists():
        return {}
    import streamlit as st

    try:
        return {k: str(v) for k, v in st.secrets.items() if not isinstance(v, Mapping)}
    except FileNotFoundError:  # no secrets.toml: that's fine, use env/.env
        return {}


def _int(values: Mapping[str, str], key: str, default: int) -> int:
    raw = values.get(key, "")
    if raw in ("", None):
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(f"{key} must be a whole number, got {raw!r}") from exc


def _float(values: Mapping[str, str], key: str, default: float) -> float:
    raw = values.get(key, "")
    if raw in ("", None):
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ConfigError(f"{key} must be a number, got {raw!r}") from exc


def load_settings(
    overrides: Mapping[str, str] | None = None,
    *,
    env_file: Path | None = PROJECT_ROOT / ".env",
    use_streamlit_secrets: bool = True,
) -> Settings:
    """Build the Settings object from all configured sources."""
    values: dict[str, str] = {}
    if env_file is not None and env_file.exists():
        values.update({k: v for k, v in dotenv_values(env_file).items() if v is not None})
    if use_streamlit_secrets:
        values.update(_streamlit_secrets())
    values.update(os.environ)
    if overrides:
        values.update(overrides)

    def get(key: str, default: str) -> str:
        value = values.get(key, "")
        return value.strip() if value and value.strip() else default

    db_path = Path(get("PERF_DB_PATH", "data/app.db"))
    if not db_path.is_absolute():
        db_path = PROJECT_ROOT / db_path

    mode_raw = get("NOTIFY_MODE", NotifyMode.DRY_RUN.value).lower()
    try:
        notify_mode = NotifyMode(mode_raw)
    except ValueError as exc:
        allowed = ", ".join(m.value for m in NotifyMode)
        raise ConfigError(f"NOTIFY_MODE must be one of: {allowed} (got {mode_raw!r})") from exc

    webhook = get("ZAPIER_WEBHOOK_URL", "") or None
    if notify_mode is NotifyMode.ZAPIER and not webhook:
        raise ConfigError("NOTIFY_MODE=zapier needs ZAPIER_WEBHOOK_URL to be set")
    if webhook and not webhook.startswith("https://"):
        raise ConfigError("ZAPIER_WEBHOOK_URL must start with https://")

    timezone = get("APP_TIMEZONE", "Europe/Berlin")
    try:
        ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ConfigError(f"APP_TIMEZONE {timezone!r} is not a known time zone") from exc

    reveal = get("REVEAL_MANAGER_ANSWERS", "meeting_date").lower()
    if reveal not in REVEAL_POLICIES:
        raise ConfigError(f"REVEAL_MANAGER_ANSWERS must be one of: {', '.join(REVEAL_POLICIES)}")

    auth = get("AUTH_PROVIDER", "simulated").lower()
    if auth not in AUTH_PROVIDERS:
        raise ConfigError(f"AUTH_PROVIDER must be one of: {', '.join(AUTH_PROVIDERS)}")

    return Settings(
        db_path=db_path,
        notify_mode=notify_mode,
        zapier_webhook_url=webhook,
        zapier_timeout_seconds=_float(values, "ZAPIER_TIMEOUT_SECONDS", 10.0),
        zapier_max_retries=max(0, _int(values, "ZAPIER_MAX_RETRIES", 3)),
        timezone=timezone,
        reveal_manager_answers=reveal,
        meeting_duration_minutes=max(5, _int(values, "MEETING_DURATION_MINUTES", 60)),
        auth_provider=auth,
        app_base_url=get("APP_BASE_URL", "http://localhost:8501").rstrip("/"),
        log_level=get("LOG_LEVEL", "INFO").upper(),
    )
