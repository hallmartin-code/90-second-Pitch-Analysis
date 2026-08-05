"""Tests for the evaluator: coercion, scoring, the FAKE_LLM run, and an optional live call."""

from __future__ import annotations

import os

import pytest

from app.config import Settings
from app.evaluator import (
    EvaluationResult,
    _coerce_slide_record,
    _unstringify,
    evaluate_deck,
    finalize_scores,
)
from app.ingest import ingest_pdf
from app.metrics import compute_deck_metrics
from app.rubric import RaskinElement, SlideType, TextDensity, aggregate_overall
from tests.factories import make_evaluation_payload


# --- _unstringify (tool-input normalization) ---------------------------------


def test_unstringify_passes_through_normal_structures():
    value = {"elements": [{"score": 7}], "n": 3, "flag": True}
    assert _unstringify(value) == value


def test_unstringify_parses_json_encoded_list():
    raw = {"elements": '[{"score": 7}, {"score": 8}]'}
    assert _unstringify(raw) == {"elements": [{"score": 7}, {"score": 8}]}


def test_unstringify_leaves_plain_strings_alone():
    assert _unstringify({"company_name": "Acme"}) == {"company_name": "Acme"}


# --- _coerce_slide_record (Stage A resilience) -------------------------------


def test_coerce_repairs_out_of_enum_slide_type():
    rec = _coerce_slide_record({"slide_number": 3, "slide_type": "roadmap"}, 3, 15)
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


def test_coerce_handles_missing_fields():
    rec = _coerce_slide_record({"slide_type": "ask"}, 15, 15)
    assert rec.slide_number == 15
    assert rec.key_points == []
    assert rec.has_chart is False
    assert rec.headline == "Slide 15"


def test_coerce_falls_back_on_invalid_slide_number():
    rec = _coerce_slide_record({"slide_number": 999, "slide_type": "cover"}, 1, 15)
    assert rec.slide_number == 1


# --- finalize_scores ---------------------------------------------------------


def test_finalize_recomputes_overall_as_mean():
    payload = make_evaluation_payload()
    scores = {
        RaskinElement.name_the_enemy: 7.5,
        RaskinElement.why_now: 6.5,
        RaskinElement.promised_land: 8.5,
        RaskinElement.obstacles_and_gifts: 8.0,
        RaskinElement.present_evidence: 8.5,
    }
    patched = [e.model_copy(update={"score": scores[RaskinElement(e.element)]}) for e in payload.elements]
    payload = payload.model_copy(update={"elements": patched, "overall_score": 1.0})

    finalized = finalize_scores(payload)
    assert finalized.overall_score == aggregate_overall(scores)  # 7.8


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
    assert result.logo_path is None
    assert len(result.payload.elements) == 5
    assert 0 <= result.payload.overall_score <= 10
    assert result.payload.company_name
    assert len(result.slide_records) == deck.page_count


def test_fake_run_on_scanned_deck(fixture_decks, tmp_path, fake_settings):
    deck = _ingest("scanned_deck.pdf", fixture_decks, tmp_path)
    assert deck.has_text_layer is False
    metrics = compute_deck_metrics([(p.number, p.text) for p in deck.pages])
    result = evaluate_deck(deck, metrics, fake_settings)
    assert len(result.payload.elements) == 5


def test_fake_run_needs_no_api_key(fixture_decks, tmp_path):
    settings = Settings(fake_llm=True, anthropic_api_key=None)
    deck = _ingest("weak_deck.pdf", fixture_decks, tmp_path)
    metrics = compute_deck_metrics([(p.number, p.text) for p in deck.pages])
    result = evaluate_deck(deck, metrics, settings)
    assert result.payload.overall_score >= 0


# --- Optional live smoke test (auto-skips without a key) ----------------------


@pytest.mark.skipif(
    not os.getenv("ANTHROPIC_API_KEY"),
    reason="no ANTHROPIC_API_KEY set; live evaluation test skipped",
)
def test_live_evaluation_smoke(fixture_decks, tmp_path):
    settings = Settings(fake_llm=False)
    deck = _ingest("weak_deck.pdf", fixture_decks, tmp_path)
    metrics = compute_deck_metrics([(p.number, p.text) for p in deck.pages])
    result = evaluate_deck(deck, metrics, settings)
    assert len(result.payload.elements) == 5
    assert 0 <= result.payload.overall_score <= 10
    assert result.payload.company_name
