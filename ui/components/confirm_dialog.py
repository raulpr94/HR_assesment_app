"""Confirmation dialog for writes. States the consequence before anything happens.

Pages call ``ask_confirmation``; ``app.py`` calls ``render_pending_confirmation`` once
per run. Keeping the request in session state means the dialog survives full reruns,
not only dialog-fragment reruns.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

import streamlit as st

from ui.texts import t

PENDING_KEY = "_pending_confirmation"


@dataclass(frozen=True)
class Confirmation:
    owner_id: str  # the viewer who asked; dropped if "View as" changes meanwhile
    items: tuple[str, ...]
    on_confirm: Callable[[], None]
    intro: str | None = None
    confirm_label: str | None = None


def ask_confirmation(
    owner_id: str,
    items: Sequence[str],
    on_confirm: Callable[[], None],
    *,
    intro: str | None = None,
    confirm_label: str | None = None,
) -> None:
    st.session_state[PENDING_KEY] = Confirmation(
        owner_id, tuple(items), on_confirm, intro, confirm_label
    )


def _clear() -> None:
    st.session_state.pop(PENDING_KEY, None)


@st.dialog(t("confirm.title"), width="large", on_dismiss=_clear)
def _dialog(request: Confirmation) -> None:
    st.markdown(request.intro or t("confirm.intro"))
    for item in request.items:
        st.markdown(f"- {item}")
    left, right = st.columns(2)
    if left.button(
        request.confirm_label or t("confirm.confirm"),
        type="primary",
        width="stretch",
        key="confirm_yes",
    ):
        _clear()
        request.on_confirm()
        st.rerun()
    if right.button(t("confirm.cancel"), width="stretch", key="confirm_no"):
        _clear()
        st.rerun()


def render_pending_confirmation(viewer_id: str | None) -> None:
    request: Confirmation | None = st.session_state.get(PENDING_KEY)
    if request is None:
        return
    if request.owner_id != viewer_id:
        _clear()
        return
    _dialog(request)
