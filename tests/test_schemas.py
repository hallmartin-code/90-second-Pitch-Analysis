"""Tests for the rubric (weights, aggregation, bands) and the Pydantic contracts."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.rubric import (
    BANDS,
    DIMENSIONS,
    RUBRIC_VERSION,
    Dimension,
    aggregate_overall,
    band_for,
    dimension_weights,
)
from app.schemas import EvaluationPayload
from tests.factories import make_dimension_result, make_evaluation_payload


# --- Rubric ------------------------------------------------------------------


def test_weights_sum_to_one():
    total = sum(d.weight for d in DIMENSIONS)
    assert total == pytest.approx(1.0)


def test_exactly_five_distinct_dimensions():
    keys = [d.key for d in DIMENSIONS]
    assert len(keys) == 5
    assert set(keys) == set(Dimension)


def test_rubric_version_stamped():
    assert RUBRIC_VERSION == "1.0"


def test_dimension_weights_match_spec():
    weights = dimension_weights()
    assert weights[Dimension.clarity] == 0.25
    assert weights[Dimension.structure] == 0.20
    assert weights[Dimension.messaging] == 0.20
    assert weights[Dimension.differentiation] == 0.20
    assert weights[Dimension.investor_engagement] == 0.15


@pytest.mark.parametrize(
    "scores,expected",
    [
        ({d.key: 5 for d in DIMENSIONS}, 100),
        ({d.key: 0 for d in DIMENSIONS}, 0),
        ({d.key: 3 for d in DIMENSIONS}, 60),
        ({d.key: 4 for d in DIMENSIONS}, 80),
        (
            {
                Dimension.clarity: 4,
                Dimension.structure: 3,
                Dimension.messaging: 3,
                Dimension.differentiation: 2,
                Dimension.investor_engagement: 2,
            },
            58,
        ),
    ],
)
def test_aggregate_overall(scores, expected):
    assert aggregate_overall(scores) == expected


def test_aggregate_accepts_string_keys():
    scores = {d.key.value: 3 for d in DIMENSIONS}
    assert aggregate_overall(scores) == 60


def test_aggregate_rejects_missing_dimension():
    scores = {d.key: 3 for d in DIMENSIONS if d.key != Dimension.clarity}
    with pytest.raises(ValueError, match="missing scores"):
        aggregate_overall(scores)


@pytest.mark.parametrize(
    "overall,band",
    [
        (0, "Rebuild"),
        (39, "Rebuild"),
        (40, "Major revision"),
        (59, "Major revision"),
        (60, "Tighten"),
        (79, "Tighten"),
        (80, "Investor-ready"),
        (100, "Investor-ready"),
    ],
)
def test_band_boundaries(overall, band):
    assert band_for(overall) == band


def test_bands_cover_zero_to_hundred_without_gaps():
    covered = []
    for band in BANDS:
        covered.extend(range(band.lo, band.hi + 1))
    assert covered == list(range(0, 101))


# --- Schemas -----------------------------------------------------------------


def test_valid_payload_round_trips():
    payload = make_evaluation_payload()
    restored = EvaluationPayload.model_validate(payload.model_dump())
    assert restored == payload


def test_payload_json_schema_is_generatable():
    # The payload schema doubles as the Stage B tool input schema; it must serialize.
    schema = EvaluationPayload.model_json_schema()
    assert schema["type"] == "object"
    assert "dimensions" in schema["properties"]


def test_rejects_four_dimensions():
    payload = make_evaluation_payload()
    four = payload.dimensions[:4]
    with pytest.raises(ValidationError, match="exactly the 5 dimensions"):
        make_evaluation_payload(dimensions=four)


def test_rejects_duplicate_dimension():
    dupes = [make_dimension_result(Dimension.clarity) for _ in range(5)]
    with pytest.raises(ValidationError, match="exactly the 5 dimensions"):
        make_evaluation_payload(dimensions=dupes)


def test_rejects_two_rewrites():
    payload = make_evaluation_payload()
    with pytest.raises(ValidationError, match="exactly the 3 rewrites"):
        make_evaluation_payload(rewrites=payload.rewrites[:2])


def test_rejects_score_above_five():
    with pytest.raises(ValidationError):
        make_dimension_result(Dimension.clarity, score=6)


def test_rejects_zero_evidence():
    from app.schemas import DimensionResult

    valid = make_dimension_result(Dimension.clarity).model_dump()
    with pytest.raises(ValidationError):  # evidence has min_length=1
        DimensionResult.model_validate({**valid, "evidence": []})


def test_rejects_single_fix():
    from app.schemas import DimensionResult

    valid = make_dimension_result(Dimension.clarity).model_dump()
    with pytest.raises(ValidationError):  # fixes has min_length=2
        DimensionResult.model_validate({**valid, "fixes": ["only one"]})


def test_rejects_headline_over_140_chars():
    with pytest.raises(ValidationError):
        make_evaluation_payload(headline="x" * 141)


def test_rejects_duplicate_missing_slide_types():
    from app.rubric import SlideType

    with pytest.raises(ValidationError, match="duplicates"):
        make_evaluation_payload(missing_slide_types=[SlideType.gtm, SlideType.gtm])
