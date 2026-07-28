"""End-to-end web-flow tests under FAKE_LLM (no API key, no tokens).

Environment is configured to a temp DB and temp storage *before* importing the app, so the
shared engine binds to the throwaway database.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

_TMP = Path(tempfile.mkdtemp(prefix="pitch-routes-"))
os.environ["FAKE_LLM"] = "1"
os.environ["DATABASE_URL"] = f"sqlite:///{(_TMP / 'app.db').as_posix()}"
os.environ["STORAGE_DIR"] = _TMP.as_posix()

from fastapi.testclient import TestClient  # noqa: E402

from app.main import create_app  # noqa: E402


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(create_app())


def _wait_for_job(client: TestClient, job_id: str, tries: int = 60) -> dict:
    status: dict = {}
    for _ in range(tries):
        response = client.get(f"/api/jobs/{job_id}")
        assert response.status_code == 200
        status = response.json()
        if status["status"] in ("done", "failed"):
            break
    return status


def test_healthz(client):
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["fake_llm"] is True


def test_index_serves_upload_form(client):
    response = client.get("/")
    assert response.status_code == 200
    assert 'hx-post="/api/decks"' in response.text


def test_upload_evaluate_and_download(client, fixture_decks):
    data = fixture_decks["good_deck.pdf"].read_bytes()
    response = client.post(
        "/api/decks",
        files={"file": ("Northwind Logistics.pdf", data, "application/pdf")},
    )
    assert response.status_code == 200
    job_id = response.headers["X-Job-Id"]
    deck_id = response.headers["X-Deck-Id"]
    assert "job-progress" in response.text  # the progress partial was returned

    status = _wait_for_job(client, job_id)
    assert status["status"] == "done", status

    # Report page renders with the score and band.
    report = client.get(f"/report/{deck_id}")
    assert report.status_code == 200
    assert "out of 100" in report.text
    assert "Download the full PDF report" in report.text

    # Download endpoint: attachment with a clean filename, real PDF bytes.
    pdf = client.get(f"/api/reports/{deck_id}.pdf")
    assert pdf.status_code == 200
    assert pdf.headers["content-type"] == "application/pdf"
    disposition = pdf.headers["content-disposition"]
    assert "attachment" in disposition
    # Spaces are RFC 5987 percent-encoded in the filename* form.
    assert "Northwind%20Logistics-evaluation.pdf" in disposition
    assert pdf.content[:5] == b"%PDF-"

    # Inline variant for the preview iframe.
    inline = client.get(f"/api/reports/{deck_id}.pdf?inline=1")
    assert inline.status_code == 200
    assert "inline" in inline.headers["content-disposition"]


def test_status_partial_redirects_when_done(client, fixture_decks):
    data = fixture_decks["good_deck.pdf"].read_bytes()
    response = client.post(
        "/api/decks", files={"file": ("weak.pdf", data, "application/pdf")}
    )
    job_id = response.headers["X-Job-Id"]
    deck_id = response.headers["X-Deck-Id"]
    _wait_for_job(client, job_id)

    # The HTMX poll target returns an HX-Redirect to the report page once done.
    partial = client.get(f"/status/{job_id}")
    assert partial.status_code == 200
    assert partial.headers.get("HX-Redirect") == f"/report/{deck_id}"


def test_rejects_non_pdf_upload(client):
    response = client.post(
        "/api/decks",
        files={"file": ("fake.pdf", b"this is not a pdf", "application/pdf")},
    )
    assert response.status_code == 200  # HTMX swap-in, not an HTTP error
    assert "look like a PDF" in response.text
    assert "X-Job-Id" not in response.headers  # no job was created


def test_rejects_oversized_upload(client, fixture_decks, monkeypatch):
    # Shrink the cap on the live settings so a small deck trips the size guard.
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "max_upload_mb", 0)
    data = fixture_decks["good_deck.pdf"].read_bytes()
    response = client.post(
        "/api/decks", files={"file": ("big.pdf", data, "application/pdf")}
    )
    assert response.status_code == 200
    assert "limit is" in response.text
    assert "X-Job-Id" not in response.headers


def test_unknown_job_returns_404(client):
    assert client.get("/api/jobs/does-not-exist").status_code == 404


def test_unknown_report_returns_404(client):
    assert client.get("/report/nope").status_code == 404
    assert client.get("/api/reports/nope.pdf").status_code == 404
