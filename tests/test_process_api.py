"""Tests for process API endpoints (ADR-009, #186)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from klemma.api.app import create_app
from klemma.api.auth.deps import set_user_store
from klemma.api.deps import set_paper_store, set_project_store, set_user_library
from klemma.api.rate_limit import reset_rate_limiter
from klemma.stores.paper_store import LocalPaperStore
from klemma.stores.project_store import LocalProjectStore
from klemma.stores.user_library import LocalUserLibrary
from klemma.stores.user_store import LocalUserStore


@pytest.fixture
def stores(tmp_path):
    user_store = LocalUserStore(tmp_path / "users.db")
    library_db = tmp_path / "library.db"
    paper_store = LocalPaperStore(library_db)
    user_library = LocalUserLibrary(library_db)
    project_store = LocalProjectStore(tmp_path / "project.db")
    return user_store, paper_store, user_library, project_store


@pytest.fixture
def client(stores) -> TestClient:
    user_store, paper_store, user_library, project_store = stores
    app = create_app()
    set_user_store(user_store)
    set_paper_store(paper_store)
    set_user_library(user_library)
    set_project_store(project_store)
    reset_rate_limiter()
    return TestClient(app)


def _auth_token(client: TestClient) -> str:
    resp = client.post(
        "/auth/register",
        json={"email": "proc@example.com", "password": "secret123"},
    )
    return resp.json()["access_token"]


def _headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _add_source(client, token, citekey="smithML2020"):
    client.post(
        "/library/sources",
        json={"citekey": citekey, "title": f"Paper {citekey}"},
        headers=_headers(token),
    )


# ---------------------------------------------------------------------------
# Submit process job
# ---------------------------------------------------------------------------


def test_submit_process_source_not_found(client):
    token = _auth_token(client)
    resp = client.post("/process/sources/nonexistent", headers=_headers(token))
    assert resp.status_code == 404


def test_submit_process_enqueues_job(client, monkeypatch):
    token = _auth_token(client)
    _add_source(client, token)

    mock_job = MagicMock()
    mock_job.id = "test-job-123"
    mock_queue_instance = MagicMock()
    mock_queue_instance.enqueue.return_value = mock_job

    # Patch the module-level imports in process.py
    import klemma.api.routes.process as proc_mod

    monkeypatch.setattr(proc_mod, "_RQ_AVAILABLE", True)
    monkeypatch.setattr(proc_mod, "Redis", MagicMock())
    monkeypatch.setattr(proc_mod, "Queue", MagicMock(return_value=mock_queue_instance))

    resp = client.post("/process/sources/smithML2020", headers=_headers(token))

    assert resp.status_code == 202
    data = resp.json()
    assert data["job_id"] == "test-job-123"
    assert data["status"] == "queued"
    assert data["citekey"] == "smithML2020"


# ---------------------------------------------------------------------------
# Job status
# ---------------------------------------------------------------------------


def test_job_status_finished(client, monkeypatch):
    token = _auth_token(client)

    mock_job = MagicMock()
    mock_job.get_status.return_value = "finished"
    mock_job.is_finished = True
    mock_job.result = {"status": "ok", "fragment_count": 5}

    import klemma.api.routes.process as proc_mod

    monkeypatch.setattr(proc_mod, "_RQ_AVAILABLE", True)
    monkeypatch.setattr(proc_mod, "Redis", MagicMock())
    mock_job_cls = MagicMock()
    mock_job_cls.fetch.return_value = mock_job
    monkeypatch.setattr(proc_mod, "Job", mock_job_cls)

    resp = client.get("/process/jobs/test-123", headers=_headers(token))

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "finished"
    assert data["result"]["fragment_count"] == 5


def test_job_status_not_found(client, monkeypatch):
    token = _auth_token(client)

    import klemma.api.routes.process as proc_mod

    monkeypatch.setattr(proc_mod, "_RQ_AVAILABLE", True)
    monkeypatch.setattr(proc_mod, "Redis", MagicMock())
    mock_job_cls = MagicMock()
    mock_job_cls.fetch.side_effect = Exception("No such job")
    monkeypatch.setattr(proc_mod, "Job", mock_job_cls)

    resp = client.get("/process/jobs/nonexistent", headers=_headers(token))
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Auth required
# ---------------------------------------------------------------------------


def test_process_requires_auth(client):
    resp = client.post("/process/sources/anything")
    assert resp.status_code == 403


def test_job_status_requires_auth(client):
    resp = client.get("/process/jobs/anything")
    assert resp.status_code == 403
