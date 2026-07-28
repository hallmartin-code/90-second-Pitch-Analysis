"""Render an :class:`EvaluationPayload` into a ReportLab PDF report (SPEC §8).

``render_report`` is a pure function: it takes the evaluation, the ingested deck (for
thumbnails and the scanned-deck flag), the deterministic metrics, and an output path, and
writes a PDF. It has no dependency on the web layer or an API key, so layout can be
iterated on with a hand-written fixture payload.

Typography: one serif for body (Times-Roman), one sans for headings and numbers
(Helvetica), a single accent color used only for scores and rules. Deck name and page
number repeat in every footer. No emoji, no traffic-light color coding, and — per the
ReportLab gotcha — no Unicode subscript/superscript characters (``<super>``/``<sub>``
markup only).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timezone
from functools import partial
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.lib.utils import ImageReader
from reportlab.platypus import (
    Flowable,
    Image,
    ListFlowable,
    ListItem,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from app.ingest import IngestedDeck
from app.metrics import DeckMetrics
from app.rubric import DIMENSION_BY_KEY, DIMENSIONS, RUBRIC_VERSION, Dimension, dimension_weights
from app.schemas import DimensionResult, EvaluationPayload, SlideRecord

# --- Palette & geometry ------------------------------------------------------

ACCENT = colors.HexColor("#1f5f5b")
INK = colors.HexColor("#222222")
MUTED = colors.HexColor("#6b6b6b")
FAINT = colors.HexColor("#9a9a9a")
TRACK = colors.HexColor("#e7e5e0")
RULE = colors.HexColor("#d8d6d0")

PAGE_SIZE = LETTER
MARGIN = 0.9 * inch

SERIF = "Times-Roman"
SERIF_ITALIC = "Times-Italic"
SANS = "Helvetica"
SANS_BOLD = "Helvetica-Bold"

REWRITE_TITLES = {
    "one_liner": "One-liner",
    "cover_slide_copy": "Cover slide copy",
    "thirty_second_verbal": "30-second verbal pitch",
}


def _esc(text: str) -> str:
    """Escape text destined for a ReportLab ``Paragraph`` (which parses mini-markup)."""
    return escape(str(text))


# --- Styles ------------------------------------------------------------------


def _styles() -> dict[str, ParagraphStyle]:
    body = ParagraphStyle("body", fontName=SERIF, fontSize=10.5, leading=15, textColor=INK)
    return {
        "body": body,
        "cover_name": ParagraphStyle(
            "cover_name", fontName=SANS_BOLD, fontSize=26, leading=30, textColor=INK,
            alignment=TA_CENTER,
        ),
        "cover_date": ParagraphStyle(
            "cover_date", fontName=SANS, fontSize=11, leading=14, textColor=MUTED,
            alignment=TA_CENTER,
        ),
        "score_big": ParagraphStyle(
            "score_big", fontName=SANS_BOLD, fontSize=96, leading=100, textColor=ACCENT,
            alignment=TA_CENTER,
        ),
        "score_denom": ParagraphStyle(
            "score_denom", fontName=SANS, fontSize=14, leading=16, textColor=MUTED,
            alignment=TA_CENTER,
        ),
        "band": ParagraphStyle(
            "band", fontName=SANS_BOLD, fontSize=16, leading=20, textColor=INK,
            alignment=TA_CENTER,
        ),
        "headline_fix": ParagraphStyle(
            "headline_fix", fontName=SERIF_ITALIC, fontSize=14, leading=20, textColor=INK,
            alignment=TA_CENTER,
        ),
        "h1": ParagraphStyle(
            "h1", fontName=SANS_BOLD, fontSize=18, leading=22, textColor=ACCENT,
            spaceAfter=10,
        ),
        "h2": ParagraphStyle(
            "h2", fontName=SANS_BOLD, fontSize=12, leading=16, textColor=INK,
            spaceBefore=10, spaceAfter=4,
        ),
        "quote": ParagraphStyle(
            "quote", fontName=SERIF_ITALIC, fontSize=10.5, leading=15, textColor=INK,
            leftIndent=16, spaceBefore=4,
        ),
        "quote_comment": ParagraphStyle(
            "quote_comment", fontName=SERIF, fontSize=9.5, leading=13, textColor=MUTED,
            leftIndent=16, spaceAfter=4,
        ),
        "small": ParagraphStyle("small", fontName=SANS, fontSize=8.5, leading=12, textColor=MUTED),
        "small_italic": ParagraphStyle(
            "small_italic", fontName=SERIF_ITALIC, fontSize=9, leading=12, textColor=MUTED,
        ),
        "cell": ParagraphStyle("cell", fontName=SERIF, fontSize=9.5, leading=13, textColor=INK),
        "cell_head": ParagraphStyle(
            "cell_head", fontName=SANS_BOLD, fontSize=9, leading=12, textColor=INK,
        ),
    }


# --- Score bar flowable ------------------------------------------------------


class ScoreBar(Flowable):
    """A horizontal 0-max bar rendered in the single accent color over a light track."""

    def __init__(self, score: int, max_score: int = 5, width: float = 1.6 * inch, height: float = 9) -> None:
        super().__init__()
        self.score = max(0, min(score, max_score))
        self.max_score = max_score
        self.width = width
        self.height = height

    def wrap(self, avail_width: float, avail_height: float) -> tuple[float, float]:
        return self.width, self.height

    def draw(self) -> None:
        c = self.canv
        c.setFillColor(TRACK)
        c.roundRect(0, 0, self.width, self.height, 2, fill=1, stroke=0)
        filled = self.width * (self.score / self.max_score)
        if filled > 0:
            c.setFillColor(ACCENT)
            c.roundRect(0, 0, filled, self.height, 2, fill=1, stroke=0)


# --- Footer ------------------------------------------------------------------


def _draw_footer(canvas, doc, *, deck_name: str) -> None:
    canvas.saveState()
    width, _ = PAGE_SIZE
    y = 0.55 * inch
    canvas.setStrokeColor(RULE)
    canvas.setLineWidth(0.5)
    canvas.line(MARGIN, y + 0.12 * inch, width - MARGIN, y + 0.12 * inch)
    canvas.setFont(SANS, 8)
    canvas.setFillColor(FAINT)
    canvas.drawString(MARGIN, y, deck_name[:80])
    canvas.drawRightString(width - MARGIN, y, f"Page {canvas.getPageNumber()}")
    canvas.restoreState()


# --- Helpers -----------------------------------------------------------------


def _top_fixes(payload: EvaluationPayload) -> list[tuple[str, str]]:
    """Pick the three highest-leverage fixes across dimensions.

    Leverage = ``(5 - score) * weight`` — room to improve, weighted by the dimension's
    contribution to the overall score. One fix is taken from each of the top three.
    """
    weights = dimension_weights()
    ranked = sorted(
        payload.dimensions,
        key=lambda d: (5 - d.score) * weights[Dimension(d.dimension)],
        reverse=True,
    )
    fixes: list[tuple[str, str]] = []
    for result in ranked:
        if not result.fixes:
            continue
        title = DIMENSION_BY_KEY[Dimension(result.dimension)].title
        fixes.append((title, result.fixes[0]))
        if len(fixes) == 3:
            break
    return fixes


def _thumb(path: Path, target_w: float, styles: dict[str, ParagraphStyle]):
    """Return an Image flowable scaled to ``target_w``, or an em dash if unavailable."""
    if not path or not path.exists():
        return Paragraph("&#8212;", styles["cell"])
    reader = ImageReader(str(path))
    iw, ih = reader.getSize()
    scale = target_w / iw if iw else 1.0
    return Image(str(path), width=target_w, height=ih * scale)


def _by_dimension(payload: EvaluationPayload) -> dict[Dimension, DimensionResult]:
    return {Dimension(d.dimension): d for d in payload.dimensions}


# --- Section builders --------------------------------------------------------


def _cover(payload: EvaluationPayload, deck_name: str, generated_at: datetime, s) -> list:
    return [
        Spacer(1, 1.4 * inch),
        Paragraph(_esc(deck_name), s["cover_name"]),
        Spacer(1, 6),
        Paragraph(generated_at.strftime("%B %d, %Y"), s["cover_date"]),
        Spacer(1, 0.7 * inch),
        Paragraph(str(payload.overall_score), s["score_big"]),
        Paragraph("out of 100", s["score_denom"]),
        Spacer(1, 10),
        Paragraph(_esc(payload.band), s["band"]),
        Spacer(1, 0.6 * inch),
        Paragraph(_esc(payload.headline), s["headline_fix"]),
        PageBreak(),
    ]


def _executive_summary(payload: EvaluationPayload, s) -> list:
    by_dim = _by_dimension(payload)
    header = [
        Paragraph("Dimension", s["cell_head"]),
        Paragraph("Score", s["cell_head"]),
        Paragraph("", s["cell_head"]),
    ]
    rows = [header]
    for spec in DIMENSIONS:
        result = by_dim[spec.key]
        rows.append(
            [
                Paragraph(_esc(spec.title), s["cell"]),
                Paragraph(f"{result.score} / 5", s["cell"]),
                ScoreBar(result.score),
            ]
        )
    table = Table(rows, colWidths=[2.4 * inch, 0.9 * inch, 1.9 * inch], hAlign="LEFT")
    table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LINEBELOW", (0, 0), (-1, 0), 0.75, RULE),
                ("LINEBELOW", (0, 1), (-1, -2), 0.25, TRACK),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                ("LEFTPADDING", (0, 0), (0, -1), 0),
            ]
        )
    )

    story: list = [
        Paragraph("Executive summary", s["h1"]),
        Paragraph(
            f"Overall {payload.overall_score}/100 &#8212; {_esc(payload.band)}.", s["body"]
        ),
        Spacer(1, 10),
        table,
        Spacer(1, 16),
        Paragraph("Three highest-leverage fixes", s["h2"]),
    ]
    fixes = _top_fixes(payload)
    story.append(
        ListFlowable(
            [
                ListItem(
                    Paragraph(f"<b>{_esc(title)}.</b> {_esc(fix)}", s["body"]),
                    leftIndent=18,
                    value=i + 1,
                )
                for i, (title, fix) in enumerate(fixes)
            ],
            bulletType="1",
            bulletFontName=SANS_BOLD,
            bulletColor=ACCENT,
        )
    )
    story.append(PageBreak())
    return story


def _dimension_page(result: DimensionResult, has_text_layer: bool, s) -> list:
    spec = DIMENSION_BY_KEY[Dimension(result.dimension)]
    story: list = [
        Paragraph(f"{_esc(spec.title)} &#8212; {result.score}/5", s["h1"]),
        Paragraph(f"<i>{_esc(spec.question)}</i>", s["small"]),
        Spacer(1, 8),
        Paragraph(_esc(result.anchor_rationale), s["body"]),
        Paragraph("Evidence", s["h2"]),
    ]
    if not has_text_layer:
        story.append(
            Paragraph(
                "This deck has no text layer, so the quotes below were transcribed from the "
                "slide images by the model and may differ slightly from the original wording.",
                s["small_italic"],
            )
        )
    for item in result.evidence:
        story.append(Paragraph(f"<b>Slide {item.slide_number}:</b> &#8220;{_esc(item.quote)}&#8221;", s["quote"]))
        story.append(Paragraph(_esc(item.comment), s["quote_comment"]))

    story.append(Paragraph("Fixes", s["h2"]))
    story.append(
        ListFlowable(
            [ListItem(Paragraph(_esc(fix), s["body"]), leftIndent=18, value=i + 1) for i, fix in enumerate(result.fixes)],
            bulletType="1",
            bulletFontName=SANS_BOLD,
            bulletColor=ACCENT,
        )
    )
    story.append(PageBreak())
    return story


def _slide_review(
    payload: EvaluationPayload,
    deck: IngestedDeck,
    slide_records: Sequence[SlideRecord] | None,
    s,
) -> list:
    type_by_number = {r.slide_number: r.slide_type.value.replace("_", " ") for r in (slide_records or [])}
    thumb_by_number = {p.number: p.thumb_path for p in deck.pages}

    header = [Paragraph(t, s["cell_head"]) for t in ("", "#", "Type", "Verdict", "Note")]
    rows = [header]

    # Real slides first (in order), then any 'missing' gap entries.
    present = sorted((n for n in {sn.slide_number for sn in payload.slide_notes} if n >= 1))
    note_by_number = {sn.slide_number: sn for sn in payload.slide_notes}

    for number in present:
        note = note_by_number[number]
        thumb = _thumb(thumb_by_number.get(number), 0.85 * inch, s) if number in thumb_by_number else Paragraph("&#8212;", s["cell"])
        rows.append(
            [
                thumb,
                Paragraph(str(number), s["cell"]),
                Paragraph(_esc(type_by_number.get(number, "&#8212;")), s["cell"]),
                Paragraph(_esc(note.verdict.value.capitalize()), s["cell"]),
                Paragraph(_esc(note.note), s["cell"]),
            ]
        )
    for note in payload.slide_notes:
        if note.slide_number >= 1:
            continue
        rows.append(
            [
                Paragraph("&#8212;", s["cell"]),
                Paragraph("&#8212;", s["cell"]),
                Paragraph("missing", s["cell"]),
                Paragraph(_esc(note.verdict.value.capitalize()), s["cell"]),
                Paragraph(_esc(note.note), s["cell"]),
            ]
        )

    table = Table(
        rows,
        colWidths=[1.0 * inch, 0.35 * inch, 1.0 * inch, 0.75 * inch, 3.4 * inch],
        hAlign="LEFT",
    )
    table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LINEBELOW", (0, 0), (-1, 0), 0.75, RULE),
                ("LINEBELOW", (0, 1), (-1, -1), 0.25, TRACK),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                ("LEFTPADDING", (0, 0), (0, -1), 0),
            ]
        )
    )

    story: list = [Paragraph("Slide-by-slide review", s["h1"]), table]
    if payload.missing_slide_types:
        gaps = ", ".join(t.value.replace("_", " ") for t in payload.missing_slide_types)
        story.append(Spacer(1, 10))
        story.append(Paragraph(f"<b>Missing slide types:</b> {_esc(gaps)}", s["body"]))
    story.append(PageBreak())
    return story


def _rewrites(payload: EvaluationPayload, s) -> list:
    by_label = {r.label.value: r for r in payload.rewrites}
    story: list = [Paragraph("Rewrites", s["h1"])]
    for label, title in REWRITE_TITLES.items():
        rewrite = by_label.get(label)
        if rewrite is None:
            continue
        story.append(Paragraph(_esc(title), s["h2"]))
        story.append(Paragraph(_esc(rewrite.text), s["body"]))
        story.append(Paragraph(f"<i>Why: {_esc(rewrite.changed_because)}</i>", s["small_italic"]))
        story.append(Spacer(1, 8))
    story.append(PageBreak())
    return story


def _appendix(
    payload: EvaluationPayload,
    deck: IngestedDeck,
    metrics: DeckMetrics,
    model: str,
    generated_at: datetime,
    s,
) -> list:
    buzz = (
        ", ".join(f"{k} &#215;{v}" for k, v in sorted(metrics.buzzword_hits.items()))
        if metrics.buzzword_hits
        else "none"
    )
    acronyms = ", ".join(metrics.unexpanded_acronyms) if metrics.unexpanded_acronyms else "none"
    flesch = "n/a" if metrics.flesch_reading_ease is None else f"{metrics.flesch_reading_ease}"

    facts = [
        ("Slides", str(metrics.slide_count)),
        ("Total words (text layer)", str(metrics.total_words)),
        ("Flesch Reading Ease (deck)", flesch),
        ("Buzzword hits", buzz),
        ("Unexpanded acronyms", acronyms),
        ("Text layer present", "yes" if deck.has_text_layer else "no (image-only deck)"),
        ("Estimated vision tokens", f"~{deck.estimated_image_tokens:,}"),
        ("Model", model),
        ("Rubric version", RUBRIC_VERSION),
        ("Generated", generated_at.strftime("%Y-%m-%d %H:%M UTC")),
    ]
    rows = [[Paragraph(_esc(k), s["cell_head"]), Paragraph(_esc(v), s["cell"])] for k, v in facts]
    table = Table(rows, colWidths=[2.4 * inch, 3.6 * inch], hAlign="LEFT")
    table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LINEBELOW", (0, 0), (-1, -1), 0.25, TRACK),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ("LEFTPADDING", (0, 0), (0, -1), 0),
            ]
        )
    )

    how_to_read = (
        "Scores run 0&#8211;5 against fixed anchors and are weighted into a 0&#8211;100 overall "
        "score (Clarity 25%, Structure/Messaging/Differentiation 20% each, Investor Engagement "
        "15%). Bands: 0&#8211;39 Rebuild, 40&#8211;59 Major revision, 60&#8211;79 Tighten, "
        "80&#8211;100 Investor-ready. Every judgment is tied to a specific slide; treat the "
        "highest-leverage fixes on the summary page as your running order."
    )
    return [
        Paragraph("Appendix", s["h1"]),
        Paragraph("Deterministic metrics and provenance", s["h2"]),
        table,
        Spacer(1, 12),
        Paragraph("How to read the scores", s["h2"]),
        Paragraph(how_to_read, s["body"]),
    ]


# --- Entry point -------------------------------------------------------------


def render_report(
    payload: EvaluationPayload,
    deck: IngestedDeck,
    metrics: DeckMetrics,
    out_path: Path,
    *,
    deck_name: str = "Untitled deck",
    slide_records: Sequence[SlideRecord] | None = None,
    model: str = "unknown",
    generated_at: datetime | None = None,
) -> Path:
    """Render the evaluation to a PDF at ``out_path`` and return that path."""
    generated_at = generated_at or datetime.now(timezone.utc)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    s = _styles()
    by_dim = _by_dimension(payload)

    story: list = []
    story += _cover(payload, deck_name, generated_at, s)
    story += _executive_summary(payload, s)
    for spec in DIMENSIONS:  # canonical order, one page each
        story += _dimension_page(by_dim[spec.key], deck.has_text_layer, s)
    story += _slide_review(payload, deck, slide_records, s)
    story += _rewrites(payload, s)
    story += _appendix(payload, deck, metrics, model, generated_at, s)

    doc = SimpleDocTemplate(
        str(out_path),
        pagesize=PAGE_SIZE,
        leftMargin=MARGIN,
        rightMargin=MARGIN,
        topMargin=MARGIN,
        bottomMargin=MARGIN,
        title="90 Second Pitch Analysis",
        author="90 Second Pitch Analysis",
    )
    footer = partial(_draw_footer, deck_name=deck_name)
    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return out_path
