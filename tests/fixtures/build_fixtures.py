"""Generate the synthetic PDF fixtures used by the test suite.

Run directly (``python tests/fixtures/build_fixtures.py``) or via the pytest
``fixtures`` conftest, which builds any that are missing. Fixtures are generated rather
than committed so the suite is reproducible from a clean clone and so the scanned deck is
guaranteed to be genuinely image-only.

Four decks:
  good_deck.pdf     well-ordered ~11-slide deck, real text layer
  weak_deck.pdf     short, buzzword-laden deck, real text layer
  text_only.pdf     a prose document (not a deck), heavy text layer
  scanned_deck.pdf  good_deck rendered to images only — no selectable text
"""

from __future__ import annotations

from pathlib import Path

import pymupdf

# 16:9 slide canvas in points.
SLIDE_W = 960.0
SLIDE_H = 540.0

FIXTURE_NAMES = ("good_deck.pdf", "weak_deck.pdf", "text_only.pdf", "scanned_deck.pdf")


def _slide(doc: pymupdf.Document, title: str, bullets: list[str]) -> None:
    """Append one 16:9 slide with a title and bullet lines (creates a text layer)."""
    page = doc.new_page(width=SLIDE_W, height=SLIDE_H)
    page.insert_text((60, 90), title, fontsize=34, fontname="helv")
    y = 170.0
    for bullet in bullets:
        page.insert_text((80, y), f"• {bullet}", fontsize=20, fontname="helv")
        y += 40


def build_good_deck(path: Path) -> None:
    """A deck that follows the load-bearing arc, with concrete claims."""
    doc = pymupdf.open()
    _slide(doc, "Northwind Logistics", ["Real-time freight visibility for mid-market shippers"])
    _slide(doc, "The Problem", [
        "Mid-market shippers track freight by phone and spreadsheet",
        "40% of shipments have no live status between pickup and delivery",
    ])
    _slide(doc, "Why Now", [
        "ELD mandate put GPS in 3.5M trucks since 2019",
        "APIs from carriers finally standardized in 2024",
    ])
    _slide(doc, "Our Solution", [
        "One dashboard aggregating live status across 200 carriers",
        "Alerts fire before a delay becomes a missed delivery",
    ])
    _slide(doc, "Product", ["Live map, exception alerts, and a shareable customer link"])
    _slide(doc, "Market", [
        "180,000 mid-market shippers in North America",
        "$4.2B annual spend on visibility tooling",
    ])
    _slide(doc, "Traction", [
        "$40k MRR, growing 18% month over month",
        "22 paying customers, net revenue retention 128%",
    ])
    _slide(doc, "Business Model", ["SaaS: $500-$2,000 per month by shipment volume"])
    _slide(doc, "Competition", [
        "Incumbents serve enterprise only and cost $50k+ to deploy",
        "Our wedge: self-serve onboarding in under an hour",
    ])
    _slide(doc, "Team", [
        "Founders ex-Convoy and ex-Flexport, 12 years in freight",
    ])
    _slide(doc, "The Ask", [
        "Raising $2.5M to reach $150k MRR and 100 customers in 18 months",
    ])
    doc.save(path)
    doc.close()


def build_weak_deck(path: Path) -> None:
    """A short, hype-heavy deck missing several load-bearing beats."""
    doc = pymupdf.open()
    _slide(doc, "SynergyAI", ["The next-generation, best-in-class platform"])
    _slide(doc, "Our Technology", [
        "A revolutionary, disruptive AI paradigm",
        "Seamless synergy across the enterprise",
    ])
    _slide(doc, "Vision", ["We leverage AI to disrupt everything"])
    _slide(doc, "Why We Win", ["We are simply better than everyone else"])
    _slide(doc, "Team", ["A world-class team of passionate visionaries"])
    _slide(doc, "Contact", ["hello@synergyai.example"])
    doc.save(path)
    doc.close()


def build_text_only(path: Path) -> None:
    """A prose document (portrait A4-ish), not a slide deck, with a heavy text layer."""
    doc = pymupdf.open()
    paragraph = (
        "This document is a written narrative rather than a slide deck. It contains long "
        "flowing paragraphs of prose that exercise the text extraction path and the "
        "readability metrics computed later in the pipeline. Unlike a deck, there is no "
        "dominant headline per page and no visual hierarchy to speak of. "
    )
    for _ in range(3):
        page = doc.new_page(width=595.0, height=842.0)  # A4 portrait
        rect = pymupdf.Rect(60, 60, 535, 782)
        page.insert_textbox(rect, paragraph * 8, fontsize=12, fontname="helv")
    doc.save(path)
    doc.close()


def build_scanned_deck(path: Path, *, source: Path) -> None:
    """Render an existing text deck to page images only — no selectable text layer."""
    src = pymupdf.open(source)
    scanned = pymupdf.open()
    try:
        for page in src:
            pix = page.get_pixmap(dpi=150)
            # Insert an already-compressed PNG stream; a raw pixmap would bloat the file
            # to tens of MB and trip the upload-size guard.
            new_page = scanned.new_page(width=page.rect.width, height=page.rect.height)
            new_page.insert_image(new_page.rect, stream=pix.tobytes("png"))
        scanned.save(path, deflate=True, garbage=4)
    finally:
        src.close()
        scanned.close()


def build_all(out_dir: Path) -> dict[str, Path]:
    """Build every fixture into ``out_dir`` and return a name -> path map."""
    out_dir.mkdir(parents=True, exist_ok=True)
    good = out_dir / "good_deck.pdf"
    weak = out_dir / "weak_deck.pdf"
    text_only = out_dir / "text_only.pdf"
    scanned = out_dir / "scanned_deck.pdf"

    build_good_deck(good)
    build_weak_deck(weak)
    build_text_only(text_only)
    build_scanned_deck(scanned, source=good)

    return {
        "good_deck.pdf": good,
        "weak_deck.pdf": weak,
        "text_only.pdf": text_only,
        "scanned_deck.pdf": scanned,
    }


if __name__ == "__main__":
    here = Path(__file__).resolve().parent
    built = build_all(here)
    for name, path in built.items():
        size_kb = path.stat().st_size / 1024
        print(f"  {name:18s} {size_kb:7.1f} KB  {path}")
