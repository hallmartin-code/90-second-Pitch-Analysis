"""Tests for the evaluator: verification logic, the FAKE_LLM run, and an optional live call."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from app.config import Settings
from app.ingest import IngestedDeck, IngestedPage, ingest_pdf
from app.metrics import compute_deck_metrics
from app.rubric import Dimension, aggregate_overall
from app.evaluator import (
    EvaluationResult,
    _unstringify,
    evaluate_deck,
    finalize_scores,
    verify_and_prune,
)
from app.schemas import Evidence
from tests.factories import make_evaluation_payload


def _deck(texts: list[str], *, has_text_layer: bool = True) -> IngestedDeck:
    """Build an IngestedDeck from page texts without touching disk (verify never reads images)."""
    pages = [
        IngestedPage(
            number=i + 1,
            image_path=Path(f"page-{i + 1}.png"),
            thumb_path=Path(f"thumb-{i + 1}.png"),
            text=text,
            width=1000,
            height=700,
        )
        for i, text in enumerate(texts)
    ]
    return IngestedDeck(
        deck_id="unit",
        source_path=Path("source.pdf"),
        pages=pages,
        has_text_layer=has_text_layer,
        total_chars=sum(len(t) for t in texts),
    )


QUOTE = "Real-time freight visibility for mid-market shippers"


def _payload_with_first_dimension_evidence(slide_number: int, quote: str):
    payload = make_evaluation_payload()
    first = payload.dimensions[0]
    patched = first.model_copy(
        update={"evidence": [Evidence(slide_number=slide_number, quote=quote, comment="x")]}
    )
    return payload.model_copy(update={"dimensions": [patched, *payload.dimensions[1:]]})


# --- verify_and_prune --------------------------------------------------------


def test_valid_evidence_survives():
    deck = _deck(["cover text", QUOTE, "more"])
    payload = _payload_with_first_dimension_evidence(2, QUOTE)
    pruned, lost, dropped = verify_and_prune(payload, deck)
    assert lost == []
    assert dropped == []
    assert len(pruned.dimensions[0].evidence) == 1


def test_nonexistent_slide_number_is_dropped():
    deck = _deck(["a", QUOTE, "c"])  # 3 pages
    payload = _payload_with_first_dimension_evidence(999, QUOTE)
    pruned, lost, dropped = verify_and_prune(payload, deck)
    assert Dimension.clarity in lost  # first dimension is clarity; it lost its only evidence
    assert any("does not exist" in d for d in dropped)


def test_quote_not_in_text_is_dropped_when_text_layer_present():
    deck = _deck(["a", "totally different slide content", "c"])
    payload = _payload_with_first_dimension_evidence(2, QUOTE)
    pruned, lost, dropped = verify_and_prune(payload, deck)
    assert Dimension.clarity in lost
    assert any("quote not found" in d for d in dropped)


def test_quote_not_checked_when_no_text_layer():
    deck = _deck(["", "", ""], has_text_layer=False)
    payload = _payload_with_first_dimension_evidence(2, "anything transcribed")
    pruned, lost, dropped = verify_and_prune(payload, deck)
    assert lost == []
    assert dropped == []


def test_quote_match_is_whitespace_and_case_insensitive():
    deck = _deck(["a", "  REAL-TIME   freight  visibility for   mid-market shippers ", "c"])
    payload = _payload_with_first_dimension_evidence(2, QUOTE)
    _, lost, dropped = verify_and_prune(payload, deck)
    assert lost == []
    assert dropped == []


# --- _unstringify (tool-input normalization) ---------------------------------


def test_unstringify_passes_through_normal_structures():
    value = {"slides": [{"slide_number": 1}], "n": 3, "flag": True}
    assert _unstringify(value) == value


def test_unstringify_parses_json_encoded_list():
    raw = {"slides": '[{"slide_number": 1}, {"slide_number": 2}]'}
    assert _unstringify(raw) == {"slides": [{"slide_number": 1}, {"slide_number": 2}]}


def test_unstringify_handles_double_wrapped_object():
    # The model quirk observed live: the whole object encoded as a string under "slides".
    raw = {"slides": '{"slides": [{"slide_number": 1}]}'}
    assert _unstringify(raw) == {"slides": {"slides": [{"slide_number": 1}]}}


def test_unstringify_leaves_plain_strings_alone():
    assert _unstringify({"headline": "We help X do Y"}) == {"headline": "We help X do Y"}


# --- finalize_scores ---------------------------------------------------------


def test_finalize_recomputes_overall_and_band():
    payload = make_evaluation_payload()
    scores = {
        Dimension.clarity: 4,
        Dimension.structure: 3,
        Dimension.messaging: 3,
        Dimension.differentiation: 2,
        Dimension.investor_engagement: 2,
    }
    patched = [d.model_copy(update={"score": scores[Dimension(d.dimension)]}) for d in payload.dimensions]
    payload = payload.model_copy(update={"dimensions": patched, "overall_score": 1, "band": "wrong"})

    finalized = finalize_scores(payload)
    assert finalized.overall_score == aggregate_overall(scores)  # 58
    assert finalized.band == "Major revision"


# --- FAKE_LLM full run -------------------------------------------------------


@pytest.fixture
def fake_settings() -> Settings:
    return Settings(fake_llm=True, anthropic_api_key=None)


def _ingest(name, fixture_decks, tmp_path):
    data = fixture_decks[name].read_bytes()
    return ingest_pdf(
        data, deck_id="ev", storage_root=tmp_path, max_bytes=30 * 1024 * 1024, max_pages=40
    )


def test_fake_run_produces_valid_result(fixture_decks, tmp_path, fake_settings):
    deck = _ingest("good_deck.pdf", fixture_decks, tmp_path)
    metrics = compute_deck_metrics([(p.number, p.text) for p in deck.pages])
    result = evaluate_deck(deck, metrics, fake_settings)

    assert isinstance(result, EvaluationResult)
    assert result.model == "fake-llm"
    assert len(result.payload.dimensions) == 5
    assert len(result.payload.rewrites) == 3
    assert len(result.slide_records) == deck.page_count
    # Overall is the deterministic aggregate of the canned scores.
    assert 0 <= result.payload.overall_score <= 100


def test_fake_evidence_matches_text_layer(fixture_decks, tmp_path, fake_settings):
    """The canned payload's quotes must survive verification on a real deck (no drops)."""
    deck = _ingest("good_deck.pdf", fixture_decks, tmp_path)
    metrics = compute_deck_metrics([(p.number, p.text) for p in deck.pages])
    result = evaluate_deck(deck, metrics, fake_settings)
    _, lost, dropped = verify_and_prune(result.payload, deck)
    assert lost == []
    assert dropped == []


