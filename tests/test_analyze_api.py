"""Tests for analyze API endpoints (ADR-009, #99)."""

from __future__ import annotations

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
        json={"email": "analyze@example.com", "password": "secret123"},
    )
    return resp.json()["access_token"]


def _headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------


def test_status_empty(client):
    token = _auth_token(client)
    resp = client.get("/analyze/status", headers=_headers(token))
    assert resp.status_code == 200
    data = resp.json()
    assert data["sources"]["total"] == 0
    assert data["sources"]["completed"] == 0
    assert data["sources"]["pending"] == 0
    assert data["coverage"] == []
    assert data["total_fragments"] == 0


def test_status_with_sources(client):
    token = _auth_token(client)
    # Add a source via library API
    client.post(
        "/library/sources",
        json={"citekey": "smithML2020", "title": "ML Paper", "authors": "Smith"},
        headers=_headers(token),
    )
    resp = client.get("/analyze/status", headers=_headers(token))
    data = resp.json()
    assert data["sources"]["total"] == 1
    assert data["sources"]["pending"] == 1  # default status


def test_status_with_coverage(client):
    token = _auth_token(client)
    # Add source + assign to section
    client.post(
        "/library/sources",
        json={"citekey": "jonesNLP2019", "title": "NLP Paper"},
        headers=_headers(token),
    )
    client.post(
        "/projects/sections/assign",
        json={"citekey": "jonesNLP2019", "sections": ["2.1"], "chapters": [2]},
        headers=_headers(token),
    )
    resp = client.get("/analyze/status", headers=_headers(token))
    data = resp.json()
    assert len(data["coverage"]) == 1
    assert data["coverage"][0]["section"] == "2.1"
    assert data["coverage"][0]["source_count"] == 1


def test_status_requires_auth(client):
    resp = client.get("/analyze/status")
    assert resp.status_code == 403
