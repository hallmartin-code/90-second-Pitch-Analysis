"""PDF ingestion: validate, rasterize every page, extract the text layer.

Pure functions with no web or LLM dependency. A deck is read with **vision** — every
page is rasterized to a PNG for the vision model — and the text layer is kept only as a
secondary signal for quote verification and scanned-deck detection.
"""

from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf

logger = logging.getLogger(__name__)

# --- Tunable ingestion parameters (defaults match SPEC §5) -------------------

RASTER_DPI = 150
MAX_LONG_EDGE_PX = 1568
THUMB_LONG_EDGE_PX = 400
SCANNED_TEXT_THRESHOLD = 50  # total chars below this => treat as image-only

_PDF_MAGIC = b"%PDF-"
_SANITIZE_STRIP = re.compile(r"[^A-Za-z0-9._ -]+")


class IngestError(Exception):
    """A user-facing ingestion failure. ``message`` is safe to show verbatim."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


@dataclass(slots=True)
class IngestedPage:
    """One rasterized page plus its extracted text."""

    number: int  # 1-based
    image_path: Path
    thumb_path: Path
    text: str
    width: int  # rasterized image pixels
    height: int


@dataclass(slots=True)
class IngestedDeck:
    """Result of ingesting a PDF: rasterized pages and document-level signals."""

    deck_id: str
    source_path: Path
    pages: list[IngestedPage] = field(default_factory=list)
    has_text_layer: bool = False
    total_chars: int = 0
    estimated_image_tokens: int = 0

    @property
    def page_count(self) -> int:
        return len(self.pages)


def sanitize_filename(name: str, *, default: str = "deck", max_length: int = 120) -> str:
    """Reduce a client-supplied filename to a safe display label.

    Never used as a path component — deck storage keys off a generated UUID — but the
    original name is shown in the report, so it must be stripped of anything unpleasant.
    """
    # Keep only the final component; drop any directory portion the client injected.
    base = Path(name.replace("\\", "/")).name
    stem, dot, ext = base.rpartition(".")
    label = stem if dot else base
    cleaned = _SANITIZE_STRIP.sub("", label).strip(" .-_")
    cleaned = re.sub(r"\s+", " ", cleaned)
    if not cleaned:
        cleaned = default
    if ext and ext.lower() == "pdf":
        cleaned = f"{cleaned}.pdf"
    return cleaned[:max_length]


def estimate_image_tokens(width: int, height: int) -> int:
    """Approximate Claude vision input tokens for an image of the given pixel size.

    Anthropic's guidance is roughly ``(width * height) / 750`` tokens. A page capped at
    1568 px long edge lands near the ~1,500–1,600 tokens/page figure in SPEC §5.
    """
    return math.ceil((width * height) / 750)


def validate_pdf_bytes(
    data: bytes,
    *,
    max_bytes: int,
    max_pages: int,
) -> pymupdf.Document:
    """Validate raw upload bytes and return an open document.

    Raises :class:`IngestError` with a specific, user-safe message on any rejection.
    The caller owns closing the returned document.
    """
    if len(data) == 0:
        raise IngestError("The uploaded file is empty.")
    if len(data) > max_bytes:
        got_mb = len(data) / (1024 * 1024)
        cap_mb = max_bytes / (1024 * 1024)
        raise IngestError(
            f"That file is {got_mb:.1f} MB. The limit is {cap_mb:.0f} MB — "
            "please compress the deck or export at a lower resolution."
        )
    # Check the magic bytes, not the extension, so a renamed file can't sneak through.
    if not data[:5] == _PDF_MAGIC:
        raise IngestError("That doesn't look like a PDF file. Please upload a PDF pitch deck.")

    try:
        doc = pymupdf.open(stream=data, filetype="pdf")
    except Exception as exc:  # pymupdf raises a variety of low-level errors
        logger.warning("PyMuPDF failed to open uploaded bytes: %s", exc)
        raise IngestError("This PDF appears to be corrupted and could not be opened.") from exc

    if doc.needs_pass or doc.is_encrypted:
        doc.close()
        raise IngestError(
            "This PDF is password-protected. Please remove the password and upload it again."
        )

    page_count = doc.page_count
    if page_count == 0:
        doc.close()
        raise IngestError("This PDF has no pages.")
    if page_count > max_pages:
        doc.close()
        raise IngestError(
            f"This deck has {page_count} pages. The limit is {max_pages}. "
            "Please trim it to the core slides and upload again."
        )
    return doc


def _render_page_png(page: pymupdf.Page, long_edge_px: int, out_path: Path) -> tuple[int, int]:
    """Rasterize a page so its long edge is ``long_edge_px`` at most ~150 DPI, save PNG.

    Returns the (width, height) of the written image in pixels.
    """
    rect = page.rect
    long_edge_pts = max(rect.width, rect.height) or 1.0
    # Start from the target DPI, then clamp so the long edge never exceeds the cap.
    zoom = RASTER_DPI / 72.0
    if long_edge_pts * zoom > long_edge_px:
        zoom = long_edge_px / long_edge_pts
    matrix = pymupdf.Matrix(zoom, zoom)
    pix = page.get_pixmap(matrix=matrix, alpha=False)
    pix.save(out_path)
    return pix.width, pix.height


def ingest_pdf(
    data: bytes,
    *,
    deck_id: str,
    storage_root: Path,
    max_bytes: int,
    max_pages: int,
) -> IngestedDeck:
    """Ingest raw PDF bytes into rasterized pages, thumbnails, and text.

    Layout produced under ``storage_root``::

        decks/{deck_id}/source.pdf
        decks/{deck_id}/pages/page-01.png ...
        decks/{deck_id}/thumbs/thumb-01.png ...

    Storage keys off ``deck_id`` (a caller-generated UUID), never the client filename.
    """
    doc = validate_pdf_bytes(data, max_bytes=max_bytes, max_pages=max_pages)
    try:
        deck_dir = storage_root / "decks" / deck_id
        pages_dir = deck_dir / "pages"
        thumbs_dir = deck_dir / "thumbs"
        pages_dir.mkdir(parents=True, exist_ok=True)
        thumbs_dir.mkdir(parents=True, exist_ok=True)

        source_path = deck_dir / "source.pdf"
        source_path.write_bytes(data)

        pages: list[IngestedPage] = []
        total_chars = 0
        estimated_tokens = 0

        for index, page in enumerate(doc):
            number = index + 1
            image_path = pages_dir / f"page-{number:02d}.png"
            thumb_path = thumbs_dir / f"thumb-{number:02d}.png"

            width, height = _render_page_png(page, MAX_LONG_EDGE_PX, image_path)
            _render_page_png(page, THUMB_LONG_EDGE_PX, thumb_path)

            text = page.get_text().strip()
            total_chars += len(text)
            estimated_tokens += estimate_image_tokens(width, height)

            pages.append(
                IngestedPage(
                    number=number,
                    image_path=image_path,
                    thumb_path=thumb_path,
                    text=text,
                    width=width,
                    height=height,
                )
            )

        has_text_layer = total_chars >= SCANNED_TEXT_THRESHOLD
        deck = IngestedDeck(
            deck_id=deck_id,
            source_path=source_path,
            pages=pages,
            has_text_layer=has_text_layer,
            total_chars=total_chars,
            estimated_image_tokens=estimated_tokens,
        )
    finally:
        doc.close()

    logger.info(
        "Ingested deck %s: %d pages, text_layer=%s, ~%d image tokens",
        deck_id,
        deck.page_count,
        deck.has_text_layer,
        deck.estimated_image_tokens,
    )
    if not has_text_layer:
        logger.info(
            "Deck %s has no usable text layer (%d chars); evidence quotes will be "
            "model-transcribed and must be labelled as such.",
            deck_id,
            total_chars,
        )
    return deck
