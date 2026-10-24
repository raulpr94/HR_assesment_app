"""User-facing text lookup from config/texts_<lang>.yaml."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from config.settings import CONFIG_DIR
from core.errors import DomainError

DEFAULT_LANGUAGE = "en"


@lru_cache(maxsize=4)
def _load(language: str) -> dict[str, Any]:
    path = Path(CONFIG_DIR) / f"texts_{language}.yaml"
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def t(key: str, language: str = DEFAULT_LANGUAGE, **params: object) -> str:
    """Look up a dotted key like ``status.COMPLETED``. Unknown keys return the key."""
    node: Any = _load(language)
    for part in key.split("."):
        if not isinstance(node, dict) or part not in node:
            return key
        node = node[part]
    text = str(node)
    if params:
        try:
            return text.format(**params)
        except (KeyError, IndexError, ValueError):
            return text
    return text


def error_text(error: DomainError, language: str = DEFAULT_LANGUAGE) -> str:
    """Friendly message for a domain error, falling back to its English default."""
    key = f"errors.{error.code.value}"
    template = t(key, language)
    return error.message if template == key else error.format(template)


def t_raw(key: str, language: str = DEFAULT_LANGUAGE) -> Any:
    """The raw value at a dotted key (e.g. a list of table rows), or None."""
    node: Any = _load(language)
    for part in key.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node
