"""Review detail: survey form with drafts, locked answers, both sides, acknowledgement."""

from __future__ import annotations

from datetime import tzinfo

import pandas as pd
import streamlit as st

from core.errors import DomainError
from core.models import Action, Page, RespondentRole, Status
from core.next_step import NextKind
from core.permissions import Viewer
from core.survey import check_answers, normalise_answers
from core.timeutil import format_date, format_local_date, format_local_datetime, to_local
from services.review_detail_service import HiddenReason, Relation, ReviewDetail, SideView
from ui import queries
from ui.components.confirm_dialog import ask_confirmation
from ui.components.progress_stepper import progress_stepper
from ui.components.status_badge import status_badge, status_label
from ui.components.survey_form import (
    FormResult,
    render_answers,
    render_side_by_side,
    survey_form,
)
from ui.state import REVIEW_ID_KEY, clear_caches, flash, get_services, require_page
from ui.texts import error_text, t

PICKER_KEY = "review_picker"
QUERY_DONE_KEY = "_review_query_param_done"


def render() -> None:
    viewer = require_page(Page.REVIEW)
    tz = get_services().settings.tz
    st.title(t("nav.review"))

    cycle = queries.open_cycle()
    if cycle is None:
        st.info(t("common.no_open_cycle"), icon=":material/info:")
        return
    items = queries.viewable_reviews(cycle.cycle_id, viewer.employee_id)
    if not items:
        st.info(t("review.none"), icon=":material/inbox:")
        return

    review_id = _pick_review(items)
    try:
        detail = queries.review_detail(review_id, viewer.employee_id)
    except DomainError as exc:
        st.error(error_text(exc), icon=":material/block:")
        return

    _header(detail, tz)
    _answers(detail, viewer)
    _acknowledgement(detail, viewer, tz)
    _comments(detail, tz)
    if viewer.is_hr:
        _history(detail, viewer, tz)


# ---- selection and header ----------------------------------------------------------


def _pick_review(items: list) -> int:  # type: ignore[type-arg]
    ids = [item.review_id for item in items]
    incoming = st.session_state.pop(REVIEW_ID_KEY, None)
    if incoming is None and not st.session_state.get(QUERY_DONE_KEY):
        incoming = st.query_params.get("review_id")  # links in notification emails
        st.session_state[QUERY_DONE_KEY] = True
    try:
        wanted = int(incoming) if incoming is not None else None
    except ValueError:
        wanted = None
    if wanted in ids:
        st.session_state[PICKER_KEY] = wanted
    if st.session_state.get(PICKER_KEY) not in ids:
        st.session_state[PICKER_KEY] = ids[0]
    by_id = {item.review_id: item for item in items}
    return st.selectbox(
        t("review.pick"),
        ids,
        format_func=lambda rid: (
            f"{by_id[rid].reviewee} · {by_id[rid].reviewer} — {status_label(by_id[rid].status)}"
        ),
        key=PICKER_KEY,
        help=t("review.pick_help"),
    )


def _header(detail: ReviewDetail, tz: tzinfo) -> None:
    st.subheader(t("review.heading", reviewee=detail.reviewee.name, reviewer=detail.reviewer.name))
    st.caption(t(f"review.relation_{detail.relation.value}"))
    st.markdown(status_badge(detail.status))
    progress_stepper(detail.status)
    if detail.progress.meeting_at:
        st.caption(
            t("review.meeting_line", when=format_local_datetime(detail.progress.meeting_at, tz))
        )
    if (
        detail.relation is Relation.REVIEWER
        and detail.status is Status.MANAGER_ASSESSMENT_SUBMITTED
    ):
        st.info(t("review.schedule_hint"), icon=":material/event:")


# ---- answers -------------------------------------------------------------------------


def _hidden_text(side: SideView) -> str:
    if side.hidden_reason is HiddenReason.PARTICIPANTS_ONLY:
        return t("review.hidden_participants_only")
    if side.hidden_reason is HiddenReason.REVEAL_LATER:
        if side.reveal_date:
            return t("review.hidden_reveal_later", date=format_date(side.reveal_date))
        return t("review.hidden_reveal_unscheduled")
    return t("review.hidden_not_submitted")


