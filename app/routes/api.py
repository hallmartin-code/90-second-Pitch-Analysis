"""JSON/upload/file API routes (SPEC §9)."""

from __future__ import annotations

import logging
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, File, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from sqlmodel import Session, select

from app.config import get_settings
from app.db import engine
from app.ingest import IngestError, sanitize_filename, validate_pdf_bytes
from app.jobs import run_evaluation
from app.models import STATUS_LABELS, Deck, Evaluation, Job
from app.templating import templates

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/api/decks")
async def upload_deck(
    request: Request, background: BackgroundTasks, file: UploadFile = File(...)
):
    """Validate an uploaded PDF, start a background job, return the progress partial.

    The job id and deck id are also returned as response headers for programmatic callers.
    """
    settings = get_settings()
    data = await file.read()

    # Cheap validation up front so bad uploads get an immediate, specific message.
    try:
        doc = validate_pdf_bytes(
            data, max_bytes=settings.max_upload_bytes, max_pages=settings.max_pages
        )
        doc.close()
    except IngestError as exc:
        return templates.TemplateResponse(
            request, "partials/error.html", {"message": exc.message}, status_code=200
        )

    deck_id = uuid4().hex
    deck_name = sanitize_filename(file.filename or "deck.pdf")
    job_id = uuid4().hex

    with Session(engine) as session:
        session.add(Deck(id=deck_id, original_filename=deck_name))
        session.add(Job(id=job_id, deck_id=deck_id))
        session.commit()

    background.add_task(run_evaluation, job_id, deck_id, data, deck_name)

    response = templates.TemplateResponse(
        request,
        "partials/progress.html",
        {"job_id": job_id, "label": STATUS_LABELS["queued"], "page_total": 0, "in_progress": True},
    )
    response.headers["X-Job-Id"] = job_id
    response.headers["X-Deck-Id"] = deck_id
    return response


@router.get("/api/jobs/{job_id}")
def job_status(job_id: str) -> JSONResponse:
    """Return the job's status as JSON (the programmatic polling endpoint)."""
    with Session(engine) as session:
        job = session.get(Job, job_id)
        if job is None:
            return JSONResponse({"error": "job not found"}, status_code=404)
        return JSONResponse(
            {
                "job_id": job.id,
                "deck_id": job.deck_id,
                "status": job.status,
                "label": STATUS_LABELS.get(job.status, job.status),
                "page_current": job.page_current,
                "page_total": job.page_total,
                "error": job.error,
            }
        )


@router.get("/api/reports/{deck_id}.pdf")
def report_pdf(deck_id: str, inline: bool = False):
    """Serve the rendered report PDF. Attachment by default; ``?inline=1`` for preview."""
    with Session(engine) as session:
        deck = session.get(Deck, deck_id)
        evaluation = session.exec(
            select(Evaluation).where(Evaluation.deck_id == deck_id)
        ).first()

    if deck is None or evaluation is None:
        return JSONResponse({"error": "report not found"}, status_code=404)
    path = Path(evaluation.report_path)
    if not path.exists():
        return JSONResponse({"error": "report file missing"}, status_code=404)

    stem = deck.original_filename
    if stem.lower().endswith(".pdf"):
        stem = stem[:-4]
    clean_name = f"{stem or 'deck'}-evaluation.pdf"

    if inline:
        return FileResponse(
            path,
            media_type="application/pdf",
            headers={"Content-Disposition": f'inline; filename="{clean_name}"'},
        )
    return FileResponse(path, media_type="application/pdf", filename=clean_name)
