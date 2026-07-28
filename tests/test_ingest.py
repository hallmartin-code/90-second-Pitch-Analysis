"""Tests for PDF ingestion: validation, rasterization, and text extraction."""

from __future__ import annotations

import struct
import zlib
from pathlib import Path

import pymupdf
import pytest

from app.ingest import (
    MAX_LONG_EDGE_PX,
    THUMB_LONG_EDGE_PX,
    IngestError,
    estimate_image_tokens,
    ingest_pdf,
    sanitize_filename,
)

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def _png_dimensions(path: Path) -> tuple[int, int]:
    """Read width/height from a PNG's IHDR chunk without a Pillow dependency."""
    data = path.read_bytes()
    assert data[:8] == PNG_MAGIC, f"{path} is not a PNG"
    width, height = struct.unpack(">II", data[16:24])
    return width, height


def _ingest(name: str, fixture_decks: dict[str, Path], tmp_path: Path):
    data = fixture_decks[name].read_bytes()
    return ingest_pdf(
        data,
        deck_id="test-deck",
        storage_root=tmp_path,
        max_bytes=30 * 1024 * 1024,
        max_pages=40,
    )


# --- Rasterization -----------------------------------------------------------


def test_good_deck_rasterizes_every_page(fixture_decks, tmp_path):
    deck = _ingest("good_deck.pdf", fixture_decks, tmp_path)

    expected_pages = pymupdf.open(fixture_decks["good_deck.pdf"]).page_count
    assert deck.page_count == expected_pages

    for page in deck.pages:
        assert page.image_path.exists()
        assert page.thumb_path.exists()
        w, h = _png_dimensions(page.image_path)
        assert (w, h) == (page.width, page.height)
        assert max(w, h) <= MAX_LONG_EDGE_PX
        assert max(w, h) > THUMB_LONG_EDGE_PX  # a full page, not a thumbnail

        tw, th = _png_dimensions(page.thumb_path)
        assert max(tw, th) <= THUMB_LONG_EDGE_PX


def test_source_pdf_is_persisted(fixture_decks, tmp_path):
    deck = _ingest("good_deck.pdf", fixture_decks, tmp_path)
    assert deck.source_path.exists()
    assert deck.source_path == tmp_path / "decks" / "test-deck" / "source.pdf"
    assert deck.source_path.read_bytes()[:5] == b"%PDF-"


# --- Text layer & scanned detection ------------------------------------------


def test_text_deck_has_text_layer(fixture_decks, tmp_path):
    deck = _ingest("good_deck.pdf", fixture_decks, tmp_path)
    assert deck.has_text_layer is True
    assert deck.total_chars > 50
    # Concrete claims from the fixture should survive extraction.
    all_text = " ".join(p.text for p in deck.pages)
    assert "Northwind" in all_text
    assert "MRR" in all_text


def test_scanned_deck_has_no_text_layer(fixture_decks, tmp_path):
    deck = _ingest("scanned_deck.pdf", fixture_decks, tmp_path)
    # Image-only deck: still fully rasterized, but flagged as having no text layer.
    assert deck.page_count > 0
    assert deck.has_text_layer is False
    assert deck.total_chars < 50
    for page in deck.pages:
        assert page.image_path.exists()


def test_text_only_document_extracts_prose(fixture_decks, tmp_path):
    deck = _ingest("text_only.pdf", fixture_decks, tmp_path)
    assert deck.has_text_layer is True
    assert deck.total_chars > 500  # heavy prose


# --- Token estimation --------------------------------------------------------


def test_estimated_tokens_are_positive_and_summed(fixture_decks, tmp_path):
    deck = _ingest("good_deck.pdf", fixture_decks, tmp_path)
    assert deck.estimated_image_tokens > 0
    per_page = sum(estimate_image_tokens(p.width, p.height) for p in deck.pages)
    assert deck.estimated_image_tokens == per_page


def test_estimate_image_tokens_formula():
    assert estimate_image_tokens(1568, 882) == pytest.approx(1568 * 882 / 750, abs=1)


# --- Validation --------------------------------------------------------------


def test_rejects_non_pdf_bytes(tmp_path):
    with pytest.raises(IngestError, match="look like a PDF"):
        ingest_pdf(
            b"this is not a pdf at all",
            deck_id="x",
            storage_root=tmp_path,
            max_bytes=30 * 1024 * 1024,
            max_pages=40,
        )


def test_rejects_empty_upload(tmp_path):
    with pytest.raises(IngestError, match="empty"):
        ingest_pdf(b"", deck_id="x", storage_root=tmp_path, max_bytes=1000, max_pages=40)


def test_rejects_oversized_upload(fixture_decks, tmp_path):
    data = fixture_decks["good_deck.pdf"].read_bytes()
    with pytest.raises(IngestError, match="limit is"):
        ingest_pdf(data, deck_id="x", storage_root=tmp_path, max_bytes=1024, max_pages=40)


def test_rejects_too_many_pages(fixture_decks, tmp_path):
    data = fixture_decks["good_deck.pdf"].read_bytes()
    with pytest.raises(IngestError, match="limit is 3"):
        ingest_pdf(data, deck_id="x", storage_root=tmp_path, max_bytes=30 * 1024 * 1024, max_pages=3)


def test_rejects_encrypted_pdf(tmp_path):
    # Build a password-protected PDF on the fly.
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 72), "secret deck", fontsize=20)
    enc_path = tmp_path / "enc.pdf"
    doc.save(
        enc_path,
        encryption=pymupdf.PDF_ENCRYPT_AES_256,
        owner_pw="ownerpw",
        user_pw="userpw",
    )
    doc.close()

    with pytest.raises(IngestError, match="password-protected"):
        ingest_pdf(
            enc_path.read_bytes(),
            deck_id="x",
            storage_root=tmp_path,
            max_bytes=30 * 1024 * 1024,
            max_pages=40,
        )


# --- Filename sanitization ---------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("My Pitch Deck.pdf", "My Pitch Deck.pdf"),
        ("../../etc/passwd", "passwd"),
        (r"C:\Users\evil\deck.pdf", "deck.pdf"),
        ("deck  with   spaces.PDF", "deck with spaces.pdf"),
        ("!!!.pdf", "deck.pdf"),  # nothing safe left in the stem -> default
        ("weird$$$name.exe", "weirdname"),  # non-pdf extension is dropped
    ],
)
def test_sanitize_filename(raw, expected):
    assert sanitize_filename(raw) == expected


def test_sanitize_filename_never_yields_path_separators():
    for raw in ["a/b/c.pdf", "..\\..\\x.pdf", "/etc/shadow"]:
        out = sanitize_filename(raw)
        assert "/" not in out and "\\" not in out
