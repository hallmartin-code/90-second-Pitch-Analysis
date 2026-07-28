"""Builders for valid schema objects, reused across schema, report, and route tests.

A single source of a known-good ``EvaluationPayload`` keeps the tests honest: every test
starts from something valid and mutates one thing to prove a constraint bites.
"""

from __future__ import annotations

from app.rubric import Dimension, RewriteLabel, SlideType, SlideVerdict, TextDensity
from app.schemas import (
    DimensionResult,
    Evidence,
    EvaluationPayload,
    Rewrite,
    SlideNote,
    SlideRecord,
)

_DIMENSION_BLURB = {
    Dimension.clarity: "The cover names the customer and the job in one sentence.",
    Dimension.structure: "All load-bearing beats are present and in order.",
    Dimension.messaging: "One sharp claim, backed by a concrete traction number.",
    Dimension.differentiation: "Names the status-quo alternative and a hard-to-copy wedge.",
    Dimension.investor_engagement: "The ask is specific: amount, use of funds, milestone.",
}


def make_dimension_result(dimension: Dimension, score: int = 4) -> DimensionResult:
    return DimensionResult(
        dimension=dimension,
        score=score,
        anchor_rationale=f"Matches the high anchor: {_DIMENSION_BLURB[dimension]}",
        evidence=[
            Evidence(
                slide_number=2,
                quote="Real-time freight visibility for mid-market shippers",
                comment="States who it's for and what it does on the cover.",
            )
        ],
        fixes=[
            "Move the traction number onto slide 2.",
            "Cut the jargon in the subhead.",
        ],
    )


def make_slide_record(slide_number: int = 1) -> SlideRecord:
    return SlideRecord(
        slide_number=slide_number,
        slide_type=SlideType.cover,
        headline="Northwind Logistics",
        key_points=["Real-time freight visibility for mid-market shippers"],
        has_chart=False,
        has_screenshot=False,
        text_density=TextDensity.sparse,
        readability_notes=[],
    )


def make_slide_records() -> list[SlideRecord]:
    """Eleven records mirroring the good-deck fixture, for the slide-by-slide table."""
    types = [
        SlideType.cover,
        SlideType.problem,
        SlideType.why_now,
        SlideType.solution,
        SlideType.product,
        SlideType.market,
        SlideType.traction,
        SlideType.business_model,
        SlideType.competition,
        SlideType.team,
        SlideType.ask,
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
    """Return a fully valid payload. Pass field overrides to construct invalid variants."""
    data: dict[str, object] = dict(
        overall_score=72,
        band="Tighten",
        headline="Put the one-line 'what it does' on the cover and lead traction earlier.",
        dimensions=[make_dimension_result(dim) for dim in Dimension],
        rewrites=[
            Rewrite(
                label=RewriteLabel.one_liner,
                text="Northwind gives mid-market shippers live freight visibility across 200 carriers.",
                changed_because="Replaces the abstract tagline with the customer and the job.",
            ),
            Rewrite(
                label=RewriteLabel.cover_slide_copy,
                text="Northwind Logistics — live freight visibility for mid-market shippers.",
                changed_because="Names the segment on the cover so the 'what' lands immediately.",
            ),
            Rewrite(
                label=RewriteLabel.thirty_second_verbal,
                text=(
                    "Mid-market shippers still track freight by phone. Northwind aggregates "
                    "live status across 200 carriers and alerts them before a delay becomes a "
                    "missed delivery. We're at $40k MRR, growing 18% a month, raising $2.5M."
                ),
                changed_because="Opens on the pain, states the wedge, ends on traction and the ask.",
            ),
        ],
        slide_notes=[
            SlideNote(slide_number=1, verdict=SlideVerdict.tighten, note="Strong cover; add the segment."),
            SlideNote(slide_number=2, verdict=SlideVerdict.keep, note="Problem is concrete."),
            SlideNote(slide_number=0, verdict=SlideVerdict.missing, note="No dedicated GTM slide."),
        ],
        unsupported_claims=["'Better than everyone else' has no evidence on the slide."],
        missing_slide_types=[SlideType.gtm],
    )
    data.update(overrides)
    return EvaluationPayload(**data)
