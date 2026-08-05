"""Render an :class:`EvaluationPayload` into a ReportLab PDF report (Raskin format).

``render_report`` is a pure function: it takes the evaluation, an output path, and optional
logo images, and writes a document-style report that mirrors the manual TEN Capital format —
company logo, title, overall assessment, one section per Raskin element (score, evaluation,
recommendation), the obstacles/gifts pairs, a summary scorecard, the overall alignment, and a
suggested rebuild flow. Every page carries the TEN Capital footer with the company name.

It has no dependency on the web layer or an API key, so layout can be iterated on with a
hand-written fixture payload.
"""

from __future__ import annotations

from datetime import datetime, timezone
from functools import partial
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.lib.utils import ImageReader
from reportlab.platypus import (
    HRFlowable,
    Image,
    ListFlowable,
    ListItem,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from app.rubric import ELEMENT_BY_KEY, RASKIN_ELEMENTS, RaskinElement
from app.schemas import EvaluationPayload

# --- Palette & geometry ------------------------------------------------------

ACCENT = colors.HexColor("#e8622a")   # TEN Capital-style orange accent
INK = colors.HexColor("#1f2937")
MUTED = colors.HexColor("#4b5563")
FAINT = colors.HexColor("#9ca3af")
RULE = colors.HexColor("#d8d6d0")

PAGE_SIZE = LETTER  # portrait, document style
MARGIN = 0.9 * inch

SERIF = "Times-Roman"
SERIF_ITALIC = "Times-Italic"
SANS = "Helvetica"
SANS_BOLD = "Helvetica-Bold"

MAX_LOGO_HEIGHT = 0.95 * inch
FOOTER_LOGO_HEIGHT = 0.16 * inch


# The built-in ReportLab fonts render only Latin-1 (WinAnsi). Map the common non-Latin
# glyphs the model emits to safe equivalents, then drop anything else, so nothing renders as
# a black box.
_GLYPH_MAP = {
    "→": "->", "←": "<-", "↔": "<->", "⇒": "=>",
    "•": "-", "▪": "-", "…": "...", "−": "-",
    "‘": "'", "’": "'", "“": '"', "”": '"',
    "×": "x", "–": "-", "—": "-", " ": " ",
}


def _esc(text: object) -> str:
    value = str(text)
    for bad, good in _GLYPH_MAP.items():
        value = value.replace(bad, good)
    # Drop any remaining characters the built-in fonts can't render.
    value = value.encode("cp1252", "ignore").decode("cp1252")
    return escape(value)


def _fmt_score(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else str(value)


def _styles() -> dict[str, ParagraphStyle]:
    return {
        "title": ParagraphStyle(
            "title", fontName=SANS_BOLD, fontSize=20, leading=25, textColor=INK,
            alignment=TA_CENTER, spaceBefore=6,
        ),
        "subtitle": ParagraphStyle(
            "subtitle", fontName=SANS, fontSize=10, leading=14, textColor=MUTED,
            alignment=TA_CENTER,
        ),
        "h1": ParagraphStyle(
            "h1", fontName=SANS_BOLD, fontSize=14, leading=18, textColor=INK,
            spaceBefore=14, spaceAfter=4,
        ),
        "h2": ParagraphStyle(
            "h2", fontName=SANS_BOLD, fontSize=11, leading=15, textColor=INK,
            spaceBefore=8, spaceAfter=3,
        ),
        "score": ParagraphStyle(
            "score", fontName=SANS_BOLD, fontSize=11, leading=15, textColor=ACCENT,
            spaceBefore=2, spaceAfter=3,
        ),
        "body": ParagraphStyle(
            "body", fontName=SERIF, fontSize=10.5, leading=15, textColor=INK, spaceAfter=6,
        ),
        "overall": ParagraphStyle(
            "overall", fontName=SANS_BOLD, fontSize=13, leading=17, textColor=ACCENT,
            spaceBefore=8, spaceAfter=6,
        ),
        "cell": ParagraphStyle("cell", fontName=SERIF, fontSize=9.5, leading=13, textColor=INK),
        "cell_head": ParagraphStyle(
            "cell_head", fontName=SANS_BOLD, fontSize=9, leading=12, textColor=INK,
        ),
        "small_italic": ParagraphStyle(
            "small_italic", fontName=SERIF_ITALIC, fontSize=9.5, leading=13, textColor=MUTED,
            spaceAfter=6,
        ),
    }


# --- Footer ------------------------------------------------------------------


def _draw_footer(canvas, doc, *, company: str, date_str: str, ten_logo: ImageReader | None,
                 ten_logo_size: tuple[float, float] | None) -> None:
    canvas.saveState()
    width, _ = PAGE_SIZE
    y = 0.5 * inch
    canvas.setStrokeColor(RULE)
    canvas.setLineWidth(0.5)
    canvas.line(MARGIN, y + 0.14 * inch, width - MARGIN, y + 0.14 * inch)
    canvas.setFont(SANS, 7.5)
    canvas.setFillColor(FAINT)

    left = f"{company} - 90 second pitch Analysis"
    canvas.drawString(MARGIN, y, left[:70])
    canvas.drawCentredString(width / 2.0, y, str(canvas.getPageNumber()))

    compiled = f"Compiled on {date_str} by TEN Capital Network"
    if ten_logo is not None and ten_logo_size is not None:
        lw, lh = ten_logo_size
        canvas.drawImage(ten_logo, width - MARGIN - lw, y - 0.03 * inch, width=lw, height=lh, mask="auto")
        canvas.drawRightString(width - MARGIN - lw - 6, y, compiled)
    else:
        canvas.drawRightString(width - MARGIN, y, compiled)
    canvas.restoreState()


# --- Section builders --------------------------------------------------------


def _header(payload: EvaluationPayload, company_logo_path: Path | None, s) -> list:
    story: list = []
    if company_logo_path and Path(company_logo_path).exists():
        reader = ImageReader(str(company_logo_path))
        iw, ih = reader.getSize()
        if iw and ih:
            scale = min(MAX_LOGO_HEIGHT / ih, (3.2 * inch) / iw)
            img = Image(str(company_logo_path), width=iw * scale, height=ih * scale)
            img.hAlign = "CENTER"
            story.append(Spacer(1, 6))
            story.append(img)
    story.append(Paragraph(f"{_esc(payload.company_name)} - 90 second pitch Analysis", s["title"]))
    story.append(Spacer(1, 10))
    return story


def _overall_assessment(payload: EvaluationPayload, s) -> list:
    return [
        Paragraph("Overall Assessment", s["h1"]),
        Paragraph(_esc(payload.overall_assessment), s["body"]),
        HRFlowable(width="100%", thickness=0.5, color=RULE, spaceBefore=8, spaceAfter=6),
    ]


def _element_section(payload: EvaluationPayload, s) -> list:
    by_key = {RaskinElement(e.element): e for e in payload.elements}
    story: list = []
    for spec in RASKIN_ELEMENTS:
        result = by_key.get(spec.key)
        if result is None:
            continue
        story.append(Paragraph(f"{spec.number}. {_esc(spec.title)}", s["h1"]))
        story.append(Paragraph(f"Score: {_fmt_score(result.score)}/10", s["score"]))
        story.append(Paragraph(_esc(result.evaluation), s["body"]))

        if spec.key is RaskinElement.obstacles_and_gifts and payload.obstacles_and_gifts:
            story.extend(_obstacles_table(payload, s))

        story.append(Paragraph("Recommendation", s["h2"]))
        story.append(Paragraph(_esc(result.recommendation), s["body"]))
    return story


def _obstacles_table(payload: EvaluationPayload, s) -> list:
    rows = [[Paragraph("Obstacle", s["cell_head"]), Paragraph("Gift", s["cell_head"]),
             Paragraph("Assessment", s["cell_head"])]]
    for pair in payload.obstacles_and_gifts:
        rows.append([
            Paragraph(_esc(pair.obstacle), s["cell"]),
            Paragraph(_esc(pair.gift), s["cell"]),
            Paragraph(_esc(pair.assessment), s["cell"]),
        ])
    table = Table(rows, colWidths=[2.2 * inch, 2.2 * inch, 2.3 * inch], hAlign="LEFT")
    table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LINEBELOW", (0, 0), (-1, 0), 0.75, RULE),
        ("LINEBELOW", (0, 1), (-1, -1), 0.25, RULE),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING", (0, 0), (0, -1), 0),
    ]))
    return [Spacer(1, 4), Paragraph("Obstacles &amp; Gifts", s["h2"]), table, Spacer(1, 6)]


