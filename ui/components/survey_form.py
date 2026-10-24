"""Survey rendering: the editable form (with drafts) and read-only views.

No database access here: the form returns what the user did and the page decides.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

import streamlit as st

from core.survey import Question, QuestionType, SurveyDefinition
from ui.texts import t


@dataclass(frozen=True)
class FormResult:
    submit: bool  # False = "Save draft", True = "Submit…"
    values: dict[str, str | None]


def rating_text(survey: SurveyDefinition, value: str | None) -> str:
    if value is None or value == "":
        return t("review.not_answered")
    try:
        number = int(value)
    except ValueError:
        return value
    label = survey.scale_labels.get(number, "")
    return f"**{number}** · {label}" if label else f"**{number}**"


def answer_text(
    survey: SurveyDefinition, question: Question, answers: Mapping[str, str | None]
) -> str:
    value = answers.get(question.id)
    if question.type is QuestionType.RATING:
        text = rating_text(survey, value)
        comment = answers.get(question.comment_id) if question.comment else None
        return f"{text}  \n_{comment}_" if comment else text
    return value if value else t("review.not_answered")


def _label(question: Question) -> str:
    return f"{question.text} {t('review.required_mark')}" if question.required else question.text


def survey_form(
    survey: SurveyDefinition, answers: Mapping[str, str | None], *, form_key: str
) -> FormResult | None:
    """Editable survey in an ``st.form``. Returns the values when a button is pressed."""
    scale = list(range(survey.scale_min, survey.scale_max + 1))
    values: dict[str, object] = {}
    with st.form(form_key, enter_to_submit=False, border=True):
        st.caption(t("review.required_note"))
        for question in survey.questions:
            key = f"{form_key}_{question.id}"
            current = answers.get(question.id)
            if question.type is QuestionType.RATING:
                index = (
                    scale.index(int(current))
                    if current and current.isdigit() and int(current) in scale
                    else None
                )
                values[question.id] = st.radio(
                    _label(question),
                    options=scale,
                    index=index,
                    format_func=lambda n: f"{n} · {survey.scale_labels.get(n, '')}".rstrip(" ·"),
                    horizontal=True,
                    key=key,
                    help=question.help,
                )
                if question.comment:
                    values[question.comment_id] = st.text_area(
                        t("review.comment_label"),
                        value=answers.get(question.comment_id) or "",
                        key=f"{key}_comment",
                        height=80,
                    )
            else:
                values[question.id] = st.text_area(
                    _label(question),
                    value=current or "",
                    key=key,
                    help=question.help,
                    max_chars=5000,
                    height=110,
                )
        left, right = st.columns(2)
        draft = left.form_submit_button(
            t("review.save_draft"), icon=":material/save:", width="stretch"
        )
        submit = right.form_submit_button(
            t("review.submit"), type="primary", icon=":material/send:", width="stretch"
        )
    if not (draft or submit):
        return None
    cleaned = {k: (None if v in (None, "") else str(v)) for k, v in values.items()}
    return FormResult(submit=bool(submit), values=cleaned)


def render_answers(survey: SurveyDefinition, answers: Mapping[str, str | None]) -> None:
    """One side, read-only."""
    for question in survey.questions:
        st.markdown(f"**{question.text}**")
        st.markdown(answer_text(survey, question, answers))


def render_side_by_side(
    survey: SurveyDefinition,
    left: Mapping[str, str | None],
    right: Mapping[str, str | None],
    *,
    left_title: str,
    right_title: str,
) -> None:
    """Both sides per question, so differences are easy to spot."""
    header_left, header_right = st.columns(2)
    header_left.markdown(f"##### {left_title}")
    header_right.markdown(f"##### {right_title}")
    for question in survey.questions:
        with st.container(border=True):
            st.markdown(f"**{question.text}**")
            col_left, col_right = st.columns(2)
            col_left.markdown(answer_text(survey, question, left))
            col_right.markdown(answer_text(survey, question, right))
