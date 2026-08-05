"""Tests for the Raskin framework (aggregation) and the Pydantic contracts."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.rubric import (
    RASKIN_ELEMENTS,
    RUBRIC_VERSION,
    RaskinElement,
    aggregate_overall,
)
from app.schemas import EvaluationPayload
from tests.factories import make_element_result, make_evaluation_payload


# --- Framework ---------------------------------------------------------------


def test_five_distinct_elements():
    keys = [e.key for e in RASKIN_ELEMENTS]
    assert len(keys) == 5
    assert set(keys) == set(RaskinElement)


def test_rubric_version():
    assert RUBRIC_VERSION == "2.0-raskin"


@pytest.mark.parametrize(
    "scores,expected",
    [
        ({e.key: 10 for e in RASKIN_ELEMENTS}, 10.0),
        ({e.key: 0 for e in RASKIN_ELEMENTS}, 0.0),
        (
            {
                RaskinElement.name_the_enemy: 7.5,
                RaskinElement.why_now: 6.5,
                RaskinElement.promised_land: 8.5,
                RaskinElement.obstacles_and_gifts: 8.0,
                RaskinElement.present_evidence: 8.5,
            },
            7.8,  # the Qualisure example
        ),
    ],
)
def test_aggregate_overall(scores, expected):
    assert aggregate_overall(scores) == expected


def test_aggregate_accepts_string_keys():
    scores = {e.key.value: 5 for e in RASKIN_ELEMENTS}
    assert aggregate_overall(scores) == 5.0


def test_aggregate_rejects_missing_element():
    scores = {e.key: 5 for e in RASKIN_ELEMENTS if e.key != RaskinElement.why_now}
    with pytest.raises(ValueError, match="missing scores"):
        aggregate_overall(scores)


# --- Schemas -----------------------------------------------------------------


def test_valid_payload_round_trips():
    payload = make_evaluation_payload()
    restored = EvaluationPayload.model_validate(payload.model_dump())
    assert restored == payload


def test_payload_json_schema_is_generatable():
    schema = EvaluationPayload.model_json_schema()
    assert schema["type"] == "object"
    assert "elements" in schema["properties"]


def test_rejects_four_elements():
    payload = make_evaluation_payload()
    with pytest.raises(ValidationError, match="exactly the 5 Raskin elements"):
        make_evaluation_payload(elements=payload.elements[:4])


def test_rejects_duplicate_element():
    dupes = [make_element_result(RaskinElement.why_now) for _ in range(5)]
    with pytest.raises(ValidationError, match="exactly the 5 Raskin elements"):
        make_evaluation_payload(elements=dupes)


def test_accepts_half_point_scores():
    el = make_element_result(RaskinElement.why_now, score=6.5)
    assert el.score == 6.5


def test_tolerates_unknown_keys_and_missing_optional_fields():
    from app.schemas import ElementResult

    # Extra keys ignored; missing optional text fields default to empty.
    el = ElementResult.model_validate(
        {"element": "why_now", "score": 6.5, "made_up_field": 123}
    )
    assert el.summary == "" and el.evaluation == ""


def test_out_of_range_score_is_accepted_then_clamped_by_finalize():
    from app.evaluator import finalize_scores

    payload = make_evaluation_payload()
    hot = payload.elements[0].model_copy(update={"score": 42})
    payload = payload.model_copy(update={"elements": [hot, *payload.elements[1:]]})
    finalized = finalize_scores(payload)
    assert finalized.elements[0].score == 10.0  # clamped
    assert 0 <= finalized.overall_score <= 10
