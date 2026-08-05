"""Canned, valid LLM output for ``FAKE_LLM=1`` (Raskin schema).

Lets the whole pipeline — evaluator, report, web flow — run and be tested without an API
key or spending tokens.
"""

from __future__ import annotations

from app.ingest import IngestedDeck
from app.metrics import DeckMetrics
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

_FAKE_SCORES: dict[RaskinElement, float] = {
    RaskinElement.name_the_enemy: 7.5,
    RaskinElement.why_now: 6.5,
    RaskinElement.promised_land: 8.5,
    RaskinElement.obstacles_and_gifts: 8.0,
    RaskinElement.present_evidence: 8.5,
}

_FAKE_SUMMARY: dict[RaskinElement, str] = {
    RaskinElement.name_the_enemy: "Villain is present but competes with several others.",
    RaskinElement.why_now: "Urgency is implied, not stated — the weakest element.",
    RaskinElement.promised_land: "Clear, aspirational future state.",
    RaskinElement.obstacles_and_gifts: "Obstacles and gifts exist but aren't explicitly paired.",
    RaskinElement.present_evidence: "Strong, specific proof and named partners.",
}


def _first_line(text: str, limit: int = 120) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped[:limit]
    return ""


def fake_slide_records(deck: IngestedDeck) -> list[SlideRecord]:
    """One descriptive record per page, typed along a plausible arc."""
    arc = [
        SlideType.cover, SlideType.problem, SlideType.why_now, SlideType.solution,
        SlideType.product, SlideType.market, SlideType.traction, SlideType.business_model,
        SlideType.competition, SlideType.team, SlideType.ask,
    ]
    records: list[SlideRecord] = []
    for page in deck.pages:
        index = page.number - 1
        slide_type = arc[index] if index < len(arc) else SlideType.unclear
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


def fake_company_name(deck: IngestedDeck) -> str:
    """Best-effort company name from the cover slide's first line."""
    if deck.pages:
        first = _first_line(deck.pages[0].text, limit=80)
        if first:
            return first
    return "Sample Company"


def fake_evaluation_payload(deck: IngestedDeck, metrics: DeckMetrics) -> EvaluationPayload:
    """A valid, deterministic Raskin payload."""
    elements = [
        ElementResult(
            element=spec.key,
            score=_FAKE_SCORES[spec.key],
            summary=f"[FAKE_LLM] {_FAKE_SUMMARY[spec.key]}",
            evaluation=(
                f"[FAKE_LLM] {spec.title}: {spec.guidance} On this deck the element is "
                "present but would benefit from sharper framing."
            ),
            recommendation=f"[FAKE_LLM] Strengthen '{spec.title}' by leading with it earlier and "
            "making one concrete claim.",
        )
        for spec in RASKIN_ELEMENTS
    ]

    return EvaluationPayload(
        company_name=fake_company_name(deck),
        overall_assessment=(
            "[FAKE_LLM] This deck has the ingredients of a strong pitch, but reads more like a "
            "company overview than a strategic narrative. It explains what the company does "
            "well; it is less effective at building urgency and a single rallying enemy."
        ),
        overall_score=aggregate_overall(_FAKE_SCORES),
        elements=elements,
        obstacles_and_gifts=[
            ObstacleGift(
                obstacle="[FAKE_LLM] Fragmented status quo",
                gift="[FAKE_LLM] A unifying platform",
                assessment="Clear pairing, easy to follow.",
            ),
            ObstacleGift(
                obstacle="[FAKE_LLM] Legacy approach doesn't scale",
                gift="[FAKE_LLM] A distributed model",
                assessment="Strong and differentiated.",
            ),
            ObstacleGift(
                obstacle="[FAKE_LLM] Decisions lack insight",
                gift="[FAKE_LLM] An intelligence layer",
                assessment="Compelling but underdeveloped.",
            ),
        ],
        rebuild_flow=[
            RebuildSlide(slides="1", label="Opening", line="[FAKE_LLM] State the one-line vision."),
            RebuildSlide(slides="2", label="The Enemy", line="[FAKE_LLM] Name one dominant villain."),
            RebuildSlide(slides="3", label="Why Now", line="[FAKE_LLM] State the shift explicitly."),
            RebuildSlide(slides="4", label="Promised Land", line="[FAKE_LLM] Paint the future state."),
            RebuildSlide(slides="5-6", label="Obstacles & Gifts", line="[FAKE_LLM] Pair each problem with a gift."),
            RebuildSlide(slides="7", label="Proof", line="[FAKE_LLM] Lead with your strongest evidence."),
        ],
    )
