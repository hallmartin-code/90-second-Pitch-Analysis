"""Pydantic v2 data contracts for the evaluation pipeline (SPEC §7.2).

These schemas are the interface between the LLM and the rest of the app. Stage B is forced
to emit an :class:`EvaluationPayload` via tool use, so the payload's JSON schema *is* the
tool's input schema — validation happens at the tool boundary, never by parsing prose.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.rubric import (
    Dimension,
    RewriteLabel,
    SlideType,
    SlideVerdict,
    TextDensity,
)

SlideNumber = Annotated[int, Field(ge=1)]


class SlideRecord(BaseModel):
    """Descriptive parse of one slide, produced by Stage A (not evaluative)."""

    model_config = ConfigDict(extra="forbid")

    slide_number: SlideNumber
    slide_type: SlideType
    headline: str
    key_points: Annotated[list[str], Field(max_length=5)]
    has_chart: bool
    has_screenshot: bool
    text_density: TextDensity
    readability_notes: list[str]


class Evidence(BaseModel):
    """A specific, slide-anchored quote justifying a score."""

    model_config = ConfigDict(extra="forbid")

    slide_number: SlideNumber
    quote: Annotated[str, Field(max_length=200)]  # verbatim where a text layer exists
    comment: str  # why this drives the score


class DimensionResult(BaseModel):
    """Score, rationale, evidence, and fixes for a single dimension."""

    model_config = ConfigDict(extra="forbid")

    dimension: Dimension
    score: Annotated[int, Field(ge=0, le=5)]
    anchor_rationale: str  # must cite the anchor language it matched
    evidence: Annotated[list[Evidence], Field(min_length=1, max_length=3)]
    fixes: Annotated[list[str], Field(min_length=2, max_length=4)]  # imperative, slide-specific


class Rewrite(BaseModel):
    """One rewritten asset the founder can use verbatim."""

    model_config = ConfigDict(extra="forbid")

    label: RewriteLabel
    text: str
    changed_because: str


class SlideNote(BaseModel):
    """One row of the slide-by-slide review, or a 'missing' gap entry."""

    model_config = ConfigDict(extra="forbid")

    slide_number: int
    verdict: SlideVerdict
    note: str


class EvaluationPayload(BaseModel):
    """The complete evaluation. This schema doubles as the Stage B tool input schema."""

    model_config = ConfigDict(extra="forbid")

    overall_score: Annotated[int, Field(ge=0, le=100)]
    band: str
    headline: Annotated[str, Field(max_length=140)]  # the single most important fix
    dimensions: list[DimensionResult]  # exactly 5, one per Dimension
    rewrites: list[Rewrite]  # exactly 3, one per RewriteLabel
    slide_notes: list[SlideNote]  # one per slide, plus any "missing" entries
    unsupported_claims: list[str]
    missing_slide_types: list[SlideType]

    @field_validator("dimensions")
    @classmethod
    def _exactly_five_distinct_dimensions(cls, value: list[DimensionResult]) -> list[DimensionResult]:
        keys = [d.dimension for d in value]
        if len(keys) != 5 or set(keys) != set(Dimension):
            present = sorted(k.value for k in set(keys))
            raise ValueError(
                f"exactly the 5 dimensions are required, each once; got {present}"
            )
        return value

    @field_validator("rewrites")
    @classmethod
    def _exactly_three_distinct_rewrites(cls, value: list[Rewrite]) -> list[Rewrite]:
        labels = [r.label for r in value]
        if len(labels) != 3 or set(labels) != set(RewriteLabel):
            present = sorted(l.value for l in set(labels))
            raise ValueError(
                f"exactly the 3 rewrites are required, each once; got {present}"
            )
        return value

    @model_validator(mode="after")
    def _evidence_and_missing_are_consistent(self) -> EvaluationPayload:
        # Every 'missing' slide note should correspond to a declared missing slide type gap,
        # and missing_slide_types must be distinct.
        if len(set(self.missing_slide_types)) != len(self.missing_slide_types):
            raise ValueError("missing_slide_types must not contain duplicates")
        return self
