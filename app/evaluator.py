"""Two-stage LLM orchestration for the Raskin evaluation.

Stage A parses each slide into a descriptive ``SlideRecord`` via vision. A short cover call
reads the company name and locates the logo for the report header. Stage B evaluates the
whole deck against Andy Raskin's framework and is forced, via tool use, to emit a single
``EvaluationPayload`` — the tool's input schema *is* the payload schema, so no JSON is parsed
out of prose. The overall score is recomputed deterministically from the element scores.

``FAKE_LLM=1`` short-circuits the LLM with a canned, valid payload so the pipeline runs
without an API key. The model default (`claude-sonnet-5`) comes from settings; every call
forces a specific tool, so thinking is disabled (a specific ``tool_choice`` is incompatible
with adaptive thinking).
"""

from __future__ import annotations

import base64
import json
import logging
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import anthropic
import pymupdf
from pydantic import BaseModel, Field, ValidationError

from app.config import Settings
from app.fake_llm import fake_company_name, fake_evaluation_payload, fake_slide_records
from app.ingest import IngestedDeck, IngestedPage
from app.metrics import DeckMetrics
from app.rubric import (
    RaskinElement,
    SlideType,
    TextDensity,
    aggregate_overall,
    render_rubric_text,
)
from app.schemas import EvaluationPayload, SlideRecord

logger = logging.getLogger(__name__)

PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"
STAGE_A_BATCH_SIZE = 5
STAGE_A_MAX_TOKENS = 8000
STAGE_B_MAX_TOKENS = 16000
COVER_MAX_TOKENS = 1000
LOGO_DPI = 200

_SLIDE_PARSE_TOOL = "record_slides"
_EVALUATE_TOOL = "submit_evaluation"
_COVER_TOOL = "cover_info"


class EvaluationError(Exception):
    """A user-facing evaluation failure. ``message`` is safe to show verbatim."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


@dataclass(slots=True)
class EvaluationResult:
    """The payload, the slide records, the model, and the extracted company logo path."""

    payload: EvaluationPayload
    slide_records: list[SlideRecord]
    model: str
    logo_path: Path | None = None


class _SlideBatch(BaseModel):
    """Stage A tool wrapper: a batch of slide records."""

    slides: list[SlideRecord]


class _CoverInfo(BaseModel):
    """Cover-call tool output: company name and the logo's bounding box (0-1 coords)."""

    company_name: str = ""
    logo_found: bool = False
    x0: float = Field(default=0.0, ge=0, le=1)
    y0: float = Field(default=0.0, ge=0, le=1)
    x1: float = Field(default=0.0, ge=0, le=1)
    y1: float = Field(default=0.0, ge=0, le=1)


@lru_cache(maxsize=8)
def _prompt(name: str) -> str:
    return (PROMPTS_DIR / name).read_text(encoding="utf-8")


def _unstringify(value: object) -> object:
    """Recursively parse JSON-encoded string values back into objects/lists.

    Models sometimes return a tool argument with a nested object or list encoded as a JSON
    string (more common when the tool schema uses ``$ref``/``$defs``). This normalizes such
    values so Pydantic validation sees the real structure.
    """
    if isinstance(value, str):
        stripped = value.strip()
        if stripped[:1] in "[{":
            try:
                return _unstringify(json.loads(stripped))
            except (ValueError, TypeError):
                return value
        return value
    if isinstance(value, list):
        return [_unstringify(item) for item in value]
    if isinstance(value, dict):
        return {key: _unstringify(item) for key, item in value.items()}
    return value


# --- Verification (pure Python, no LLM) --------------------------------------


def _coerce_slide_record(raw: dict, expected_number: int, page_count: int) -> SlideRecord:
    """Best-effort build a valid SlideRecord from a model-returned dict.

    Stage A is descriptive, not evaluative — so a record with an out-of-enum ``slide_type``,
    an odd ``text_density``, too many ``key_points``, or a missing field should be repaired,
    never allowed to fail the whole deck.
    """

    def _slide_type(value: object) -> SlideType:
        try:
            return SlideType(value)
        except (ValueError, KeyError):
            return SlideType.unclear

    def _density(value: object) -> TextDensity:
        try:
            return TextDensity(value)
        except (ValueError, KeyError):
            return TextDensity.balanced

    def _str_list(value: object, limit: int) -> list[str]:
        if isinstance(value, str):
            value = [value]
        if not isinstance(value, list):
            return []
        return [str(item) for item in value if item is not None][:limit]

    number = raw.get("slide_number")
    if not (isinstance(number, int) and 1 <= number <= page_count):
        number = expected_number
    headline = str(raw.get("headline") or f"Slide {number}")

    return SlideRecord(
        slide_number=number,
        slide_type=_slide_type(raw.get("slide_type")),
        headline=headline,
        key_points=_str_list(raw.get("key_points"), 5),
        has_chart=bool(raw.get("has_chart")),
        has_screenshot=bool(raw.get("has_screenshot")),
        text_density=_density(raw.get("text_density")),
        readability_notes=_str_list(raw.get("readability_notes"), 10),
    )


