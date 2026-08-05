"""Pydantic v2 data contracts for the evaluation pipeline (Raskin framework, v2).

These schemas are the interface between the LLM and the rest of the app. Stage A returns
``SlideRecord``s; Stage B is forced, via tool use, to emit an ``EvaluationPayload`` — the
payload's JSON schema *is* the tool's input schema.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.rubric import RaskinElement, SlideType, TextDensity

SlideNumber = Annotated[int, Field(ge=1)]

# Deliberately tolerant models. Every hard-validation failure we hit on real decks came from
# a too-strict schema; the model's descriptive fields should never block a report. Unknown
# keys are ignored, text fields default to empty, and scores are clamped in code (finalize).


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


class ObstacleGift(BaseModel):
    """One paired obstacle → magic gift, from Raskin element 4."""

    model_config = ConfigDict(extra="ignore")

    obstacle: str = ""
    gift: str = ""
    assessment: str = ""  # how well the deck pairs them (Problem -> Solution -> Outcome)


class ElementResult(BaseModel):
    """Score, evaluation, and recommendation for one Raskin element."""

    model_config = ConfigDict(extra="ignore")

    element: RaskinElement
    score: float = 0.0  # 0-10; clamped in finalize_scores
    summary: str = ""  # short line for the scorecard table
    evaluation: str = ""  # narrative assessment
    recommendation: str = ""  # concrete, specific advice


class RebuildSlide(BaseModel):
    """One slide in the suggested 'If I Were Rebuilding This Deck' flow."""

    model_config = ConfigDict(extra="ignore")

    slides: str = ""  # e.g. "1", "3-5", "8-10"
    label: str = ""  # e.g. "The Enemy", "Why Now"
    line: str = ""  # the one-line message that slide should deliver


class EvaluationPayload(BaseModel):
    """The complete Raskin evaluation. This schema doubles as the Stage B tool input."""

    model_config = ConfigDict(extra="ignore")

    company_name: str = ""
    overall_assessment: str = ""  # the opening narrative paragraph
    overall_score: float = 0.0  # recomputed in Python as the mean of the elements
    elements: list[ElementResult]  # exactly 5, one per RaskinElement
    obstacles_and_gifts: list[ObstacleGift] = Field(default_factory=list)
    rebuild_flow: list[RebuildSlide] = Field(default_factory=list)

    @field_validator("elements")
    @classmethod
    def _exactly_five_distinct_elements(cls, value: list[ElementResult]) -> list[ElementResult]:
        keys = [e.element for e in value]
        if len(keys) != 5 or set(keys) != set(RaskinElement):
            present = sorted(k.value for k in set(keys))
            raise ValueError(
                f"exactly the 5 Raskin elements are required, each once; got {present}"
            )
        return value
