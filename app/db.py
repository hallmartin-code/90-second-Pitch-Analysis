"""Database engine and initialization.

SQLite via ``DATABASE_URL``. The engine is shared across the web request and the background
task thread, so SQLite needs ``check_same_thread=False``.
"""

from __future__ import annotations

from pathlib import Path

from sqlmodel import Session, SQLModel, create_engine

from app.config import get_settings

_url = get_settings().database_url
_connect_args = {"check_same_thread": False} if _url.startswith("sqlite") else {}
engine = create_engine(_url, connect_args=_connect_args)


def _ensure_sqlite_dir(url: str) -> None:
    """Create the parent directory for a file-backed SQLite database if needed."""
    if url.startswith("sqlite") and ":///" in url:
        path = url.split(":///", 1)[1]
        if path and path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)


def init_db() -> None:
    """Create tables. Import models first so they register on SQLModel.metadata."""
    import app.models  # noqa: F401  (registers Deck/Job/Evaluation)

    _ensure_sqlite_dir(_url)
    SQLModel.metadata.create_all(engine)


def get_session() -> Session:
    """Return a new session bound to the shared engine."""
    return Session(engine)
