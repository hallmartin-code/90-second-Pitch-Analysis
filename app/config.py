"""Application settings, loaded from environment and an optional .env file."""

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# Repo root (this file lives at <root>/app/config.py).
BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    """Typed configuration. Values come from the environment or `.env`."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,  # allow Settings(fake_llm=...) in tests, not just FAKE_LLM
    )

    # LLM
    anthropic_api_key: str | None = Field(default=None, alias="ANTHROPIC_API_KEY")
    anthropic_model: str = Field(default="claude-sonnet-5", alias="ANTHROPIC_MODEL")
    fake_llm: bool = Field(default=True, alias="FAKE_LLM")
    llm_timeout_seconds: float = Field(default=120.0, alias="LLM_TIMEOUT_SECONDS")

    # Ingestion guardrails
    max_upload_mb: int = Field(default=30, alias="MAX_UPLOAD_MB")
    max_pages: int = Field(default=40, alias="MAX_PAGES")

    # Storage / persistence
    storage_dir: str = Field(default="storage", alias="STORAGE_DIR")
    database_url: str = Field(default="sqlite:///./data/app.db", alias="DATABASE_URL")

    @property
    def storage_path(self) -> Path:
        """Absolute path to the storage directory, created on access."""
        path = BASE_DIR / self.storage_dir
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024


@lru_cache
def get_settings() -> Settings:
    """Return a cached Settings instance."""
    return Settings()
