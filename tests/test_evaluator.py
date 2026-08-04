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
    _coerce_slide_record,
    _unstringify,
    evaluate_deck,
    finalize_scores,
    verify_and_prune,
)
from app.rubric import SlideType, TextDensity
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


def _evidence_slides(payload):
    return [e.slide_number for e in payload.dimensions[0].evidence]


def test_valid_evidence_survives():
    deck = _deck(["cover text", QUOTE, "more"])
    payload = _payload_with_first_dimension_evidence(2, QUOTE)
    pruned, notes = verify_and_prune(payload, deck)
    assert notes == []
    assert len(pruned.dimensions[0].evidence) == 1


def test_verification_never_empties_a_dimension():
    # Every dimension must keep at least one evidence item (payload stays valid, no re-run).
    deck = _deck(["a", "totally different content", "c"])
    payload = _payload_with_first_dimension_evidence(2, QUOTE)  # quote won't match slide 2
    pruned, notes = verify_and_prune(payload, deck)
    assert len(pruned.dimensions[0].evidence) >= 1  # kept as-is, not dropped
    assert any("kept as-is" in n for n in notes)


def test_bad_slide_kept_when_it_is_the_only_evidence():
    deck = _deck(["a", QUOTE, "c"])  # 3 pages; evidence cites slide 999
    payload = _payload_with_first_dimension_evidence(999, QUOTE)
    pruned, notes = verify_and_prune(payload, deck)
    assert len(pruned.dimensions[0].evidence) == 1  # never emptied
    assert any("missing slides" in n for n in notes)


def test_bad_slide_dropped_when_a_good_one_remains():
    from app.schemas import Evidence

    deck = _deck(["a", QUOTE, "c"])
    payload = make_evaluation_payload()
    two = [Evidence(slide_number=2, quote=QUOTE, comment="ok"), Evidence(slide_number=999, quote="x", comment="bad")]
    dim = payload.dimensions[0].model_copy(update={"evidence": two})
    payload = payload.model_copy(update={"dimensions": [dim, *payload.dimensions[1:]]})
    pruned, notes = verify_and_prune(payload, deck)
    assert _evidence_slides(pruned) == [2]  # the phantom slide dropped, real one kept


def test_quote_not_checked_when_no_text_layer():
    deck = _deck(["", "", ""], has_text_layer=False)
    payload = _payload_with_first_dimension_evidence(2, "anything transcribed")
    pruned, notes = verify_and_prune(payload, deck)
    assert notes == []
    assert len(pruned.dimensions[0].evidence) == 1


def test_quote_match_is_whitespace_and_case_insensitive():
    deck = _deck(["a", "  REAL-TIME   freight  visibility for   mid-market shippers ", "c"])
    payload = _payload_with_first_dimension_evidence(2, QUOTE)
    _, notes = verify_and_prune(payload, deck)
    assert notes == []


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


# --- _coerce_slide_record (Stage A resilience) -------------------------------


def test_coerce_repairs_out_of_enum_slide_type():
    rec = _coerce_slide_record(
        {"slide_number": 3, "slide_type": "roadmap", "text_density": "balanced"}, 3, 15
    )
    assert rec.slide_type is SlideType.unclear
    assert rec.slide_number == 3


def test_coerce_repairs_bad_text_density():
    rec = _coerce_slide_record({"slide_type": "team", "text_density": "medium"}, 5, 15)
    assert rec.text_density is TextDensity.balanced


def test_coerce_truncates_too_many_key_points():
    rec = _coerce_slide_record(
        {"slide_type": "market", "key_points": [f"p{i}" for i in range(9)]}, 6, 15
    )
    assert len(rec.key_points) == 5


def test_coerce_handles_string_key_points_and_missing_fields():
    rec = _coerce_slide_record({"slide_type": "ask"}, 15, 15)  # minimal input
    assert rec.slide_number == 15
    assert rec.key_points == []
    assert rec.has_chart is False
    assert rec.headline == "Slide 15"


def test_coerce_falls_back_on_invalid_slide_number():
    rec = _coerce_slide_record({"slide_number": 999, "slide_type": "cover"}, 1, 15)
    assert rec.slide_number == 1  # 999 is out of range -> use expected


def test_coerce_produces_valid_slide_record():
    # A fully valid, if messy, dict round-trips without raising.
    rec = _coerce_slide_record(
        {
            "slide_number": 2,
            "slide_type": "traction",
            "headline": "Revenue Growth",
            "key_points": ["$800K to $39M", None, "40%+ margin"],
            "has_chart": 1,
            "has_screenshot": 0,
            "text_density": "dense",
            "readability_notes": "small font",
        },
        2,
        15,
    )
    assert rec.slide_type is SlideType.traction
    assert rec.has_chart is True and rec.has_screenshot is False
    assert rec.key_points == ["$800K to $39M", "40%+ margin"]  # None dropped
    assert rec.readability_notes == ["small font"]


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
    _, notes = verify_and_prune(result.payload, deck)
    assert notes == []


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