def _scorecard(payload: EvaluationPayload, s) -> list:
    by_key = {RaskinElement(e.element): e for e in payload.elements}
    rows = [[Paragraph("Framework Element", s["cell_head"]), Paragraph("Score", s["cell_head"]),
             Paragraph("Assessment", s["cell_head"])]]
    for spec in RASKIN_ELEMENTS:
        result = by_key.get(spec.key)
        if result is None:
            continue
        rows.append([
            Paragraph(_esc(spec.title), s["cell"]),
            Paragraph(f"{_fmt_score(result.score)}/10", s["cell"]),
            Paragraph(_esc(result.summary), s["cell"]),
        ])
    table = Table(rows, colWidths=[2.0 * inch, 0.7 * inch, 4.0 * inch], hAlign="LEFT")
    table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LINEBELOW", (0, 0), (-1, 0), 0.75, RULE),
        ("LINEBELOW", (0, 1), (-1, -1), 0.25, RULE),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING", (0, 0), (0, -1), 0),
    ]))
    return [
        Paragraph("Summary Scorecard", s["h1"]),
        table,
        Paragraph(f"Overall Raskin Alignment: {_fmt_score(payload.overall_score)}/10", s["overall"]),
    ]


def _rebuild(payload: EvaluationPayload, s) -> list:
    if not payload.rebuild_flow:
        return []
    items = [
        ListItem(
            Paragraph(
                f"<b>Slide {_esc(step.slides)} — {_esc(step.label)}:</b> {_esc(step.line)}",
                s["body"],
            ),
            leftIndent=16,
        )
        for step in payload.rebuild_flow
    ]
    return [
        Paragraph("If I Were Rebuilding This Deck Around Raskin", s["h1"]),
        Paragraph("Suggested opening flow:", s["h2"]),
        ListFlowable(items, bulletType="bullet", bulletColor=ACCENT, start="circle"),
    ]


