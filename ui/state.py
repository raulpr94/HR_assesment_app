"""Session-state helpers and cached service access for the Streamlit UI."""

from __future__ import annotations

import logging

import streamlit as st

from config.settings import Settings, load_settings
from core.models import Page
from core.permissions import Viewer, can_access_page
from services.container import AppServices, build_services
from ui.texts import t

log = logging.getLogger(__name__)

VIEW_AS_KEY = "view_as_id"
VIEWER_KEY = "_current_viewer"


@st.cache_resource(show_spinner=False)
def get_settings() -> Settings:
    settings = load_settings()
    logging.basicConfig(level=settings.log_level, format="%(levelname)s %(name)s: %(message)s")
    return settings


@st.cache_resource(show_spinner=False)
def get_services() -> AppServices:
    return build_services(get_settings())


def clear_caches() -> None:
    """Call after every write so cached read-only lookups are refreshed."""
    st.cache_data.clear()


def set_viewer(viewer: Viewer | None) -> None:
    st.session_state[VIEWER_KEY] = viewer


def current_viewer() -> Viewer | None:
    return st.session_state.get(VIEWER_KEY)


def require_page(page: Page) -> Viewer:
    """Refuse access when a page is reached directly by someone not allowed to see it."""
    viewer = current_viewer()
    if viewer is None or not can_access_page(viewer, page):
        st.error(t("common.access_denied"), icon=":material/block:")
        st.stop()
    return viewer


# ---- navigation ------------------------------------------------------------------------

PAGES_KEY = "_pages"
REVIEW_ID_KEY = "review_id"


def register_pages(pages: dict[Page, object]) -> None:
    """Remember this run's st.Page objects so pages can link to each other."""
    st.session_state[PAGES_KEY] = pages


def go_to(page: Page, **state: object) -> None:
    """Switch page, optionally handing over values through session state."""
    st.session_state.update(state)
    target = st.session_state.get(PAGES_KEY, {}).get(page)
    if target is not None:
        st.switch_page(target)  # type: ignore[arg-type]


# ---- flash messages (toasts that survive a rerun) ---------------------------------------

FLASH_KEY = "_flash"


def flash(message: str, *, ok: bool = True) -> None:
    st.session_state.setdefault(FLASH_KEY, []).append((message, ok))


def show_flashes() -> None:
    for message, ok in st.session_state.pop(FLASH_KEY, []):
        st.toast(message, icon=":material/check_circle:" if ok else ":material/error:")
