"""Tests for the ReportLab PDF renderer, driven by a fixture payload."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pymupdf
import pytest

from app.ingest import ingest_pdf
from app.metrics import compute_deck_metrics
from app.report import render_report
from app.rubric import Dimension
from tests.factories import make_evaluation_payload, make_slide_records


@pytest.fixture
def rendered(fixture_decks, tmp_path):
    """Render a report from the good-deck fixture and return (path, deck, payload)."""
    data = fixture_decks["good_deck.pdf"].read_bytes()
    deck = ingest_pdf(
        data, deck_id="rpt", storage_root=tmp_path, max_bytes=30 * 1024 * 1024, max_pages=40
    )
    metrics = compute_deck_metrics([(p.number, p.text) for p in deck.pages])
    payload = make_evaluation_payload()
    out = tmp_path / "report.pdf"
    result = render_report(
        payload,
        deck,
        metrics,
        out,
        deck_name="Northwind Logistics.pdf",
        slide_records=make_slide_records(),
        model="claude-sonnet-5",
        generated_at=datetime(2026, 7, 28, tzinfo=timezone.utc),
    )
    return result, deck, payload


def test_produces_valid_pdf(rendered):
    out, _deck, _payload = rendered
    assert out.exists()
    data = out.read_bytes()
    assert data[:5] == b"%PDF-"
    assert b"%%EOF" in data[-2048:]
    assert out.stat().st_size > 5000


def test_has_expected_page_count(rendered):
    out, _deck, _payload = rendered
    doc = pymupdf.open(out)
    try:
        # Cover + exec summary + 5 dimension pages + slide review + rewrites + appendix.
        assert doc.page_count >= 9
    finally:
        doc.close()


def test_report_text_contains_key_content(rendered):
    out, _deck, payload = rendered
    doc = pymupdf.open(out)
    try:
        text = "\n".join(page.get_text() for page in doc)
    finally:
        doc.close()

    assert "Northwind Logistics" in text
    assert str(payload.overall_score) in text
    assert payload.band in text
    assert "Executive summary" in text
    assert "Slide-by-slide review" in text
    assert "Rewrites" in text
    assert "Appendix" in text
    # Every dimension title appears.
    from app.rubric import DIMENSIONS

    for spec in DIMENSIONS:
        assert spec.title in text
    # Provenance in the appendix.
    assert "Rubric version" in text
    assert "1.0" in text


def test_scanned_deck_labels_transcribed_quotes(fixture_decks, tmp_path):
    data = fixture_decks["scanned_deck.pdf"].read_bytes()
    deck = ingest_pdf(
        data, deck_id="scan", storage_root=tmp_path, max_bytes=30 * 1024 * 1024, max_pages=40
    )
    assert deck.has_text_layer is False
    metrics = compute_deck_metrics([(p.number, p.text) for p in deck.pages])
    out = tmp_path / "scan_report.pdf"
    render_report(make_evaluation_payload(), deck, metrics, out, deck_name="Scanned deck")

    doc = pymupdf.open(out)
    try:
        text = "\n".join(page.get_text() for page in doc)
    finally:
        doc.close()
    assert "transcribed from the slide images" in text
    assert "image-only deck" in text


def test_no_black_box_glyphs_from_unicode(rendered):
    """Guard against the ReportLab sub/superscript black-box gotcha.

    The renderer must avoid raw Unicode sub/superscript characters; assert none leaked in.
    """
    out, _deck, _payload = rendered
    doc = pymupdf.open(out)
    try:
        text = "\n".join(page.get_text() for page in doc)
    finally:
        doc.close()
    forbidden = "¹²³⁰ⁱ⁲₀₁₂₃"
    assert not any(ch in text for ch in forbidden)


def test_missing_dimension_fix_still_renders(fixture_decks, tmp_path):
    """A dimension whose fixes list is minimal must not break leverage selection."""
    data = fixture_decks["good_deck.pdf"].read_bytes()
    deck = ingest_pdf(
        data, deck_id="rpt2", storage_root=tmp_path, max_bytes=30 * 1024 * 1024, max_pages=40
    )
    metrics = compute_deck_metrics([(p.number, p.text) for p in deck.pages])
    payload = make_evaluation_payload()
    out = tmp_path / "r.pdf"
    render_report(payload, deck, metrics, out, deck_name="X")
    assert out.exists()