def finalize_scores(payload: EvaluationPayload) -> EvaluationPayload:
    """Clamp element scores to 0-10 and recompute the overall as their mean."""
    clamped = [
        e.model_copy(update={"score": max(0.0, min(10.0, float(e.score)))})
        for e in payload.elements
    ]
    scores = {RaskinElement(e.element): e.score for e in clamped}
    return payload.model_copy(
        update={"elements": clamped, "overall_score": aggregate_overall(scores)}
    )


# --- LLM plumbing ------------------------------------------------------------


def _client(settings: Settings) -> anthropic.Anthropic:
    if not settings.anthropic_api_key:
        raise EvaluationError(
            "The evaluator is not configured with an API key. Set ANTHROPIC_API_KEY, "
            "or run with FAKE_LLM=1."
        )
    return anthropic.Anthropic(api_key=settings.anthropic_api_key)


def _image_block(page: IngestedPage) -> dict:
    data = base64.standard_b64encode(page.image_path.read_bytes()).decode("ascii")
    return {
        "type": "image",
        "source": {"type": "base64", "media_type": "image/png", "data": data},
    }


def _invoke_tool(
    client: anthropic.Anthropic,
    *,
    settings: Settings,
    system: str,
    content: list[dict],
    tool_name: str,
    tool_schema: dict,
    tool_description: str,
    max_tokens: int,
) -> dict:
    """Call the model, forcing ``tool_name``, and return the tool input dict."""
    try:
        response = client.with_options(timeout=settings.llm_timeout_seconds).messages.create(
            model=settings.anthropic_model,
            max_tokens=max_tokens,
            system=system,
            thinking={"type": "disabled"},  # required with a specific tool_choice
            tools=[
                {
                    "name": tool_name,
                    "description": tool_description,
                    "input_schema": tool_schema,
                }
            ],
            tool_choice={"type": "tool", "name": tool_name},
            messages=[{"role": "user", "content": content}],
        )
    except anthropic.APITimeoutError as exc:
        raise EvaluationError(
            "The evaluation timed out. Please try again in a moment."
        ) from exc
    except anthropic.APIStatusError as exc:
        logger.warning("Anthropic API error (%s): %s", exc.status_code, exc.message)
        raise EvaluationError("The evaluation service returned an error. Please try again.") from exc
    except anthropic.APIConnectionError as exc:
        raise EvaluationError("Could not reach the evaluation service. Please try again.") from exc

    for block in response.content:
        if block.type == "tool_use" and block.name == tool_name:
            normalized = _unstringify(dict(block.input))
            return normalized if isinstance(normalized, dict) else {}
    raise EvaluationError("The evaluator did not return a structured result.")


# --- Stage A: parse slides ---------------------------------------------------


def _stage_a(client: anthropic.Anthropic, deck: IngestedDeck, settings: Settings) -> list[SlideRecord]:
    system = _prompt("slide_parse.md")
    schema = _SlideBatch.model_json_schema()
    by_number: dict[int, SlideRecord] = {}

    for start in range(0, deck.page_count, STAGE_A_BATCH_SIZE):
        batch = deck.pages[start : start + STAGE_A_BATCH_SIZE]
        content: list[dict] = []
        for page in batch:
            text = page.text if page.text else "[no text layer on this slide]"
            content.append(
                {"type": "text", "text": f"Slide {page.number} (page {page.number}). Extracted text:\n{text}"}
            )
            content.append(_image_block(page))
        content.append(
            {
                "type": "text",
                "text": (
                    f"Record all {len(batch)} slides above (page numbers "
                    f"{batch[0].number}-{batch[-1].number}) by calling {_SLIDE_PARSE_TOOL}."
                ),
            }
        )
        raw = _invoke_tool(
            client,
            settings=settings,
            system=system,
            content=content,
            tool_name=_SLIDE_PARSE_TOOL,
            tool_schema=schema,
            tool_description="Record one descriptive SlideRecord per slide in the batch.",
            max_tokens=STAGE_A_MAX_TOKENS,
        )
        # Unwrap common shapes: {"slides": [...]}, a bare [...], or a double-wrapped
        # {"slides": {"slides": [...]}} that some models emit.
        slides = raw.get("slides", raw) if isinstance(raw, dict) else raw
        if isinstance(slides, dict) and "slides" in slides:
            slides = slides["slides"]
        if not isinstance(slides, list):
            logger.warning("Stage A batch was not a list; skipping (got %s)", type(slides).__name__)
            slides = []
        # Coerce each record individually — one malformed record must not fail the deck.
        for index, item in enumerate(slides):
            if not isinstance(item, dict):
                continue
            expected = batch[index].number if index < len(batch) else batch[-1].number
            record = _coerce_slide_record(item, expected, deck.page_count)
            by_number[record.slide_number] = record

    # Fill any slide the model skipped with a minimal 'unclear' record so downstream code
    # always has one record per page.
    records: list[SlideRecord] = []
    for page in deck.pages:
        records.append(
            by_number.get(page.number)
            or SlideRecord(
                slide_number=page.number,
                slide_type=SlideType.unclear,
                headline=f"Slide {page.number}",
                key_points=[],
                has_chart=False,
                has_screenshot=False,
                text_density=TextDensity.balanced,
                readability_notes=["Not parsed by Stage A."],
            )
        )
    return records


