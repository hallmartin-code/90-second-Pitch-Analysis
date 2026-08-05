# 90 Second Pitch Analysis

A Python web app where a founder uploads their **pitch deck as a PDF** and downloads an
**evaluation report as a PDF**.

The report scores the deck against **Andy Raskin's strategic-narrative framework** — the
five elements behind category-defining pitches — and reads like the manual analysis a
partner would write, with a concrete recommendation for every section.

## The framework (Raskin, /10)

Each element is scored 0–10; the overall alignment is their mean.

1. **Name the Enemy** — one dominant villain the deck rallies against.
2. **Why Now?** — an explicit reason the market is changing right now.
3. **Tease the Promised Land** — a vivid future state, introduced early.
4. **Three Obstacles and Three Gifts** — obstacles paired with the "magic gifts" that beat them.
5. **Present Evidence** — proof the story can come true (traction, validation, team).

## How it works

1. Every page of the deck is rasterized to an image and read with **Claude's vision API**
   (text extraction alone is blind to layout, charts, and text baked into images).
2. A deterministic pass computes readability and buzzword metrics as ground truth.
3. Stage A parses each slide into structured records; a short cover call reads the company
   name and locates the logo; Stage B scores the deck against the Raskin framework via forced
   tool-use, and the overall score is recomputed in Python as the mean of the five.
4. A document-style **ReportLab PDF** is rendered — company logo, title, Overall Assessment,
   one section per element (Score / Evaluation / Recommendation), Obstacles & Gifts table,
   Summary Scorecard, a suggested rebuild flow, and a TEN Capital footer — and offered for
   download.

The report's schema is deliberately **tolerant** (unknown fields ignored, text fields optional,
scores clamped in code) so a real deck's evaluation never fails validation.

## Stack

FastAPI · Jinja2 · HTMX + Tailwind (CDN) · Pydantic v2 · SQLite/SQLModel ·
PyMuPDF (PDF reading) · ReportLab (PDF writing) · Anthropic SDK · textstat.

## Setup

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/). This project runs on the
locally installed Python 3.14.

```bash
# 1. Install dependencies
uv sync --extra dev

# 2. Configure
cp .env.example .env
# Set ANTHROPIC_API_KEY for real evaluation (the default).
# No key handy? Set FAKE_LLM=1 to run the whole flow on a canned payload.

# 3. Run
uv run uvicorn app.main:app --reload
```

Then open http://127.0.0.1:8000 — and http://127.0.0.1:8000/healthz for a liveness check.

## Testing

```bash
uv run pytest
```

## Deploy to Railway

The app is built for [Railway](https://railway.app) (Nixpacks, no Docker).

1. **Create the service** from this repo. `railway.json` selects the Nixpacks builder and a
   `/healthz` health check; `nixpacks.toml` pins Python 3.12 and installs with `uv`. If the
   build picks the wrong Python, set `NIXPACKS_PYTHON_VERSION=3.12` in the Variables tab.
2. **Set variables** (Variables tab — never commit these):
   - `ANTHROPIC_API_KEY` — your key. (Real evaluation is the default; no `FAKE_LLM` needed.)
   - `ANTHROPIC_MODEL=claude-sonnet-5` (optional; this is the default).
   - `FAKE_LLM=1` — only if you want to run without spending tokens.
3. **Add a Volume** so reports and job history survive redeploys (the container filesystem is
   otherwise ephemeral). Mount it at `/data`, then set:
   - `STORAGE_DIR=/data/storage`
   - `DATABASE_URL=sqlite:////data/app.db`  ← note the **four** slashes (absolute path).
4. Deploy. Railway runs `uvicorn app.main:app --host 0.0.0.0 --port $PORT` and health-checks
   `/healthz`.

Uploads up to 30 MB pass through Railway's proxy fine. A 15-slide deck is ~30–90 s of
evaluation, handled by an in-process background task (no Celery/Redis).

## Configuration (`.env`)

| Variable | Default | Purpose |
|---|---|---|
| `ANTHROPIC_API_KEY` | — | Required for real evaluation (the default); unused when `FAKE_LLM=1`. |
| `ANTHROPIC_MODEL` | `claude-sonnet-5` | Vision-capable model for both stages. |
| `FAKE_LLM` | `0` | Set to `1` to return a canned valid payload instead of calling the API. |
| `LLM_TIMEOUT_SECONDS` | `120` | Per-call timeout. |
| `MAX_UPLOAD_MB` | `30` | Reject larger uploads. |
| `MAX_PAGES` | `40` | Reject longer decks (cost guardrail). |
| `STORAGE_DIR` | `storage` | Uploads, rendered pages, and reports. |
| `DATABASE_URL` | `sqlite:///./data/app.db` | Persistence. |

Secrets live only in `.env` (gitignored). Never commit real keys.

## Branding assets

- **Company logo** — extracted automatically from each deck's cover slide; nothing to
  configure.
- **TEN Capital footer logo** — drop a PNG (ideally transparent) at
  `app/static/ten_capital_logo.png`. It then appears in every report's footer. If the file is
  absent, the footer renders the text `Compiled on M/D/YYYY by TEN Capital Network` only.

## Build status

> The evaluation was migrated from the original 5-dimension investor rubric to **Andy
> Raskin's strategic-narrative framework** (v2). The report is now document-style. If you are
> upgrading an existing deployment, **delete the old database** (`data/app.db`, or the file on
> your Railway volume) once — the evaluation table's columns changed and old rows are
> incompatible.


- [x] **Phase 1 — Skeleton**: app factory, health route, styled landing page, config, README.
- [x] **Phase 2 — Ingestion**: PDF validation, per-page rasterization + thumbnails, text
      layer extraction, scanned-deck detection, synthetic fixtures. 19 tests green.
- [x] **Phase 3 — Rubric, schemas, metrics**: structured rubric (weights, anchors, bands,
      aggregation), Pydantic v2 contracts, deterministic text signals. 39 tests green.
- [x] **Phase 4 — Report renderer**: `render_report()` pure function → 10-page ReportLab
      PDF (cover, exec summary with score bars, per-dimension pages, slide-by-slide table
      with thumbnails, rewrites, appendix). 6 tests green; 64 total.
- [x] **Phase 5 — Evaluator**: two-stage vision pipeline (Stage A slide parse, Stage B
      forced tool-use `EvaluationPayload`), Python-side evidence verification + retries,
      deterministic score recomputation, `FAKE_LLM` mode, CLI. 10 tests green (+1 live,
      auto-skipped); 74 total.
- [x] **Phase 6 — Web flow**: multipart upload, SQLite/SQLModel persistence, background
      job with honest stage labels, HTMX 2s polling, report page with inline preview +
      download. Verified over real HTTP (upload → progress → PDF). 7 route tests; 81 total.
- [x] **Phase 7 — Hardening**: Railway deploy config (`railway.json`, `nixpacks.toml`,
      `Procfile`), volume-backed persistence docs, size/page-limit enforcement + route test,
      token-cost logging, security review. 82 tests green (+1 live, auto-skipped).
- [x] **Framework migration (Raskin, v2)**: replaced the 5-dimension rubric with Andy
      Raskin's strategic-narrative framework; document-style report with company-logo
      extraction and the TEN Capital footer; tolerant schema (no evidence/quote verification).
      Verified live end-to-end. 67 tests green (+1 live, auto-skipped).
