"""Canned, valid LLM outputs for ``FAKE_LLM=1`` (SPEC §9).

Lets the whole pipeline — evaluator, report, web flow — run and be tested without an API
key or spending tokens. The payload is deliberately built from the deck's *real* extracted
text so that Python-side quote verification passes on any deck, not just a fixture.
"""

from __future__ import annotations

from app.ingest import IngestedDeck
from app.metrics import DeckMetrics
from app.rubric import (
    DIMENSION_BY_KEY,
    EXPECTED_ARC,
    Dimension,
    RewriteLabel,
    SlideType,
    SlideVerdict,
    TextDensity,
    aggregate_overall,
    band_for,
)
from app.schemas import (
    DimensionResult,
    Evidence,
    EvaluationPayload,
    Rewrite,
    SlideNote,
    SlideRecord,
)

# A fixed, plausible score profile: leverage (5-score)*weight then differs per dimension, so
# the report's "top three fixes" surface distinct items.
_FAKE_SCORES: dict[Dimension, int] = {
    Dimension.clarity: 4,
    Dimension.structure: 3,
    Dimension.messaging: 3,
    Dimension.differentiation: 2,
    Dimension.investor_engagement: 2,
}


def _first_line(text: str, limit: int = 180) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped[:limit]
    return ""


def fake_slide_records(deck: IngestedDeck) -> list[SlideRecord]:
    """One descriptive record per page, typed along the expected arc."""
    records: list[SlideRecord] = []
    for page in deck.pages:
        index = page.number - 1
        slide_type = EXPECTED_ARC[index] if index < len(EXPECTED_ARC) else SlideType.unclear
        points = [line.strip() for line in page.text.splitlines() if line.strip()][:3]
        records.append(
            SlideRecord(
                slide_number=page.number,
                slide_type=slide_type,
                headline=_first_line(page.text) or f"Slide {page.number}",
                key_points=[p[:200] for p in points] or ["(no text layer detected)"],
                has_chart=False,
                has_screenshot=False,
                text_density=TextDensity.balanced,
                readability_notes=[],
            )
        )
    return records


def fake_evaluation_payload(deck: IngestedDeck, metrics: DeckMetrics) -> EvaluationPayload:
    """A valid, deterministic payload whose evidence quotes match the deck's text layer."""
    first_page = deck.pages[0] if deck.pages else None
    if deck.has_text_layer and first_page is not None:
        quote = _first_line(first_page.text, limit=190) or "Opening slide"
    else:
        quote = "Opening slide (transcribed from image)"

    dimensions: list[DimensionResult] = []
    for spec in DIMENSION_BY_KEY.values():
        score = _FAKE_SCORES[spec.key]
        band_word = "high" if score >= 4 else "mid" if score >= 2 else "low"
        dimensions.append(
            DimensionResult(
                dimension=spec.key,
                score=score,
                anchor_rationale=(
                    f"[FAKE_LLM] Matched the {band_word} anchor for {spec.title}: "
                    f"{getattr(spec.anchors, band_word)}"
                ),
                evidence=[
                    Evidence(
                        slide_number=1,
                        quote=quote,
                        comment=f"Drives the {spec.title} score (canned FAKE_LLM evidence).",
                    )
                ],
                fixes=[
                    f"Sharpen the {spec.title.lower()} on slide 1.",
                    f"Add one concrete number to support the {spec.title.lower()} claim.",
                ],
            )
        )

    overall = aggregate_overall(_FAKE_SCORES)
    slide_notes = [
        SlideNote(
            slide_number=page.number,
            verdict=SlideVerdict.keep if page.number % 2 else SlideVerdict.tighten,
            note=f"[FAKE_LLM] Review slide {page.number}.",
        )
        for page in deck.pages
    ]
    slide_notes.append(
        SlideNote(slide_number=0, verdict=SlideVerdict.missing, note="[FAKE_LLM] No GTM slide.")
    )

    return EvaluationPayload(
        overall_score=overall,
        band=band_for(overall),
        headline="[FAKE_LLM] Put the one-line 'what it does' on the cover and lead with traction.",
        dimensions=dimensions,
        rewrites=[
            Rewrite(
                label=RewriteLabel.one_liner,
                text="[FAKE_LLM] We help <who> do <what> so they can <outcome>.",
                changed_because="Replaces an abstract tagline with the customer and the job.",
            ),
            Rewrite(
                label=RewriteLabel.cover_slide_copy,
                text="[FAKE_LLM] <Company> — <one concrete sentence of what it does>.",
                changed_because="Names the segment on the cover so the 'what' lands immediately.",
            ),
            Rewrite(
                label=RewriteLabel.thirty_second_verbal,
                text=(
                    "[FAKE_LLM] Open on the pain, state the wedge in one line, end on your "
                    "strongest traction number and the specific ask."
                ),
                changed_because="Front-loads the opening so it lands with an investor in 30 seconds.",
            ),
        ],
        slide_notes=slide_notes,
        unsupported_claims=["[FAKE_LLM] Any superlative with no evidence on its slide."],
        missing_slide_types=[SlideType.gtm],
    )
