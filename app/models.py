"""SQLModel tables and job status vocabulary (SPEC §4, §9)."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from sqlmodel import Field, SQLModel


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class JobStatus(str, Enum):
    """Lifecycle of an evaluation job, surfaced to the polling UI."""

    queued = "queued"
    rasterizing = "rasterizing"
    parsing_slides = "parsing_slides"
    evaluating = "evaluating"
    rendering = "rendering"
    done = "done"
    failed = "failed"


# Honest, human-readable stage labels for the progress UI.
STATUS_LABELS: dict[str, str] = {
    JobStatus.queued.value: "Queued…",
    JobStatus.rasterizing.value: "Rendering your slides to images…",
    JobStatus.parsing_slides.value: "Reading each slide…",
    JobStatus.evaluating.value: "Scoring against the rubric…",
    JobStatus.rendering.value: "Building your report…",
    JobStatus.done.value: "Done",
    JobStatus.failed.value: "Failed",
}

TERMINAL_STATUSES = {JobStatus.done.value, JobStatus.failed.value}


class Deck(SQLModel, table=True):
    """An uploaded deck. Storage keys off ``id`` (a generated UUID)."""

    id: str = Field(primary_key=True)
    original_filename: str
    page_count: int = 0
    has_text_layer: bool = False
    created_at: datetime = Field(default_factory=_utcnow)


class Job(SQLModel, table=True):
    """A background evaluation job for a deck."""

    id: str = Field(primary_key=True)
    deck_id: str = Field(index=True)
    status: str = Field(default=JobStatus.queued.value)
    page_current: int = 0
    page_total: int = 0
    error: str | None = None
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)


class Evaluation(SQLModel, table=True):
    """A completed evaluation: the payload JSON plus provenance and the report path."""

    id: str = Field(primary_key=True)
    deck_id: str = Field(index=True)
    job_id: str
    model: str
    rubric_version: str
    overall_score: int
    band: str
    payload_json: str
    report_path: str
    created_at: datetime = Field(default_factory=_utcnow)
