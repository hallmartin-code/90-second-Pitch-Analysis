# Build Brief: Pitch Deck Evaluator (PDF in → PDF report out)

> ⚠️ **SUPERSEDED (2026-08-05) re: the evaluation framework.** The app was migrated from the
> 5-dimension investor rubric described below (§7 — Clarity/Structure/Messaging/
> Differentiation/Investor Engagement, /100 with bands) to **Andy Raskin's strategic-narrative
> framework** (/10): (1) Name the Enemy, (2) Why Now?, (3) Tease the Promised Land, (4) Three
> Obstacles and Three Gifts, (5) Present Evidence — overall = mean of the five. The report is
> now document-style (company logo, Overall Assessment, per-element Score/Evaluation/
> Recommendation, Obstacles & Gifts table, Summary Scorecard, rebuild flow, TEN Capital
> footer), and the evidence/quote-verification path was removed in favour of a tolerant
> schema. The current framework lives in `app/rubric.py` (`RUBRIC_VERSION = "2.0-raskin"`).
> Everything else in this brief — stack, ingestion, `FAKE_LLM`, web flow, deploy — still
> holds. See the README for the current description.

> **How to use this file:** work one phase at a time. Do not build all seven phases in a
> single turn. Confirm the plan for each phase before writing code, and paste real test
> output at each checkpoint.

---

## 1. Mission

A Python web app where a founder uploads their **pitch deck as a PDF** and downloads an
**evaluation report as a PDF**.

The report scores the deck on five dimensions — **Clarity, Structure, Messaging,
Differentiation, Investor Engagement** — with every judgment tied to a specific slide, and
delivers rewrites that make the opening land with an investor in under 30 seconds.

**Primary user:** a founder preparing for an investor meeting.
**Success test:** the founder can act on the report without asking a follow-up question, and
would be willing to forward it to a co-founder as-is.

---

## 2. The critical technical decision: read decks with vision, not text extraction

A pitch deck PDF is **not a text document**. Text extraction returns disconnected bullet
fragments and is completely blind to layout, charts, slide hierarchy, and any text baked
into images — which is exactly where a deck's messaging lives. An evaluator built on
`extract_text()` alone will produce confident nonsense.

**Therefore:** rasterize every page to an image and send the images to Claude's vision API.
Use the extracted text layer only as a secondary signal for verifying quotes and detecting
scanned decks.

This is the single decision that determines whether the product works.

---

## 3. Tech stack — use exactly this

| Layer | Choice | Why |
|---|---|---|
| Runtime | Python 3.12 | (this build runs on locally installed 3.14) |
| Web framework | FastAPI + Uvicorn | async endpoints |
| Templating | Jinja2 | server-rendered |
| Interactivity | HTMX 2.x via CDN | upload + polling, no build step |
| Styling | Tailwind via CDN | no PostCSS pipeline |
| Validation | Pydantic v2 | |
| Persistence | SQLite + SQLModel | `./data/app.db` |
| **PDF reading** | **PyMuPDF (`pymupdf`)** | text *and* rasterization in one pure-wheel dep — no poppler, works on Windows |
| **PDF writing** | **ReportLab (Platypus)** | pure Python. **Do not use WeasyPrint** — system deps break Windows |
| LLM | `anthropic` SDK | vision-capable; model from env, default `claude-sonnet-5` |
| Text metrics | `textstat` | deterministic readability layer |
| Background work | FastAPI `BackgroundTasks` | 30–90s eval; **no Celery, no Redis** |
| Tests | `pytest`, `pytest-asyncio`, `httpx` | |
| Deps | `uv` + `pyproject.toml` | |

**Do not** introduce React, Node, Docker, Postgres, Celery, or auth in v1.

---

## 4. Repository layout

```
app/
  main.py               # app factory, routes, static mount
  config.py             # pydantic-settings from .env
  models.py             # SQLModel: Deck, Slide, Evaluation, Job
  schemas.py            # Pydantic contracts (Section 7)
  rubric.py             # dimensions, weights, score anchors, canonical slide types
  ingest.py             # PDF -> pages: text layer + rasterized PNGs (Section 5)
  metrics.py            # deterministic signals (Section 6.1)
  evaluator.py          # two-stage LLM orchestration (Section 6.2)
  report.py             # EvaluationPayload -> ReportLab PDF (Section 8)
  prompts/
    slide_parse.md      # Stage A system prompt
    deck_evaluate.md    # Stage B system prompt
  routes/
    pages.py            # GET /   GET /status/{job_id}   GET /report/{id}
    api.py              # POST /api/decks   GET /api/jobs/{id}   GET /api/reports/{id}.pdf
  templates/ + partials/
storage/
  decks/{deck_id}/source.pdf
  decks/{deck_id}/pages/page-01.png ...
  decks/{deck_id}/report.pdf
tests/
  test_ingest.py test_metrics.py test_schemas.py test_report.py test_routes.py
  fixtures/ good_deck.pdf weak_deck.pdf scanned_deck.pdf text_only.pdf
.env.example  pyproject.toml  README.md
```

