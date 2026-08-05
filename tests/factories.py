"""Builders for valid schema objects, reused across schema, report, and route tests."""

from __future__ import annotations

from app.rubric import (
    RASKIN_ELEMENTS,
    RaskinElement,
    SlideType,
    TextDensity,
    aggregate_overall,
)
from app.schemas import (
    ElementResult,
    EvaluationPayload,
    ObstacleGift,
    RebuildSlide,
    SlideRecord,
)

_SCORES: dict[RaskinElement, float] = {
    RaskinElement.name_the_enemy: 7.5,
    RaskinElement.why_now: 6.5,
    RaskinElement.promised_land: 8.5,
    RaskinElement.obstacles_and_gifts: 8.0,
    RaskinElement.present_evidence: 8.5,
}


def make_element_result(element: RaskinElement, score: float | None = None) -> ElementResult:
    return ElementResult(
        element=element,
        score=_SCORES[element] if score is None else score,
        summary=f"{element.value.replace('_', ' ').title()} — solid but sharpen the framing.",
        evaluation=f"The deck addresses {element.value.replace('_', ' ')} but could lead with it earlier.",
        recommendation=f"Strengthen {element.value.replace('_', ' ')} with one concrete, specific claim.",
    )


def make_slide_records() -> list[SlideRecord]:
    types = [
        SlideType.cover, SlideType.problem, SlideType.why_now, SlideType.solution,
        SlideType.product, SlideType.market, SlideType.traction, SlideType.business_model,
        SlideType.competition, SlideType.team, SlideType.ask,
    ]
    return [
        SlideRecord(
            slide_number=i + 1,
            slide_type=slide_type,
            headline=slide_type.value.replace("_", " ").title(),
            key_points=["A concrete point drawn from the slide."],
            has_chart=slide_type in {SlideType.market, SlideType.traction, SlideType.financials},
            has_screenshot=slide_type == SlideType.product,
            text_density=TextDensity.balanced,
            readability_notes=[],
        )
        for i, slide_type in enumerate(types)
    ]


def make_evaluation_payload(**overrides: object) -> EvaluationPayload:
    """Return a fully valid Raskin payload. Pass overrides to construct invalid variants."""
    data: dict[str, object] = dict(
        company_name="Northwind Logistics",
        overall_assessment=(
            "Northwind has the ingredients of a strong pitch — a real problem, a differentiated "
            "platform, and traction — but reads more like a company overview than a strategic "
            "narrative built around a single enemy and an explicit why-now."
        ),
        overall_score=aggregate_overall(_SCORES),
        elements=[make_element_result(spec.key) for spec in RASKIN_ELEMENTS],
        obstacles_and_gifts=[
            ObstacleGift(obstacle="Fragmented status quo", gift="A unifying platform", assessment="Clear pairing."),
            ObstacleGift(obstacle="Legacy model doesn't scale", gift="A distributed network", assessment="Strong."),
            ObstacleGift(obstacle="Decisions lack insight", gift="An intelligence layer", assessment="Underdeveloped."),
        ],
        rebuild_flow=[
            RebuildSlide(slides="1", label="Opening", line="State the one-line vision."),
            RebuildSlide(slides="2", label="The Enemy", line="Name one dominant villain."),
            RebuildSlide(slides="3", label="Why Now", line="State the shift explicitly."),
        ],
    )
    data.update(overrides)
    return EvaluationPayload(**data)