# --- Stage B: evaluate the deck ----------------------------------------------


def _records_digest(records: list[SlideRecord]) -> str:
    lines = []
    for r in records:
        lines.append(
            f"Slide {r.slide_number} [{r.slide_type.value}] {r.headline!r} "
            f"density={r.text_density.value} chart={r.has_chart} screenshot={r.has_screenshot}"
        )
        for point in r.key_points:
            lines.append(f"    - {point}")
        for note in r.readability_notes:
            lines.append(f"    ! {note}")
    return "\n".join(lines)


def _metrics_digest(metrics: DeckMetrics) -> str:
    buzz = ", ".join(f"{k} x{v}" for k, v in sorted(metrics.buzzword_hits.items())) or "none"
    acronyms = ", ".join(metrics.unexpanded_acronyms) or "none"
    flesch = "n/a" if metrics.flesch_reading_ease is None else str(metrics.flesch_reading_ease)
    per_slide = "; ".join(
        f"s{m.slide_number}:{m.word_count}w" for m in metrics.slides
    )
    return (
        f"slides={metrics.slide_count}, total_words={metrics.total_words}, "
        f"deck_flesch={flesch}\nbuzzwords: {buzz}\nunexpanded_acronyms: {acronyms}\n"
        f"words_per_slide: {per_slide}"
    )


def _stage_b_content(
    deck: IngestedDeck, metrics: DeckMetrics, records: list[SlideRecord], extra: str | None
) -> list[dict]:
    content: list[dict] = [
        {"type": "text", "text": "SLIDE RECORDS (from Stage A):\n" + _records_digest(records)},
        {"type": "text", "text": "DETERMINISTIC METRICS (ground truth):\n" + _metrics_digest(metrics)},
        {"type": "text", "text": "Images of the first three slides follow for direct inspection of the opening."},
    ]
    for page in deck.pages[:3]:
        content.append(_image_block(page))
    if extra:
        content.append({"type": "text", "text": extra})
    content.append(
        {"type": "text", "text": f"Evaluate the deck now by calling {_EVALUATE_TOOL}."}
    )
    return content


def _stage_b(
    client: anthropic.Anthropic,
    deck: IngestedDeck,
    metrics: DeckMetrics,
    records: list[SlideRecord],
    settings: Settings,
) -> EvaluationPayload:
    system = _prompt("deck_evaluate.md").replace("{rubric}", render_rubric_text())
    schema = EvaluationPayload.model_json_schema()
    extra: str | None = None

    for attempt in range(2):  # one retry with the validation error appended
        raw = _invoke_tool(
            client,
            settings=settings,
            system=system,
            content=_stage_b_content(deck, metrics, records, extra),
            tool_name=_EVALUATE_TOOL,
            tool_schema=schema,
            tool_description="Submit the complete Raskin evaluation as an EvaluationPayload.",
            max_tokens=STAGE_B_MAX_TOKENS,
        )
        try:
            return EvaluationPayload.model_validate(raw)
        except ValidationError as exc:
            logger.warning("Stage B payload invalid (attempt %d): %s", attempt + 1, exc)
            extra = (
                "Your previous response failed validation with these errors. Fix them and "
                f"resubmit exactly the 5 Raskin elements:\n{exc}"
            )
    raise EvaluationError("The evaluation could not be completed correctly. Please try again.")


# --- Cover: company name + logo ----------------------------------------------