---

## 5. Ingestion (`ingest.py`)

For each uploaded PDF:

1. **Validate.** Must be a real PDF (check magic bytes, not the extension). Reject if
   encrypted, over 30 MB, or over 40 pages, with a specific message. Store under a generated
   UUID — **never** use the client-supplied filename as a path component.
2. **Rasterize every page** with PyMuPDF at ~150 DPI, then downscale so the long edge is
   **≤ 1568 px**. Save as PNG.
3. **Extract the text layer** per page.
4. **Detect scanned decks:** if the whole document yields under ~50 characters, mark
   `has_text_layer=False`. The app still works — vision carries it — but evidence quotes are
   then model-transcribed and must be labelled as such in the report.
5. **Generate thumbnails** (long edge 400 px) for embedding in the output PDF.

```python
import fitz  # PyMuPDF

doc = fitz.open(path)
for i, page in enumerate(doc):
    pix = page.get_pixmap(dpi=150)
    pix.save(pages_dir / f"page-{i+1:02d}.png")
    text = page.get_text()
```

**Cost guardrail:** each rasterized page costs roughly 1,500–1,600 input tokens. A 20-slide
deck is ~32K tokens of images. Enforce the 40-page cap and log estimated token usage per
evaluation.

---

## 6. Evaluation pipeline

### 6.1 Deterministic pass (`metrics.py`) — no LLM
From the text layer (when present), compute and pass in as ground truth: word count per
slide, Flesch Reading Ease, longest sentence, buzzword hits against a curated list in
`rubric.py` (*synergy, disruptive, seamless, revolutionary, best-in-class, next-generation,
paradigm, leverage-as-a-verb*), unexpanded acronyms, and total slide count.

### 6.2 Stage A — parse each slide (vision)
Send slide images in batches of 5, with their page numbers and extracted text. Return one
`SlideRecord` per slide:

- `slide_type` — one of the canonical types in `rubric.py`: `cover, problem, solution,
  product, why_now, market, business_model, traction, competition, gtm, team, financials,
  ask, appendix, unclear`
- `headline` — the slide's actual title or dominant claim
- `key_points` — up to 5, verbatim where a text layer exists
- `has_chart`, `has_screenshot`, `text_density` (`sparse|balanced|dense`)
- `readability_notes` — anything visually broken

This stage is deliberately descriptive, not evaluative.

### 6.3 Stage B — evaluate the deck
Input: all `SlideRecord`s + the deterministic metrics + **the images of the first three
slides again**.

Output: one `EvaluationPayload` (Section 7), forced via **tool use** — define a single tool
whose input schema is the payload schema. Do not parse JSON out of prose.

System prompt persona (`prompts/deck_evaluate.md`): *a partner at an early-stage fund who has
seen 4,000 decks, is blunt, allergic to hype, and never awards a 5 for effort.* Inject the
full rubric with anchors. Cite the slide number before judging, score against the anchors
only, and — if the deck is missing a beat entirely — score low and name the gap.

### 6.4 Verification (Python, not the model)
- Every `Evidence.slide_number` must exist in the deck.
- Where `has_text_layer=True`, every `Evidence.quote` must appear in that slide's extracted
  text after whitespace normalization. Drop quotes that don't match and log them; if a
  dimension loses all its evidence, re-run that dimension once.
- Exactly 5 dimensions and exactly 3 rewrites, or reject and retry once with the validation
  error appended.

---

## 7. The rubric and data contracts

### 7.1 Dimensions — 0–5 whole points each, against fixed anchors

Encode as structured data in `rubric.py` (not prose inside a prompt string). Stamp
`rubric_version: "1.0"` on every stored evaluation.

**Clarity — 0.25.** *Can a smart generalist say what this company does after the first two
slides?* 0–1 reader cannot state what it does · 2–3 understandable only after the whole deck;
undefined jargon; the "what" arrives after slide 5 · 4–5 the cover or slide 2 names who it's
for and what it does in one concrete sentence.

