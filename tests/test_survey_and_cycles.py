"""Survey definition and review cycles."""

from __future__ import annotations

from datetime import date

import pytest

from config.settings import CONFIG_DIR
from core.errors import DomainError, ErrorCode
from core.survey import (
    QuestionType,
    SurveyDefinitionError,
    SurveyRegistry,
    check_answers,
    load_survey,
    parse_survey,
)
from services.container import AppServices
from tests.conftest import FULL_ANSWERS


def test_shipped_survey_matches_the_spec() -> None:
    survey = load_survey(CONFIG_DIR / "survey.yaml")
    assert [q.id for q in survey.questions] == [
        "goals",
        "quality",
        "collaboration",
        "communication",
        "initiative",
        "strengths",
        "development",
        "next_goals",
        "overall",
    ]
    assert survey.question("goals").comment  # type: ignore[union-attr]
    assert survey.question("strengths").type is QuestionType.TEXT  # type: ignore[union-attr]
    assert check_answers(
        survey, {k: str(v) for k, v in FULL_ANSWERS.items()}, require_complete=True
    ).complete


@pytest.mark.parametrize(
    "data",
    [
        {"questions": [{"id": "a", "text": "A", "type": "rating"}]},  # no version
        {"version": "1", "questions": []},
        {"version": "1", "questions": [{"id": "a", "text": "A", "type": "stars"}]},
        {
            "version": "1",
            "questions": [
                {"id": "a", "text": "A", "type": "text"},
                {"id": "a", "text": "B", "type": "text"},
            ],
        },
    ],
)
def test_malformed_survey_is_rejected(data: dict) -> None:
    with pytest.raises(SurveyDefinitionError):
        parse_survey(data)


def test_registry_keeps_old_versions() -> None:
    old = parse_survey(
        {"version": "2025.1", "questions": [{"id": "x", "text": "X", "type": "text"}]}
    )
    new = parse_survey(
        {"version": "2026.1", "questions": [{"id": "y", "text": "Y", "type": "text"}]}
    )
    registry = SurveyRegistry(new, {"2025.1": old})
    assert registry.get("2025.1") is old
    assert registry.get("2026.1") is new and registry.get("unknown") is new


def test_only_one_open_cycle(services: AppServices) -> None:
    with pytest.raises(DomainError) as err:
        services.cycles.open_cycle("Another")
    assert err.value.code is ErrorCode.CYCLE_ALREADY_OPEN


def test_cycle_validation(bare_services: AppServices) -> None:
    with pytest.raises(DomainError) as err:
        bare_services.cycles.open_cycle("  ")
    assert err.value.code is ErrorCode.CYCLE_NAME_REQUIRED
    with pytest.raises(DomainError) as err:
        bare_services.cycles.open_cycle("X", date(2026, 10, 10), date(2026, 10, 1))
    assert err.value.code is ErrorCode.CYCLE_INVALID_DATES


def test_close_cycle_blocks_new_reviews(services: AppServices) -> None:
    cycle = services.cycles.get_open_cycle()
    assert cycle is not None
    with pytest.raises(DomainError) as err:
        services.cycles.close_cycle(cycle.cycle_id, actor_id="C1")
    assert err.value.code is ErrorCode.HR_ONLY
    services.cycles.close_cycle(cycle.cycle_id, actor_id="H1")
    with pytest.raises(DomainError) as err:
        services.reviews.transition(None, "initiate", "C1", {"reviewee_id": "B1"})
    assert err.value.code is ErrorCode.NO_OPEN_CYCLE
    with pytest.raises(DomainError) as err:
        services.reviews.transition(
            None, "initiate", "C1", {"reviewee_id": "B1", "cycle_id": cycle.cycle_id}
        )
    assert err.value.code is ErrorCode.CYCLE_CLOSED
