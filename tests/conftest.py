"""Shared pytest fixtures.

Builds the synthetic PDF fixtures on demand so the suite runs from a clean clone.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.fixtures.build_fixtures import FIXTURE_NAMES, build_all

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture(scope="session")
def fixture_decks() -> dict[str, Path]:
    """Ensure all synthetic decks exist and return a name -> path map."""
    existing = {name: FIXTURES_DIR / name for name in FIXTURE_NAMES}
    if not all(path.exists() for path in existing.values()):
        return build_all(FIXTURES_DIR)
    return existing
