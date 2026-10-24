"""Survey definition: load and validate it, and check answers against it.

Questions live in config/survey.yaml. Each review stores the survey version it was
started with; older versions are kept in config/survey_archive/<version>.yaml.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

import yaml

MAX_TEXT_LENGTH = 5000
COMMENT_SUFFIX = "__comment"


class SurveyDefinitionError(ValueError):
    """The survey YAML is malformed."""


class QuestionType(str, Enum):
    RATING = "rating"
    TEXT = "text"


@dataclass(frozen=True)
class Question:
    id: str
    text: str
    type: QuestionType
    required: bool = True
    comment: bool = False  # rating question with an optional free-text comment
    help: str | None = None

    @property
    def comment_id(self) -> str:
        return comment_key(self.id)


@dataclass(frozen=True)
class SurveyDefinition:
    version: str
    title: str
    questions: tuple[Question, ...]
    scale_min: int = 1
    scale_max: int = 5
    scale_labels: dict[int, str] = field(default_factory=dict)

    def question(self, question_id: str) -> Question | None:
        return next((q for q in self.questions if q.id == question_id), None)

    @property
    def answer_keys(self) -> tuple[str, ...]:
        """Every key an answer set may contain (questions plus comment fields)."""
        keys: list[str] = []
        for q in self.questions:
            keys.append(q.id)
            if q.comment:
                keys.append(q.comment_id)
        return tuple(keys)


@dataclass(frozen=True)
class AnswerCheck:
    missing: tuple[str, ...] = ()
    invalid: dict[str, str] = field(default_factory=dict)

    @property
    def complete(self) -> bool:
        return not self.missing and not self.invalid


def comment_key(question_id: str) -> str:
    return f"{question_id}{COMMENT_SUFFIX}"


def parse_survey(data: Mapping[str, Any]) -> SurveyDefinition:
    if not isinstance(data, Mapping):
        raise SurveyDefinitionError("Survey file must contain a mapping at the top level")
    version = str(data.get("version", "")).strip()
    if not version:
        raise SurveyDefinitionError("Survey needs a non-empty 'version'")
    scale = data.get("scale") or {}
    scale_min, scale_max = int(scale.get("min", 1)), int(scale.get("max", 5))
    if scale_min >= scale_max:
        raise SurveyDefinitionError("scale.min must be lower than scale.max")
    labels = {int(k): str(v) for k, v in (scale.get("labels") or {}).items()}

    raw_questions = data.get("questions")
    if not isinstance(raw_questions, list) or not raw_questions:
        raise SurveyDefinitionError("Survey needs a non-empty 'questions' list")
    questions: list[Question] = []
    seen: set[str] = set()
    for index, raw in enumerate(raw_questions, start=1):
        qid = str(raw.get("id", "")).strip()
        if not qid or COMMENT_SUFFIX in qid:
            raise SurveyDefinitionError(f"Question {index} needs a valid 'id'")
        if qid in seen:
            raise SurveyDefinitionError(f"Duplicate question id {qid!r}")
        seen.add(qid)
        try:
            qtype = QuestionType(str(raw.get("type", "")).lower())
        except ValueError as exc:
            raise SurveyDefinitionError(
                f"Question {qid!r}: type must be 'rating' or 'text'"
            ) from exc
        text = str(raw.get("text", "")).strip()
        if not text:
            raise SurveyDefinitionError(f"Question {qid!r} needs 'text'")
        questions.append(
            Question(
                id=qid,
                text=text,
                type=qtype,
                required=bool(raw.get("required", True)),
                comment=bool(raw.get("comment", False)) and qtype is QuestionType.RATING,
                help=raw.get("help"),
            )
        )
    return SurveyDefinition(
        version=version,
        title=str(data.get("title", "Performance review")),
        questions=tuple(questions),
        scale_min=scale_min,
        scale_max=scale_max,
        scale_labels=labels,
    )


def load_survey(path: Path) -> SurveyDefinition:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise SurveyDefinitionError(f"Survey file not found: {path}") from exc
    except yaml.YAMLError as exc:
        raise SurveyDefinitionError(f"Survey file {path} is not valid YAML: {exc}") from exc
    return parse_survey(data)


def check_answers(
    survey: SurveyDefinition, answers: Mapping[str, str | None], *, require_complete: bool
) -> AnswerCheck:
    """Validate answer values; with ``require_complete`` also list missing required ones."""
    invalid: dict[str, str] = {}
    missing: list[str] = []
    known = set(survey.answer_keys)
    for key in answers:
        if key not in known:
            invalid[key] = "unknown question"
    for q in survey.questions:
        value = _clean(answers.get(q.id))
        if value is None:
            if require_complete and q.required:
                missing.append(q.id)
        elif q.type is QuestionType.RATING:
            try:
                rating = int(value)
            except ValueError:
                invalid[q.id] = "must be a whole number"
            else:
                if not survey.scale_min <= rating <= survey.scale_max:
                    invalid[q.id] = f"must be between {survey.scale_min} and {survey.scale_max}"
        elif len(value) > MAX_TEXT_LENGTH:
            invalid[q.id] = f"must be at most {MAX_TEXT_LENGTH} characters"
        if q.comment:
            comment = _clean(answers.get(q.comment_id))
            if comment is not None and len(comment) > MAX_TEXT_LENGTH:
                invalid[q.comment_id] = f"must be at most {MAX_TEXT_LENGTH} characters"
    return AnswerCheck(missing=tuple(missing), invalid=invalid)


def normalise_answers(answers: Mapping[str, object]) -> dict[str, str | None]:
    """Strip text and turn blanks into None; ratings become their string form."""
    return {key: _clean(None if value is None else str(value)) for key, value in answers.items()}


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return value or None


class SurveyRegistry:
    """The current survey plus archived versions, looked up by version string."""

    def __init__(self, current: SurveyDefinition, archived: Mapping[str, SurveyDefinition]):
        self.current = current
        self._by_version = {**archived, current.version: current}

    @classmethod
    def from_paths(cls, current_path: Path, archive_dir: Path | None = None) -> SurveyRegistry:
        current = load_survey(current_path)
        archived: dict[str, SurveyDefinition] = {}
        if archive_dir and archive_dir.is_dir():
            for path in sorted(archive_dir.glob("*.yaml")):
                survey = load_survey(path)
                archived[survey.version] = survey
        return cls(current, archived)

    def get(self, version: str | None) -> SurveyDefinition:
        """Survey for ``version``; unknown or empty versions fall back to the current one."""
        if version and version in self._by_version:
            return self._by_version[version]
        return self.current
