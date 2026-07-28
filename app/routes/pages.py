"""Server-rendered HTML pages and the HTMX polling partial (SPEC §9)."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from sqlmodel import Session, select

from app.config import get_settings
from app.db import engine
from app.models import STATUS_LABELS, TERMINAL_STATUSES, Deck, Evaluation, Job, JobStatus
from app.rubric import DIMENSION_BY_KEY, DIMENSIONS, Dimension
from app.schemas import EvaluationPayload
from app.templating import templates

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
def index(request: Request) -> HTMLResponse:
    settings = get_settings()
    return templates.TemplateResponse(
        request,
        "index.html",
        {"max_upload_mb": settings.max_upload_mb, "max_pages": settings.max_pages},
    )


@router.get("/status/{job_id}", response_class=HTMLResponse)
def status_partial(request: Request, job_id: str) -> HTMLResponse:
    """HTMX poll target: return the progress partial, or redirect when the job finishes."""
    with Session(engine) as session:
        job = session.get(Job, job_id)

    if job is None:
        return templates.TemplateResponse(
            request, "partials/error.html", {"message": "That job could not be found."}
        )
    if job.status == JobStatus.failed.value:
        return templates.TemplateResponse(
            request,
            "partials/error.html",
            {"message": job.error or "The evaluation failed. Please try again."},
        )
    if job.status == JobStatus.done.value:
        # Stop polling and send the browser to the report page.
        response = templates.TemplateResponse(request, "partials/done.html", {})
        response.headers["HX-Redirect"] = f"/report/{job.deck_id}"
        return response

    return templates.TemplateResponse(
        request,
        "partials/progress.html",
        {
            "job_id": job.id,
            "label": STATUS_LABELS.get(job.status, job.status),
            "page_total": job.page_total,
            "in_progress": job.status not in TERMINAL_STATUSES,
        },
    )


@router.get("/report/{deck_id}", response_class=HTMLResponse)
def report_page(request: Request, deck_id: str) -> HTMLResponse:
    with Session(engine) as session:
        deck = session.get(Deck, deck_id)
        evaluation = session.exec(
            select(Evaluation).where(Evaluation.deck_id == deck_id)
        ).first()

    if deck is None or evaluation is None:
        return templates.TemplateResponse(
            request,
            "partials/error.html",
            {"message": "That report could not be found."},
            status_code=404,
        )

    payload = EvaluationPayload.model_validate_json(evaluation.payload_json)
    by_dim = {Dimension(d.dimension): d for d in payload.dimensions}
    dimensions = [
        {"title": spec.title, "score": by_dim[spec.key].score}
        for spec in DIMENSIONS
        if spec.key in by_dim
    ]

    return templates.TemplateResponse(
        request,
        "report.html",
        {
            "deck_id": deck_id,
            "deck_name": deck.original_filename,
            "overall": payload.overall_score,
            "band": payload.band,
            "headline": payload.headline,
            "dimensions": dimensions,
            "rewrites": payload.rewrites,
            "model": evaluation.model,
        },
    )