# --- Entry point -------------------------------------------------------------


def render_report(
    payload: EvaluationPayload,
    out_path: Path,
    *,
    company_logo_path: Path | None = None,
    ten_logo_path: Path | None = None,
    generated_at: datetime | None = None,
) -> Path:
    """Render the Raskin evaluation to a PDF at ``out_path`` and return that path."""
    generated_at = generated_at or datetime.now(timezone.utc)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    s = _styles()
    company = payload.company_name.strip() or "Company"

    story: list = []
    story += _header(payload, company_logo_path, s)
    story += _overall_assessment(payload, s)
    story += _element_section(payload, s)
    story += _scorecard(payload, s)
    story += _rebuild(payload, s)

    ten_logo = None
    ten_logo_size = None
    if ten_logo_path and Path(ten_logo_path).exists():
        ten_logo = ImageReader(str(ten_logo_path))
        iw, ih = ten_logo.getSize()
        if iw and ih:
            ten_logo_size = (iw * (FOOTER_LOGO_HEIGHT / ih), FOOTER_LOGO_HEIGHT)

    # M/D/YYYY without leading zeros, cross-platform (strftime %-m/%-d is POSIX-only).
    date_str = f"{generated_at.month}/{generated_at.day}/{generated_at.year}"
    footer = partial(
        _draw_footer,
        company=company,
        date_str=date_str,
        ten_logo=ten_logo,
        ten_logo_size=ten_logo_size,
    )

    doc = SimpleDocTemplate(
        str(out_path),
        pagesize=PAGE_SIZE,
        leftMargin=MARGIN,
        rightMargin=MARGIN,
        topMargin=MARGIN,
        bottomMargin=MARGIN,
        title=f"{company} - 90 second pitch Analysis",
        author="TEN Capital Network",
    )
    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return out_path
