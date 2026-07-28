"""Tests for the deterministic metrics layer."""

from __future__ import annotations

from pathlib import Path

import pymupdf

from app.metrics import (
    compute_deck_metrics,
    compute_slide_metrics,
)


def _slides_from_pdf(path: Path) -> list[tuple[int, str]]:
    doc = pymupdf.open(path)
    try:
        return [(i + 1, page.get_text().strip()) for i, page in enumerate(doc)]
    finally:
        doc.close()


# --- Deck-level metrics on fixtures ------------------------------------------


def test_good_deck_metrics(fixture_decks):
    metrics = compute_deck_metrics(_slides_from_pdf(fixture_decks["good_deck.pdf"]))
    assert metrics.slide_count == 11
    assert metrics.total_words > 0
    assert metrics.flesch_reading_ease is not None
    # Concrete acronyms in the deck are never expanded, so they must be flagged.
    assert "MRR" in metrics.unexpanded_acronyms
    assert "ELD" in metrics.unexpanded_acronyms
    assert "GPS" in metrics.unexpanded_acronyms
    # The good deck is written plainly; it should carry few buzzwords.
    assert sum(metrics.buzzword_hits.values()) <= 1


def test_weak_deck_is_buzzword_heavy(fixture_decks):
    metrics = compute_deck_metrics(_slides_from_pdf(fixture_decks["weak_deck.pdf"]))
    hits = metrics.buzzword_hits
    for expected in ("synergy", "disruptive", "seamless", "revolutionary", "paradigm", "leverage"):
        assert expected in hits, f"expected buzzword '{expected}' not detected"
    assert "next-generation" in hits
    assert "best-in-class" in hits


def test_scanned_deck_yields_sparse_metrics(fixture_decks):
    metrics = compute_deck_metrics(_slides_from_pdf(fixture_decks["scanned_deck.pdf"]))
    # No text layer -> essentially no words, but the slide count is still correct.
    assert metrics.slide_count == 11
    assert metrics.total_words == 0
    assert metrics.flesch_reading_ease is None


# --- Unit behavior -----------------------------------------------------------


def test_buzzword_counting_is_case_insensitive():
    m = compute_slide_metrics(1, "Our SEAMLESS, Seamless, seamless platform is best-in-class.")
    assert m.buzzword_hits["seamless"] == 3
    assert m.buzzword_hits["best-in-class"] == 1


def test_expanded_acronym_is_not_flagged():
    text = "Monthly Recurring Revenue (MRR) hit $40k. ARR is a different metric."
    m = compute_slide_metrics(1, text)
    assert "MRR" not in m.unexpanded_acronyms  # expanded via parenthetical
    assert "ARR" in m.unexpanded_acronyms  # never expanded


def test_acronym_expansion_is_judged_deck_wide():
    # MRR is expanded on slide 1, so it must not be flagged on slide 2.
    slides = [
        (1, "Monthly Recurring Revenue (MRR) is our north star."),
        (2, "MRR grew 18% this month."),
    ]
    metrics = compute_deck_metrics(slides)
    assert "MRR" not in metrics.unexpanded_acronyms


def test_shouty_heading_does_not_produce_acronyms():
    m = compute_slide_metrics(1, "WHY WE WIN\nWe beat the status quo on price.")
    # 'WHY' and 'WIN' come from an all-caps heading line and must be ignored.
    assert m.unexpanded_acronyms == []


def test_longest_sentence_and_word_count():
    m = compute_slide_metrics(1, "Short one. This sentence has exactly seven words total here.")
    assert m.word_count == 10
    assert m.longest_sentence_words == 8


def test_flesch_none_for_trivial_text():
    assert compute_slide_metrics(1, "Hi").flesch_reading_ease is None
