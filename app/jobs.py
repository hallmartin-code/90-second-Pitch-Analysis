"""The background evaluation worker: ingest → metrics → evaluate → render (SPEC §9).

Runs in a FastAPI ``BackgroundTasks`` thread. Updates the ``Job`` row at each stage so the
polling UI shows honest progress, and turns any failure into a plain, user-safe sentence —
never a stack trace or a raw model error.
"""

from __future__ import annotations

import logging
from pathlib import Path
from uuid import uuid4

from sqlmodel import Session

from app.config import get_settings
from app.db import engine
from app.evaluator import EvaluationError, evaluate_deck
from app.ingest import IngestError, ingest_pdf
from app.metrics import compute_deck_metrics
from app.models import Deck, Evaluation, Job, JobStatus
from app.report import render_report
from app.rubric import RUBRIC_VERSION

logger = logging.getLogger(__name__)


def _advance(session: Session, job: Job, status: JobStatus, **fields: object) -> None:
    job.status = status.value
    for key, value in fields.items():
        setattr(job, key, value)
    from datetime import datetime, timezone

    job.updated_at = datetime.now(timezone.utc)
    session.add(job)
    session.commit()


def _fail(session: Session, job: Job, message: str) -> None:
    job.status = JobStatus.failed.value
    job.error = message
    from datetime import datetime, timezone

    job.updated_at = datetime.now(timezone.utc)
    session.add(job)
    session.commit()


def run_evaluation(job_id: str, deck_id: str, data: bytes, deck_name: str) -> None:
    """Execute the full pipeline for one deck, updating the job as it goes."""
    settings = get_settings()
    with Session(engine) as session:
        job = session.get(Job, job_id)
        if job is None:
            logger.error("Job %s vanished before it could run", job_id)
            return
        try:
            _advance(session, job, JobStatus.rasterizing)
            deck = ingest_pdf(
                data,
                deck_id=deck_id,
                storage_root=settings.storage_path,
                max_bytes=settings.max_upload_bytes,
                max_pages=settings.max_pages,
            )
            deck_row = session.get(Deck, deck_id)
            if deck_row is not None:
                deck_row.page_count = deck.page_count
                deck_row.has_text_layer = deck.has_text_layer
                session.add(deck_row)

            logger.info(
                "Job %s: %d pages, text_layer=%s, ~%d estimated vision tokens",
                job_id,
                deck.page_count,
                deck.has_text_layer,
                deck.estimated_image_tokens,
            )
            _advance(session, job, JobStatus.parsing_slides, page_total=deck.page_count)
            metrics = compute_deck_metrics([(p.number, p.text) for p in deck.pages])

            _advance(session, job, JobStatus.evaluating)
            result = evaluate_deck(deck, metrics, settings)

            _advance(session, job, JobStatus.rendering)
            report_path: Path = settings.storage_path / "decks" / deck_id / "report.pdf"
            render_report(
                result.payload,
                deck,
                metrics,
                report_path,
                deck_name=deck_name,
                slide_records=result.slide_records,
                model=result.model,
            )

            session.add(
                Evaluation(
                    id=uuid4().hex,
                    deck_id=deck_id,
                    job_id=job_id,
                    model=result.model,
                    rubric_version=RUBRIC_VERSION,
                    overall_score=result.payload.overall_score,
                    band=result.payload.band,
                    payload_json=result.payload.model_dump_json(),
                    report_path=str(report_path),
                )
            )
            _advance(session, job, JobStatus.done, page_current=deck.page_count)
            logger.info("Job %s complete: %s / %s", job_id, result.payload.overall_score, result.payload.band)

        except (IngestError, EvaluationError) as exc:
            logger.info("Job %s failed cleanly: %s", job_id, exc.message)
            _fail(session, job, exc.message)
        except Exception:  # noqa: BLE001 — last line of defense; never leak a stack trace
            logger.exception("Job %s crashed unexpectedly", job_id)
            _fail(session, job, "Something went wrong while evaluating the deck. Please try again.")
