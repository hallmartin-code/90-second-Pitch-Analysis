"""The evaluation rubric as structured, versionable, unit-testable data.

Kept out of prompt strings deliberately (SPEC §7.1): the dimensions, weights, score
anchors, canonical slide types, buzzword list, and band boundaries all live here so they
can be tested and versioned. Stage B injects this data into its prompt; verification and
aggregation import the functions below.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum

RUBRIC_VERSION = "1.0"


# --- Enumerations shared across schemas and rubric ---------------------------


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


class Dimension(str, Enum):
    """The five scored evaluation dimensions."""

    clarity = "clarity"
    structure = "structure"
    messaging = "messaging"
    differentiation = "differentiation"
    investor_engagement = "investor_engagement"


class RewriteLabel(str, Enum):
    """The three rewrites the report must deliver."""

    one_liner = "one_liner"
    cover_slide_copy = "cover_slide_copy"
    thirty_second_verbal = "thirty_second_verbal"


class SlideVerdict(str, Enum):
    """Per-slide verdict in the slide-by-slide review."""

    keep = "keep"
    tighten = "tighten"
    rebuild = "rebuild"
    cut = "cut"
    missing = "missing"


class TextDensity(str, Enum):
    sparse = "sparse"
    balanced = "balanced"
    dense = "dense"


# --- Dimension specifications with score anchors -----------------------------


@dataclass(frozen=True, slots=True)
class Anchor:
    """Score-band language a judgment must cite. Bands are 0-1, 2-3, 4-5."""

    low: str  # 0-1
    mid: str  # 2-3
    high: str  # 4-5


@dataclass(frozen=True, slots=True)
class DimensionSpec:
    key: Dimension
    title: str
    weight: float
    question: str
    anchors: Anchor


DIMENSIONS: tuple[DimensionSpec, ...] = (
    DimensionSpec(
        key=Dimension.clarity,
        title="Clarity",
        weight=0.25,
        question="Can a smart generalist say what this company does after the first two slides?",
        anchors=Anchor(
            low="Reader cannot state what the company does.",
            mid="Understandable only after the whole deck; undefined jargon; the 'what' "
            "arrives after slide 5.",
            high="The cover or slide 2 names who it's for and what it does in one concrete "
            "sentence.",
        ),
    ),
    DimensionSpec(
        key=Dimension.structure,
        title="Structure",
        weight=0.20,
        question="Does the deck follow a load-bearing order?",
        anchors=Anchor(
            low="No discernible order, or opens with the technology or team.",
            mid="Beats present but out of order or wildly disproportionate.",
            high="All load-bearing slides present, correctly ordered, proportionate.",
        ),
    ),
    DimensionSpec(
        key=Dimension.messaging,
        title="Messaging",
        weight=0.20,
        question="Is there one memorable claim, and is it supported?",
        anchors=Anchor(
            low="No identifiable core claim, or several competing ones.",
            mid="A claim exists but is generic or unsupported.",
            high="One sharp claim, in the founder's own vocabulary, immediately backed by a "
            "specific number, named customer, or observed fact.",
        ),
    ),
    DimensionSpec(
        key=Dimension.differentiation,
        title="Differentiation",
        weight=0.20,
        question="Why this approach and not the obvious alternative, including doing nothing?",
        anchors=Anchor(
            low="No alternative acknowledged.",
            mid="Names competitors but differentiates on trivially copyable features, or shows "
            "a competitor matrix rigged so they win every row.",
            high="Names the real alternative (often the status quo or a spreadsheet), states "
            "the wedge in one line, and points at something structurally hard to copy.",
        ),
    ),
    DimensionSpec(
        key=Dimension.investor_engagement,
        title="Investor Engagement",
        weight=0.15,
        question="Does it answer what an investor is silently asking?",
        anchors=Anchor(
            low="Reads as a product brochure.",
            mid="Some questions answered, ask vague or absent.",
            high="Anticipates the questions, and the ask is specific (amount, use of funds, the "
            "milestone it unlocks).",
        ),
    ),
)

DIMENSION_BY_KEY: dict[Dimension, DimensionSpec] = {d.key: d for d in DIMENSIONS}


# --- The expected narrative arc ---------------------------------------------

# Load-bearing order an investor deck is expected to follow (SPEC §7.1 Structure).
EXPECTED_ARC: tuple[SlideType, ...] = (
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
)


# --- Buzzwords (curated deterministic signal) --------------------------------

# The canonical hype words called out in SPEC §6.1. `metrics.py` compiles the patterns.
BUZZWORDS: tuple[str, ...] = (
    "synergy",
    "disruptive",
    "seamless",
    "revolutionary",
    "best-in-class",
    "next-generation",
    "paradigm",
    "leverage",  # flagged as a verb; the deterministic pass counts all occurrences
)

# (canonical label, case-insensitive regex). Order does not matter; each is counted once
# per match. "leverage" is counted wherever it appears — the deterministic layer cannot do
# part-of-speech tagging, and over-counting a genuine noun is a tolerable false positive.
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


# --- Bands -------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Band:
    name: str
    lo: int  # inclusive
    hi: int  # inclusive


BANDS: tuple[Band, ...] = (
    Band("Rebuild", 0, 39),
    Band("Major revision", 40, 59),
    Band("Tighten", 60, 79),
    Band("Investor-ready", 80, 100),
)


# --- Aggregation -------------------------------------------------------------


def dimension_weights() -> dict[Dimension, float]:
    """Return the weight of each dimension, keyed by :class:`Dimension`."""
    return {d.key: d.weight for d in DIMENSIONS}


def aggregate_overall(scores: Mapping[Dimension | str, int]) -> int:
    """Combine 0-5 dimension scores into a 0-100 overall score.

    ``overall = round(sum(score * weight) / 5 * 100)`` (SPEC §7.1). All five dimensions
    must be present.
    """
    normalized: dict[Dimension, int] = {}
    for key, value in scores.items():
        dim = key if isinstance(key, Dimension) else Dimension(key)
        normalized[dim] = value

    missing = [d.key for d in DIMENSIONS if d.key not in normalized]
    if missing:
        raise ValueError(f"missing scores for dimensions: {[d.value for d in missing]}")

    weighted = sum(normalized[d.key] * d.weight for d in DIMENSIONS)
    return round(weighted / 5 * 100)


def band_for(overall: int) -> str:
    """Return the band name for a 0-100 overall score."""
    for band in BANDS:
        if band.lo <= overall <= band.hi:
            return band.name
    raise ValueError(f"overall score out of range: {overall}")


def render_rubric_text() -> str:
    """Render the rubric as prose for injection into the Stage B system prompt.

    The rubric lives here as structured data; this is the single place it is flattened to
    text so the prompt and the tests share one source of truth.
    """
    parts: list[str] = [
        f"RUBRIC v{RUBRIC_VERSION}. Score each dimension 0-5 as a whole number, against "
        "these anchors ONLY. Never award a 5 for effort.\n"
    ]
    for spec in DIMENSIONS:
        parts.append(f"## {spec.title}  (weight {spec.weight:.2f}, key: {spec.key.value})")
        parts.append(f"Question: {spec.question}")
        parts.append(f"  0-1 -> {spec.anchors.low}")
        parts.append(f"  2-3 -> {spec.anchors.mid}")
        parts.append(f"  4-5 -> {spec.anchors.high}")
        parts.append("")
    arc = " -> ".join(t.value for t in EXPECTED_ARC)
    parts.append(f"Expected narrative arc (for Structure): {arc}")
    parts.append("Bands: " + " | ".join(f"{b.lo}-{b.hi} {b.name}" for b in BANDS))
    parts.append("Hype words to distrust (flag, don't reward): " + ", ".join(BUZZWORDS))
    return "\n".join(parts)
