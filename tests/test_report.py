"""Tests for the ReportLab PDF renderer, driven by a fixture payload."""

from __future__ import annotations

from datetime import datetime, timezone

import pymupdf
import pytest

from app.report import render_report
from tests.factories import make_evaluation_payload


@pytest.fixture
def rendered(tmp_path):
    payload = make_evaluation_payload()
    out = tmp_path / "report.pdf"
    render_report(payload, out, generated_at=datetime(2026, 6, 4, tzinfo=timezone.utc))
    return out, payload


def test_produces_valid_pdf(rendered):
    out, _payload = rendered
    assert out.exists()
    data = out.read_bytes()
    assert data[:5] == b"%PDF-"
    assert b"%%EOF" in data[-2048:]
    assert out.stat().st_size > 3000


def test_report_text_contains_key_content(rendered):
    out, payload = rendered
    doc = pymupdf.open(out)
    try:
        text = "\n".join(page.get_text() for page in doc)
    finally:
        doc.close()

    assert payload.company_name in text
    assert "90 second pitch Analysis" in text
    assert "Overall Assessment" in text
    assert "Summary Scorecard" in text
    assert "Overall Raskin Alignment: 7.8/10" in text
    assert "If I Were Rebuilding This Deck" in text
    from app.rubric import RASKIN_ELEMENTS

    for spec in RASKIN_ELEMENTS:
        assert spec.title in text
    # Footer provenance.
    assert "TEN Capital Network" in text
    assert "Compiled on 6/4/2026" in text


def test_footer_uses_company_name(rendered):
    out, payload = rendered
    doc = pymupdf.open(out)
    try:
        text = "\n".join(page.get_text() for page in doc)
    finally:
        doc.close()
    assert f"{payload.company_name} - 90 second pitch Analysis" in text


def test_renders_without_logos(tmp_path):
    # No company logo, no TEN logo → still a valid report (text-only footer).
    out = tmp_path / "r.pdf"
    render_report(make_evaluation_payload(), out)
    assert out.exists() and out.read_bytes()[:5] == b"%PDF-"


def test_no_black_box_glyphs(rendered):
    out, _payload = rendered
    doc = pymupdf.open(out)
    try:
        text = "\n".join(page.get_text() for page in doc)
    finally:
        doc.close()
    assert not any(ch in text for ch in "¹²³⁰ⁱ⁲₀₁₂₃")
