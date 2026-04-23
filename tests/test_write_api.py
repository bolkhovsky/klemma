"""Tests for write API endpoints (ADR-009, #99)."""

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
        json={"email": "write@example.com", "password": "secret123"},
    )
    return resp.json()["access_token"]


def _headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# Research
# ---------------------------------------------------------------------------


def test_submit_research_job(client, monkeypatch):
    token = _auth_token(client)

    mock_job = MagicMock()
    mock_job.id = "research-job-1"
    mock_queue = MagicMock()
    mock_queue.enqueue.return_value = mock_job

    import klemma.api.routes.write as write_mod

    monkeypatch.setattr(write_mod, "_RQ_AVAILABLE", True)
    monkeypatch.setattr(write_mod, "Redis", MagicMock())
    monkeypatch.setattr(write_mod, "Queue", MagicMock(return_value=mock_queue))

    resp = client.post(
        "/write/research",
        json={"section": "2.3"},
        headers=_headers(token),
    )
    assert resp.status_code == 202
    data = resp.json()
    assert data["job_id"] == "research-job-1"
    assert data["section"] == "2.3"
    assert data["task_type"] == "generate_research"


# ---------------------------------------------------------------------------
# Draft
# ---------------------------------------------------------------------------


def test_submit_draft_job(client, monkeypatch):
    token = _auth_token(client)

    mock_job = MagicMock()
    mock_job.id = "draft-job-1"
    mock_queue = MagicMock()
    mock_queue.enqueue.return_value = mock_job

    import klemma.api.routes.write as write_mod

    monkeypatch.setattr(write_mod, "_RQ_AVAILABLE", True)
    monkeypatch.setattr(write_mod, "Redis", MagicMock())
    monkeypatch.setattr(write_mod, "Queue", MagicMock(return_value=mock_queue))

    resp = client.post(
        "/write/draft",
        json={"section": "1.3.2"},
        headers=_headers(token),
    )
    assert resp.status_code == 202
    data = resp.json()
    assert data["job_id"] == "draft-job-1"
    assert data["section"] == "1.3.2"
    assert data["task_type"] == "generate_draft"


# ---------------------------------------------------------------------------
# Auth required
# ---------------------------------------------------------------------------


def test_research_requires_auth(client):
    resp = client.post("/write/research", json={"section": "1.1"})
    assert resp.status_code == 401


def test_draft_requires_auth(client):
    resp = client.post("/write/draft", json={"section": "1.1"})
    assert resp.status_code == 401