**Structure — 0.20.** *Does the deck follow a load-bearing order?* Expected arc:
**cover/one-liner → problem → why now → solution → product → market → traction → business
model → competition → team → ask.** 0–1 no discernible order, or opens with the technology or
team · 2–3 beats present but out of order or wildly disproportionate · 4–5 all load-bearing
slides present, correctly ordered, proportionate. Report **missing slide types** and
**slide-count share per beat** explicitly.

**Messaging — 0.20.** *Is there one memorable claim, and is it supported?* 0–1 no identifiable
core claim or several competing ones · 2–3 a claim exists but is generic or unsupported · 4–5
one sharp claim, in the founder's own vocabulary, immediately backed by a specific number,
named customer, or observed fact. Flag every unquantified superlative and every claim with no
evidence on the slide.

**Differentiation — 0.20.** *Why this approach and not the obvious alternative, including
doing nothing?* 0–1 no alternative acknowledged · 2–3 names competitors but differentiates on
trivially copyable features, or shows a competitor matrix conveniently rigged so they win
every row · 4–5 names the real alternative (often the status quo or a spreadsheet), states
the wedge in one line, and points at something structurally hard to copy.

**Investor Engagement — 0.15.** *Does it answer what an investor is silently asking?* How big
can this get · why is this team unavoidable · why now · what do the next 18 months buy · what
exactly is the ask. 0–1 reads as a product brochure · 2–3 some questions answered, ask vague
or absent · 4–5 anticipates the questions, and the ask is specific (amount, use of funds, the
milestone it unlocks).

**Aggregation:** `overall = round(sum(score * weight) / 5 * 100)`.
Bands: **0–39 Rebuild · 40–59 Major revision · 60–79 Tighten · 80–100 Investor-ready.**

### 7.2 Schemas (`schemas.py`)

```python
class SlideRecord(BaseModel):
    slide_number: conint(ge=1)
    slide_type: SlideType
    headline: str
    key_points: list[str]
    has_chart: bool
    has_screenshot: bool
    text_density: Literal["sparse", "balanced", "dense"]
    readability_notes: list[str]

class Evidence(BaseModel):
    slide_number: conint(ge=1)
    quote: str                 # <= 200 chars, verbatim where a text layer exists
    comment: str               # why this drives the score

class DimensionResult(BaseModel):
    dimension: Literal["clarity","structure","messaging",
                       "differentiation","investor_engagement"]
    score: conint(ge=0, le=5)
    anchor_rationale: str      # must cite the anchor language it matched
    evidence: list[Evidence]   # 1-3, required
    fixes: list[str]           # 2-4 imperative, slide-specific actions

class Rewrite(BaseModel):
    label: Literal["one_liner", "cover_slide_copy", "thirty_second_verbal"]
    text: str
    changed_because: str

class SlideNote(BaseModel):
    slide_number: int
    verdict: Literal["keep", "tighten", "rebuild", "cut", "missing"]
    note: str

class EvaluationPayload(BaseModel):
    overall_score: conint(ge=0, le=100)
    band: str
    headline: str                        # <= 140 chars, the single most important fix
    dimensions: list[DimensionResult]    # exactly 5
    rewrites: list[Rewrite]              # exactly 3
    slide_notes: list[SlideNote]         # one per slide, plus any "missing" entries
    unsupported_claims: list[str]
    missing_slide_types: list[SlideType]
```

---

## 8. The output PDF (`report.py`)

Built with ReportLab Platypus from `EvaluationPayload` + the slide thumbnails. A pure
function: `render_report(payload, deck, out_path) -> Path`. Testable without the web layer or
an API key.

**Page 1 — Cover.** Deck name, date, overall score set large, band label, and the single
`headline` fix in one sentence. Nothing else.

**Page 2 — Executive summary.** The five dimension scores as a compact table with a bar per
row, then the three highest-leverage fixes pulled from across the dimensions.

**Pages 3–7 — One page per dimension.** Score and anchor rationale, evidence quotes as
indented blockquotes each labelled with its slide number, then the fixes as a numbered action
list.

**Slide-by-slide review.** A table: thumbnail · slide number · detected type · verdict ·
note. Missing slide types listed at the end as gaps.

**Rewrites page.** The one-liner, the cover slide copy, and the 30-second verbal pitch, each
with its `changed_because` in small italic type below.

**Appendix.** Deterministic metrics, model and `rubric_version` used, generation timestamp,
and a one-paragraph note on how to read the scores.