def _badge(side: SideView) -> str:
    if side.submitted:
        return f":green-badge[:material/lock: {t('review.submitted_badge')}]"
    return f":gray-badge[:material/edit_note: {t('review.draft_badge')}]"


def _title(detail: ReviewDetail, role: RespondentRole) -> str:
    if role is RespondentRole.SELF:
        return t("review.self_heading", name=detail.reviewee.name)
    return t("review.manager_heading", name=detail.reviewer.name)


def _answers(detail: ReviewDetail, viewer: Viewer) -> None:
    st.divider()
    self_side, manager_side = detail.self_side, detail.manager_side

    if detail.relation is Relation.OBSERVER:
        st.info(t("review.hidden_participants_only"), icon=":material/visibility_off:")
        return

    # Someone is filling in their side right now.
    editing = next((s for s in (self_side, manager_side) if s.editable), None)
    if editing is not None:
        other = manager_side if editing is self_side else self_side
        if editing.role is RespondentRole.MANAGER and other.visible:
            left, right = st.columns(2, gap="large")
            with left:
                st.markdown(f"#### {_title(detail, RespondentRole.SELF)} {_badge(other)}")
                render_answers(detail.survey, other.answers)
            with right:
                st.markdown(f"#### {_title(detail, RespondentRole.MANAGER)}")
                _form(detail, viewer, editing)
        else:
            st.markdown(f"#### {_title(detail, editing.role)}")
            _form(detail, viewer, editing)
        return

    if self_side.visible and manager_side.visible and (self_side.answers or manager_side.answers):
        st.markdown(f"#### {t('review.compare_title')}")
        render_side_by_side(
            detail.survey,
            self_side.answers,
            manager_side.answers,
            left_title=f"{t('review.self_title')} {_badge(self_side)}",
            right_title=f"{t('review.manager_title')} {_badge(manager_side)}",
        )
        return

    if detail.relation is Relation.REVIEWER and not self_side.visible:
        st.info(t("review.waiting_self", name=detail.reviewee.name), icon=":material/schedule:")
        return

    for side in (self_side, manager_side):
        if side.visible and side.answers:
            st.markdown(f"#### {_title(detail, side.role)} {_badge(side)}")
            render_answers(detail.survey, side.answers)
        elif side.visible:
            st.markdown(f"#### {_title(detail, side.role)}")
            st.caption(t("review.no_answers"))
        elif not side.visible:
            st.markdown(f"#### {_title(detail, side.role)}")
            st.info(_hidden_text(side), icon=":material/visibility_off:")


def _form(detail: ReviewDetail, viewer: Viewer, side: SideView) -> None:
    form_key = f"survey_{detail.review_id}_{side.role.value}"
    result = survey_form(detail.survey, side.answers, form_key=form_key)
    if result is not None:
        _handle_form(detail, viewer, side.role, result)


def _handle_form(
    detail: ReviewDetail, viewer: Viewer, role: RespondentRole, result: FormResult
) -> None:
    reviews = get_services().reviews
    if not result.submit:
        try:
            reviews.save_answers(detail.review_id, viewer.employee_id, role, result.values)
        except DomainError as exc:
            st.error(error_text(exc), icon=":material/error:")
            return
        flash(t("review.draft_saved"))
        clear_caches()
        st.rerun()

    check = check_answers(detail.survey, normalise_answers(result.values), require_complete=True)
    if not check.complete:
        names = [
            q.text
            for q in detail.survey.questions
            if q.id in check.missing or q.id in check.invalid
        ]
        st.error(t("review.incomplete", questions="; ".join(names)), icon=":material/error:")
        return

    is_self = role is RespondentRole.SELF
    action = Action.SUBMIT_SELF if is_self else Action.SUBMIT_MANAGER
    consequence = t(
        "review.confirm_submit_self" if is_self else "review.confirm_submit_manager",
        name=detail.reviewee.name,
        reviewer=detail.reviewer.name,
    )
    values = dict(result.values)

    def submit() -> None:
        try:
            reviews.transition(detail.review_id, action, viewer.employee_id, {"answers": values})
        except DomainError as exc:
            flash(error_text(exc), ok=False)
        else:
            flash(t("review.submitted_self" if is_self else "review.submitted_manager"))
        clear_caches()

    ask_confirmation(
        viewer.employee_id,
        [consequence],
        submit,
        confirm_label=t("review.submit_label_self" if is_self else "review.submit_label_manager"),
    )


