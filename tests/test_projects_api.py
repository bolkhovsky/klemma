"""Tests for projects API endpoints (ADR-009, #99)."""

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
        json={"email": "proj@example.com", "password": "secret123"},
    )
    return resp.json()["access_token"]


def _headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _add_source(client, token, citekey="smithML2020"):
    """Add a source to the library for section assignment."""
    client.post(
        "/library/sources",
        json={"citekey": citekey, "title": f"Paper {citekey}", "authors": "Test"},
        headers=_headers(token),
    )


# ---------------------------------------------------------------------------
# Coverage stats
# ---------------------------------------------------------------------------


def test_coverage_empty(client):
    token = _auth_token(client)
    resp = client.get("/projects/coverage", headers=_headers(token))
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_sources"] == 0
    assert data["sections"] == {}
    assert data["chapters"] == {}


def test_coverage_after_assignment(client):
    token = _auth_token(client)
    _add_source(client, token, "smithML2020")
    client.post(
        "/projects/sections/assign",
        json={"citekey": "smithML2020", "sections": ["2.1", "2.3"], "chapters": [2, 2]},
        headers=_headers(token),
    )
    resp = client.get("/projects/coverage", headers=_headers(token))
    data = resp.json()
    assert data["total_sources"] == 1
    assert data["sections"]["2.1"] == 1
    assert data["sections"]["2.3"] == 1


# ---------------------------------------------------------------------------
# Section sources
# ---------------------------------------------------------------------------


def test_section_sources_empty(client):
    token = _auth_token(client)
    resp = client.get("/projects/sections/1.1/sources", headers=_headers(token))
    assert resp.status_code == 200
    assert resp.json()["citekeys"] == []
    assert resp.json()["count"] == 0


def test_section_sources_after_assignment(client):
    token = _auth_token(client)
    _add_source(client, token)
    client.post(
        "/projects/sections/assign",
        json={"citekey": "smithML2020", "sections": ["1.3"], "chapters": [1]},
        headers=_headers(token),
    )
    resp = client.get("/projects/sections/1.3/sources", headers=_headers(token))
    data = resp.json()
    assert "smithML2020" in data["citekeys"]
    assert data["count"] == 1


# ---------------------------------------------------------------------------
# Assign sections
# ---------------------------------------------------------------------------


def test_assign_sections(client):
    token = _auth_token(client)
    _add_source(client, token)
    resp = client.post(
        "/projects/sections/assign",
        json={"citekey": "smithML2020", "sections": ["2.1"], "chapters": [2]},
        headers=_headers(token),
    )
    assert resp.status_code == 200
    assert resp.json()["sections"] == ["2.1"]


def test_assign_sections_source_not_in_library(client):
    token = _auth_token(client)
    resp = client.post(
        "/projects/sections/assign",
        json={"citekey": "nonexistent", "sections": ["1.1"], "chapters": [1]},
        headers=_headers(token),
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Source sections
# ---------------------------------------------------------------------------


def test_get_source_sections(client):
    token = _auth_token(client)
    _add_source(client, token)
    client.post(
        "/projects/sections/assign",
        json={"citekey": "smithML2020", "sections": ["1.1", "2.3"], "chapters": [1, 2]},
        headers=_headers(token),
    )
    resp = client.get("/projects/sources/smithML2020/sections", headers=_headers(token))
    assert resp.status_code == 200
    data = resp.json()
    assert "1.1" in data["sections"]
    assert "2.3" in data["sections"]


def test_get_source_sections_unassigned(client):
    token = _auth_token(client)
    resp = client.get("/projects/sources/nobody/sections", headers=_headers(token))
    assert resp.status_code == 200
    assert resp.json()["sections"] == []


# ---------------------------------------------------------------------------
# Auth required
# ---------------------------------------------------------------------------


def test_coverage_requires_auth(client):
    resp = client.get("/projects/coverage")
    assert resp.status_code == 403