def test_fake_run_on_scanned_deck(fixture_decks, tmp_path, fake_settings):
    deck = _ingest("scanned_deck.pdf", fixture_decks, tmp_path)
    assert deck.has_text_layer is False
    metrics = compute_deck_metrics([(p.number, p.text) for p in deck.pages])
    result = evaluate_deck(deck, metrics, fake_settings)
    assert len(result.payload.dimensions) == 5


def test_fake_run_needs_no_api_key(fixture_decks, tmp_path):
    # No key set, FAKE on: must not raise.
    settings = Settings(fake_llm=True, anthropic_api_key=None)
    deck = _ingest("weak_deck.pdf", fixture_decks, tmp_path)
    metrics = compute_deck_metrics([(p.number, p.text) for p in deck.pages])
    result = evaluate_deck(deck, metrics, settings)
    assert result.payload.band


# --- Optional live smoke test (auto-skips without a key) ----------------------


@pytest.mark.skipif(
    not os.getenv("ANTHROPIC_API_KEY"),
    reason="no ANTHROPIC_API_KEY set; live evaluation test skipped",
)
def test_live_evaluation_smoke(fixture_decks, tmp_path):
    settings = Settings(fake_llm=False)  # reads ANTHROPIC_API_KEY from the environment
    deck = _ingest("weak_deck.pdf", fixture_decks, tmp_path)
    metrics = compute_deck_metrics([(p.number, p.text) for p in deck.pages])
    result = evaluate_deck(deck, metrics, settings)
    assert len(result.payload.dimensions) == 5
    assert len(result.payload.rewrites) == 3
    assert 0 <= result.payload.overall_score <= 100
    assert len(result.slide_records) == deck.page_count