Typography rules: one serif for body, one sans for headings and numbers, generous margins, a
single accent color used only for scores and rules. Repeat the deck name and page number in
the footer of every page. **No** emoji, no clip art, no gradient fills, no traffic-light color
coding.

ReportLab gotcha: never use Unicode subscript/superscript characters — the built-in fonts
render them as black boxes. Use `<super>` / `<sub>` markup inside `Paragraph` objects.

---

## 9. Web flow

- `POST /api/decks` — multipart upload, validate, persist, create a `Job`, kick off
  `BackgroundTasks`, return `job_id` immediately.
- `GET /api/jobs/{id}` — status: `queued | rasterizing | parsing_slides | evaluating |
  rendering | done | failed`, with a page counter. The upload page polls this with HTMX every
  2 seconds and shows honest stage labels.
- `GET /report/{id}` — HTML summary with an inline PDF preview and a prominent download
  button.
- `GET /api/reports/{id}.pdf` — serves the file with `Content-Disposition: attachment` and a
  clean filename.

Failures surface as a plain sentence plus a retry button. Never a stack trace, never a raw
model error.

**Non-negotiables:** `ANTHROPIC_API_KEY` from `.env` only — never hardcoded, logged, or
rendered; `.env` in `.gitignore` with a committed `.env.example`. Sanitize all filenames.
Timeout every LLM call. Add `FAKE_LLM=1` mode returning a canned valid `EvaluationPayload` so
the report renderer and the whole web flow can be built and tested without spending tokens.

---

## 10. Build phases — stop at each checkpoint

**Phase 1 — Skeleton.** `pyproject.toml`, config, FastAPI app, health route, base template,
`.env.example`, README. *Checkpoint: `uv run uvicorn app.main:app --reload` serves a styled
page.*

**Phase 2 — Ingestion.** `ingest.py` + tests against all four PDF fixtures, including the
scanned one. Pure functions, no web, no LLM. *Checkpoint: `pytest tests/test_ingest.py`
green; PNGs on disk at the right dimensions.*

**Phase 3 — Rubric, schemas, metrics.** Tests asserting weights sum to 1.0, five dimensions,
aggregation math, band boundaries. *Checkpoint: schema round-trip and scoring tests green.*

**Phase 4 — Report renderer.** `report.py` driven by a hand-written fixture payload. Build
this **before** the LLM. *Checkpoint: `pytest tests/test_report.py` produces a PDF you and I
can both open and look at.*

**Phase 5 — Evaluator.** Stage A, Stage B, tool-use enforcement, evidence verification,
retries, `FAKE_LLM`. *Checkpoint: CLI run on `weak_deck.pdf` produces a validated payload.*

**Phase 6 — Web flow.** Upload, background job, polling, report page, download. *Checkpoint:
browser upload → progress → downloaded PDF.*

**Phase 7 — Hardening.** Route tests under `FAKE_LLM`, size and page limits, timeouts,
token-cost logging, README with setup steps.

---

## 11. Working agreement

- Before each phase: list the files you will create or modify and why. Wait for go-ahead.
- Write the test in the same turn as the code it covers.
- Type-hint every function. Docstrings only where intent isn't obvious from the signature.
- Never stub a function and move on — if something is unimplemented, say so out loud.
- If a requirement here is ambiguous or wrong, say so before coding.
- Prefer deleting code to adding flags. No abstraction with exactly one caller.
- After each phase, run the tests and paste the real output.

---

## 12. Definition of done (v1)

- [ ] Upload a 15-slide PDF deck → download a scored PDF report in under 2 minutes
- [ ] Every piece of evidence names a real slide number, and quotes match the text layer when one exists
- [ ] A scanned, image-only deck still evaluates successfully
- [ ] Re-running the same deck three times varies the overall score by ≤ 5 points
- [ ] The report PDF opens correctly in Preview, Acrobat, and Chrome
- [ ] All three rewrites are usable verbatim by the founder
- [ ] `pytest` green; app runs from a clean clone with `uv sync` and an API key
- [ ] No secrets in the repo, no stack traces reachable from the UI

---

## Build decisions (this environment)

- **Toolchain:** `uv` for dependency management, running on the locally installed **Python
  3.14** (not a pinned 3.12). `requires-python` stays `>=3.12` for spec compatibility.
- **Layout:** scaffolded at the repository root (no nested project subfolder).
- **`FAKE_LLM` defaults to 1** so the renderer and web flow build/test without an API key.