# ---- acknowledgement -----------------------------------------------------------------


def _acknowledgement(detail: ReviewDetail, viewer: Viewer, tz: tzinfo) -> None:
    if detail.status not in (Status.MEETING_SCHEDULED, Status.ACKNOWLEDGED, Status.COMPLETED):
        return
    st.divider()
    st.markdown(f"#### :material/handshake: {t('review.ack_title')}")
    progress = detail.progress
    for person, stamp in (
        (detail.reviewee, progress.reviewee_ack_at),
        (detail.reviewer, progress.reviewer_ack_at),
    ):
        if stamp:
            st.markdown(
                ":green[:material/check_circle:] "
                + t("review.ack_done", name=person.name, date=format_local_date(stamp, tz))
            )
        else:
            st.markdown(
                ":gray[:material/radio_button_unchecked:] "
                + t("review.ack_pending", name=person.name)
            )

    if detail.next.kind is NextKind.MEETING and detail.relation in (
        Relation.REVIEWER,
        Relation.REVIEWEE,
    ):
        assert progress.meeting_at is not None
        day = to_local(progress.meeting_at, tz).strftime("%d.%m.%Y")
        st.info(t("review.ack_not_yet", date=day), icon=":material/schedule:")
        return
    if not detail.can_acknowledge:
        return

    with st.form(f"ack_{detail.review_id}", enter_to_submit=False):
        confirmed = st.checkbox(t("review.ack_checkbox"), key=f"ack_box_{detail.review_id}")
        comment = None
        if detail.relation is Relation.REVIEWEE:
            comment = st.text_area(
                t("review.ack_comment"),
                help=t("review.ack_comment_help"),
                key=f"ack_comment_{detail.review_id}",
                max_chars=2000,
            )
        pressed = st.form_submit_button(
            t("review.ack_button"), type="primary", icon=":material/how_to_reg:"
        )
    if not pressed:
        return
    if not confirmed:
        st.warning(t("review.ack_checkbox"), icon=":material/check_box_outline_blank:")
        return
    try:
        get_services().reviews.transition(
            detail.review_id, Action.ACKNOWLEDGE, viewer.employee_id, {"comment": comment}
        )
    except DomainError as exc:
        st.error(error_text(exc), icon=":material/error:")
        return
    flash(t("review.ack_saved"))
    clear_caches()
    st.rerun()


# ---- comments and history ------------------------------------------------------------


def _comments(detail: ReviewDetail, tz: tzinfo) -> None:
    if not detail.comments:
        return
    st.markdown(f"#### :material/forum: {t('review.comments')}")
    for comment in detail.comments:
        with st.container(border=True):
            st.caption(f"{comment.author} · {format_local_datetime(comment.created_at, tz)}")
            st.markdown(comment.text)


def _history(detail: ReviewDetail, viewer: Viewer, tz: tzinfo) -> None:
    with st.expander(f":material/history: {t('review.history')}"):
        entries = get_services().details.history(detail.review_id, viewer)
        if not entries:
            st.caption(t("review.history_empty"))
            return
        columns = [
            "timestamp",
            "actor_id",
            "action",
            "field_changed",
            "old_value",
            "new_value",
            "reason",
        ]
        frame = pd.DataFrame(entries)[columns]
        frame["timestamp"] = [format_local_datetime(ts, tz) for ts in frame["timestamp"]]
        frame = frame.rename(columns={c: t(f"review.history_columns.{c}") for c in columns})
        st.dataframe(frame, hide_index=True, width="stretch")
