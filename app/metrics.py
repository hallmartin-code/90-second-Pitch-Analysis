"""Deterministic text signals computed without the LLM (SPEC §6.1).

These are facts the model cannot hallucinate — word counts, readability, buzzword hits,
unexpanded acronyms — passed into Stage B as ground truth to stabilize scoring. Everything
here reads the extracted text layer only; a scanned deck simply yields sparse metrics and
the vision model carries the evaluation.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field

import textstat

from app.rubric import BUZZWORD_PATTERNS

_WORD_RE = re.compile(r"\b[\w'-]+\b")
_SENTENCE_SPLIT_RE = re.compile(r"[.!?]+")
_ACRONYM_RE = re.compile(r"\b[A-Z][A-Z0-9]{1,5}\b")

# Common all-caps English words that are not acronyms; excluded from acronym detection.
_ACRONYM_STOPWORDS = frozenset(
    {
        "THE", "AND", "FOR", "YOU", "ARE", "OUR", "WHY", "NOW", "HOW", "WHO", "ALL",
        "NEW", "OUT", "GET", "USE", "WE", "US", "IT", "IN", "ON", "TO", "OF", "OR",
        "AN", "AT", "BE", "BY", "DO", "GO", "IF", "IS", "MY", "NO", "SO", "UP", "VS",
        "CEO", "CTO", "COO", "CFO",  # role titles: expected, not deck jargon to expand
    }
)

_COMPILED_BUZZWORDS: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (label, re.compile(rf"\b{pattern}\b", re.IGNORECASE)) for label, pattern in BUZZWORD_PATTERNS
)


@dataclass(slots=True)
class SlideMetrics:
    """Deterministic signals for a single slide."""

    slide_number: int
    word_count: int
    flesch_reading_ease: float | None
    longest_sentence_words: int
    buzzword_hits: dict[str, int]
    unexpanded_acronyms: list[str]


@dataclass(slots=True)
class DeckMetrics:
    """Aggregated deterministic signals for the whole deck."""

    slide_count: int
    total_words: int
    flesch_reading_ease: float | None
    buzzword_hits: dict[str, int]
    unexpanded_acronyms: list[str]
    slides: list[SlideMetrics] = field(default_factory=list)


def _word_count(text: str) -> int:
    return len(_WORD_RE.findall(text))


def _longest_sentence_words(text: str) -> int:
    longest = 0
    for sentence in _SENTENCE_SPLIT_RE.split(text):
        longest = max(longest, _word_count(sentence))
    return longest


def _flesch(text: str) -> float | None:
    """Flesch Reading Ease, or None when there is too little text to be meaningful."""
    if _word_count(text) < 3:
        return None
    try:
        return round(float(textstat.flesch_reading_ease(text)), 1)
    except (ValueError, ZeroDivisionError):
        return None


def _buzzword_hits(text: str) -> dict[str, int]:
    hits: dict[str, int] = {}
    for label, pattern in _COMPILED_BUZZWORDS:
        count = len(pattern.findall(text))
        if count:
            hits[label] = hits.get(label, 0) + count
    return hits


def _content_lines(text: str) -> list[str]:
    """Lines excluding shouty all-caps headings, which produce acronym false positives."""
    keep: list[str] = []
    for line in text.splitlines():
        words = [w for w in line.split() if any(c.isalpha() for c in w)]
        is_shouty_heading = len(words) >= 2 and all(w.isupper() for w in words)
        if not is_shouty_heading:
            keep.append(line)
    return keep


def _find_acronyms(text: str) -> set[str]:
    found: set[str] = set()
    for line in _content_lines(text):
        for token in _ACRONYM_RE.findall(line):
            if token not in _ACRONYM_STOPWORDS:
                found.add(token)
    return found


def _unexpanded_acronyms(text: str) -> list[str]:
    """Acronyms that never appear alongside a parenthetical expansion, deck-wide.

    An acronym is treated as *expanded* if either ``ACRONYM (`` or ``(ACRONYM)`` occurs
    anywhere — e.g. "Monthly Recurring Revenue (MRR)". Everything else is flagged.
    """
    acronyms = _find_acronyms(text)
    unexpanded: list[str] = []
    for acronym in sorted(acronyms):
        expanded = f"{acronym} (" in text or f"({acronym})" in text
        if not expanded:
            unexpanded.append(acronym)
    return unexpanded


def compute_slide_metrics(slide_number: int, text: str) -> SlideMetrics:
    """Compute deterministic signals for one slide from its extracted text."""
    return SlideMetrics(
        slide_number=slide_number,
        word_count=_word_count(text),
        flesch_reading_ease=_flesch(text),
        longest_sentence_words=_longest_sentence_words(text),
        buzzword_hits=_buzzword_hits(text),
        unexpanded_acronyms=_unexpanded_acronyms(text),
    )


def compute_deck_metrics(slides: Sequence[tuple[int, str]]) -> DeckMetrics:
    """Compute per-slide and deck-level deterministic signals.

    ``slides`` is a sequence of ``(slide_number, extracted_text)`` pairs. Acronym expansion
    is judged deck-wide, so an acronym expanded on one slide is not flagged on another.
    """
    per_slide = [compute_slide_metrics(number, text) for number, text in slides]
    combined_text = "\n".join(text for _, text in slides)

    aggregated_buzzwords: dict[str, int] = {}
    for metrics in per_slide:
        for label, count in metrics.buzzword_hits.items():
            aggregated_buzzwords[label] = aggregated_buzzwords.get(label, 0) + count

    return DeckMetrics(
        slide_count=len(per_slide),
        total_words=sum(m.word_count for m in per_slide),
        flesch_reading_ease=_flesch(combined_text),
        buzzword_hits=aggregated_buzzwords,
        unexpanded_acronyms=_unexpanded_acronyms(combined_text),
        slides=per_slide,
    )