def _extract_cover(
    client: anthropic.Anthropic, deck: IngestedDeck, settings: Settings
) -> tuple[str, Path | None]:
    """Read the company name and crop the logo from the cover slide. Never fatal."""
    if not deck.pages:
        return "", None
    cover = deck.pages[0]
    content = [
        {
            "type": "text",
            "text": (
                "This is the cover slide of a pitch deck. Return the company's name, and the "
                "bounding box of its logo as fractions of the image (x0,y0 top-left, x1,y1 "
                "bottom-right, each 0-1). Set logo_found=false if there is no clear logo."
            ),
        },
        _image_block(cover),
        {"type": "text", "text": f"Call {_COVER_TOOL}."},
    ]
    try:
        raw = _invoke_tool(
            client,
            settings=settings,
            system="You extract a company name and logo location from a pitch-deck cover.",
            content=content,
            tool_name=_COVER_TOOL,
            tool_schema=_CoverInfo.model_json_schema(),
            tool_description="Report the company name and logo bounding box.",
            max_tokens=COVER_MAX_TOKENS,
        )
        info = _CoverInfo.model_validate(raw)
    except (EvaluationError, ValidationError) as exc:
        logger.info("Cover extraction failed (non-fatal): %s", exc)
        return "", None

    logo_path = _crop_logo(deck, info) if info.logo_found else None
    return info.company_name.strip(), logo_path


def _crop_logo(deck: IngestedDeck, info: _CoverInfo) -> Path | None:
    """Render the logo region of the cover to a PNG. Returns None if the box is implausible."""
    if not (0 <= info.x0 < info.x1 <= 1 and 0 <= info.y0 < info.y1 <= 1):
        return None
    width_frac, height_frac = info.x1 - info.x0, info.y1 - info.y0
    if width_frac < 0.02 or height_frac < 0.01 or width_frac * height_frac > 0.9:
        return None  # too tiny or basically the whole slide

    try:
        doc = pymupdf.open(deck.source_path)
        try:
            page = doc[0]
            rect = page.rect
            clip = pymupdf.Rect(
                rect.x0 + info.x0 * rect.width,
                rect.y0 + info.y0 * rect.height,
                rect.x0 + info.x1 * rect.width,
                rect.y0 + info.y1 * rect.height,
            )
            pix = page.get_pixmap(dpi=LOGO_DPI, clip=clip, alpha=False)
            out_path = deck.source_path.parent / "logo.png"
            pix.save(out_path)
            return out_path
        finally:
            doc.close()
    except Exception as exc:  # noqa: BLE001 — logo is cosmetic; never fail the report over it
        logger.info("Logo crop failed (non-fatal): %s", exc)
        return None


# --- Orchestration -----------------------------------------------------------


def evaluate_deck(
    deck: IngestedDeck, metrics: DeckMetrics, settings: Settings
) -> EvaluationResult:
    """Run the full evaluation pipeline and return a result.

    The only hard failures are an unreachable API or a Stage B payload that stays
    schema-invalid after one retry. Cover/logo extraction is best-effort and never fatal.
    """
    if settings.fake_llm:
        records = fake_slide_records(deck)
        payload = finalize_scores(fake_evaluation_payload(deck, metrics))
        return EvaluationResult(payload, records, model="fake-llm", logo_path=None)

    client = _client(settings)
    logger.info("Evaluating deck %s (~%d image tokens)", deck.deck_id, deck.estimated_image_tokens)

    records = _stage_a(client, deck, settings)
    company_name, logo_path = _extract_cover(client, deck, settings)
    payload = _stage_b(client, deck, metrics, records, settings)

    # Prefer the cover-call name if the evaluation left it blank.
    if not payload.company_name.strip() and company_name:
        payload = payload.model_copy(update={"company_name": company_name})

    payload = finalize_scores(payload)
    return EvaluationResult(payload, records, model=settings.anthropic_model, logo_path=logo_path)


# --- CLI ---------------------------------------------------------------------


def _main() -> None:
    """CLI: ``python -m app.evaluator <deck.pdf>`` → validated payload JSON on stdout."""
    import argparse
    import uuid

    from app.config import get_settings
    from app.ingest import ingest_pdf
    from app.metrics import compute_deck_metrics

    parser = argparse.ArgumentParser(description="Evaluate a pitch deck PDF.")
    parser.add_argument("pdf", type=Path, help="Path to the deck PDF")
    args = parser.parse_args()

    import sys

    try:  # avoid cp1252 crashes when printing unicode payloads on Windows consoles
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    settings = get_settings()

    deck = ingest_pdf(
        args.pdf.read_bytes(),
        deck_id=f"cli-{uuid.uuid4().hex[:8]}",
        storage_root=settings.storage_path,
        max_bytes=settings.max_upload_bytes,
        max_pages=settings.max_pages,
    )
    metrics = compute_deck_metrics([(p.number, p.text) for p in deck.pages])
    result = evaluate_deck(deck, metrics, settings)
    print(result.payload.model_dump_json(indent=2))
    print(
        f"\n[model={result.model} company={result.payload.company_name!r} "
        f"overall={result.payload.overall_score}/10 logo={result.logo_path}]"
    )


if __name__ == "__main__":
    _main()
