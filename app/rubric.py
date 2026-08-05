"""The evaluation framework as structured, versionable, unit-testable data.

Version 2.0 scores a deck against **Andy Raskin's strategic-narrative framework** — the
five elements a category-defining pitch is built on — each 0-10. Overall alignment is the
mean of the five. The Stage A slide-typing vocabulary (``SlideType``, ``TextDensity``) and
the deterministic buzzword list live here too.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum

RUBRIC_VERSION = "2.0-raskin"


# --- Stage A slide vocabulary ------------------------------------------------


class SlideType(str, Enum):
    """Canonical slide roles a deck slide can be classified as."""

    cover = "cover"
    problem = "problem"
    solution = "solution"
    product = "product"
    why_now = "why_now"
    market = "market"
    business_model = "business_model"
    traction = "traction"
    competition = "competition"
    gtm = "gtm"
    team = "team"
    financials = "financials"
    ask = "ask"
    appendix = "appendix"
    unclear = "unclear"


class TextDensity(str, Enum):
    sparse = "sparse"
    balanced = "balanced"
    dense = "dense"


# --- The Raskin framework ----------------------------------------------------


class RaskinElement(str, Enum):
    """The five elements of Andy Raskin's strategic narrative."""

    name_the_enemy = "name_the_enemy"
    why_now = "why_now"
    promised_land = "promised_land"
    obstacles_and_gifts = "obstacles_and_gifts"
    present_evidence = "present_evidence"


@dataclass(frozen=True, slots=True)
class RaskinElementSpec:
    key: RaskinElement
    number: int
    title: str
    guidance: str  # what the element means; injected into the Stage B prompt


RASKIN_ELEMENTS: tuple[RaskinElementSpec, ...] = (
    RaskinElementSpec(
        key=RaskinElement.name_the_enemy,
        number=1,
        title="Name the Enemy",
        guidance=(
            "The strongest pitches identify a single clear 'enemy' — a status quo, incumbent "
            "model, or way of thinking — that customers and investors can rally against. Score "
            "high when there is ONE dominant, memorable villain that every slide reinforces; "
            "score low when the deck lists many diffuse problems with no singular adversary."
        ),
    ),
    RaskinElementSpec(
        key=RaskinElement.why_now,
        number=2,
        title="Why Now?",
        guidance=(
            "A compelling, explicit reason the market is changing right now — the shift that "
            "makes this company inevitable today and impossible five years ago. Score high when "
            "urgency is stated explicitly with concrete technological, market, or regulatory "
            "shifts; score low when urgency is only implied."
        ),
    ),
    RaskinElementSpec(
        key=RaskinElement.promised_land,
        number=3,
        title="Tease the Promised Land",
        guidance=(
            "A vivid, aspirational vision of the future the company enables — introduced early "
            "and reinforced throughout. Score high when the future state is specific and lands "
            "by slide 1-2; score low when the vision is buried mid-deck or stays vague."
        ),
    ),
    RaskinElementSpec(
        key=RaskinElement.obstacles_and_gifts,
        number=4,
        title="Three Obstacles and Three Gifts",
        guidance=(
            "The core storytelling engine: name the obstacles between today and the promised "
            "land, then present the company's capabilities as 'magic gifts' that overcome each. "
            "Score high when obstacles and gifts are explicitly paired as Problem -> Solution "
            "-> Outcome; score low when investors must assemble that narrative themselves."
        ),
    ),
    RaskinElementSpec(
        key=RaskinElement.present_evidence,
        number=5,
        title="Present Evidence",
        guidance=(
            "Proof the company can make the story come true: clinical or technical validation, "
            "traction, revenue, named customers, partnerships, and a credible team. Score high "
            "when strong, specific proof appears early; score low when evidence is thin or "
            "arrives only at the end."
        ),
    ),
)

ELEMENT_BY_KEY: dict[RaskinElement, RaskinElementSpec] = {e.key: e for e in RASKIN_ELEMENTS}


# --- Buzzwords (deterministic signal) ----------------------------------------

BUZZWORDS: tuple[str, ...] = (
    "synergy",
    "disruptive",
    "seamless",
    "revolutionary",
    "best-in-class",
    "next-generation",
    "paradigm",
    "leverage",
)

BUZZWORD_PATTERNS: tuple[tuple[str, str], ...] = (
    ("synergy", r"synerg(?:y|ies|istic)"),
    ("disruptive", r"disrupt(?:ive|ion|ing|ed|s)?"),
    ("seamless", r"seamless(?:ly)?"),
    ("revolutionary", r"revolution(?:ary|ize|izing|ised|ized)"),
    ("best-in-class", r"best[\s-]in[\s-]class"),
    ("next-generation", r"next[\s-]gen(?:eration)?"),
    ("paradigm", r"paradigm(?:s)?"),
    ("leverage", r"leverag(?:e|es|ed|ing)"),
)


# --- Aggregation -------------------------------------------------------------


def aggregate_overall(scores: Mapping[RaskinElement | str, float]) -> float:
    """Mean of the five element scores (0-10), rounded to one decimal.

    Example: 7.5, 6.5, 8.5, 8, 8.5 -> 7.8. All five elements must be present.
    """
    normalized: dict[RaskinElement, float] = {}
    for key, value in scores.items():
        element = key if isinstance(key, RaskinElement) else RaskinElement(key)
        normalized[element] = float(value)

    missing = [e.key for e in RASKIN_ELEMENTS if e.key not in normalized]
    if missing:
        raise ValueError(f"missing scores for elements: {[e.value for e in missing]}")

    total = sum(normalized[e.key] for e in RASKIN_ELEMENTS)
    return round(total / len(RASKIN_ELEMENTS), 1)


def render_rubric_text() -> str:
    """Render the Raskin framework as prose for the Stage B system prompt."""
    parts: list[str] = [
        f"FRAMEWORK v{RUBRIC_VERSION} — Andy Raskin's strategic narrative. Score each of the "
        "five elements 0-10 (halves allowed), against this guidance ONLY.\n"
    ]
    for spec in RASKIN_ELEMENTS:
        parts.append(f"## {spec.number}. {spec.title}  (key: {spec.key.value})")
        parts.append(spec.guidance)
        parts.append("")
    parts.append(
        "Scoring guide: 0-3 absent or badly muddled; 4-6 present but generic, implicit, or "
        "out of order; 7-8 strong and clear; 9-10 exceptional and reinforced throughout the "
        "deck. Overall alignment is the mean of the five, which will be recomputed."
    )
    return "\n".join(parts)
