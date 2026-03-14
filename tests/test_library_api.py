"""Tests for library API endpoints (ADR-009, #99)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from klemma.api.app import create_app
from klemma.api.auth.deps import set_user_store
from klemma.api.deps import set_paper_store, set_user_library
from klemma.stores.paper_store import LocalPaperStore
from klemma.stores.user_library import LocalUserLibrary
from klemma.stores.user_store import LocalUserStore


@pytest.fixture
def stores(tmp_path):
    user_store = LocalUserStore(tmp_path / "users.db")
    library_db = tmp_path / "library.db"
    paper_store = LocalPaperStore(library_db)
    user_library = LocalUserLibrary(library_db)
    return user_store, paper_store, user_library


@pytest.fixture
def client(stores) -> TestClient:
    user_store, paper_store, user_library = stores
    app = create_app()
    set_user_store(user_store)
    set_paper_store(paper_store)
    set_user_library(user_library)
    return TestClient(app)


def _register_and_get_token(client: TestClient) -> str:
    resp = client.post(
        "/auth/register",
        json={"email": "lib@example.com", "password": "secret123"},
    )
    return resp.json()["access_token"]


def _auth_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# List sources
# ---------------------------------------------------------------------------


def test_list_sources_empty(client):
    token = _register_and_get_token(client)
    resp = client.get("/library/sources", headers=_auth_headers(token))
    assert resp.status_code == 200
    data = resp.json()
    assert data["sources"] == []
    assert data["total"] == 0


def test_list_sources_requires_auth(client):
    resp = client.get("/library/sources")
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Add source
# ---------------------------------------------------------------------------


def test_add_source(client):
    token = _register_and_get_token(client)
    resp = client.post(
        "/library/sources",
        json={
            "citekey": "smithML2020",
            "title": "Machine Learning for NLP",
            "authors": "John Smith",
            "year": 2020,
        },
        headers=_auth_headers(token),
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["citekey"] == "smithML2020"
    assert data["status"] == "pending"
    assert data["title"] == "Machine Learning for NLP"
    assert data["paper_id"]  # non-empty UUID


def test_add_source_duplicate_citekey(client):
    token = _register_and_get_token(client)
    client.post(
        "/library/sources",
        json={"citekey": "smithML2020", "title": "Paper A"},
        headers=_auth_headers(token),
    )
    resp = client.post(
        "/library/sources",
        json={"citekey": "smithML2020", "title": "Paper B"},
        headers=_auth_headers(token),
    )
    assert resp.status_code == 409


def test_add_source_dedup_by_doi(client):
    token = _register_and_get_token(client)
    # First source with DOI
    r1 = client.post(
        "/library/sources",
        json={"citekey": "smith2020a", "title": "Paper", "doi": "10.1234/test"},
        headers=_auth_headers(token),
    )
    # Second source with same DOI but different citekey
    r2 = client.post(
        "/library/sources",
        json={"citekey": "smith2020b", "title": "Same Paper", "doi": "10.1234/test"},
        headers=_auth_headers(token),
    )
    assert r1.status_code == 201
    assert r2.status_code == 201
    # Both should point to the same paper_id (dedup by DOI)
    assert r1.json()["paper_id"] == r2.json()["paper_id"]


# ---------------------------------------------------------------------------
# Get source
# ---------------------------------------------------------------------------


def test_get_source(client):
    token = _register_and_get_token(client)
    client.post(
        "/library/sources",
        json={"citekey": "jonesNLP2019", "title": "NLP Advances", "authors": "Jones"},
        headers=_auth_headers(token),
    )
    resp = client.get("/library/sources/jonesNLP2019", headers=_auth_headers(token))
    assert resp.status_code == 200
    data = resp.json()
    assert data["citekey"] == "jonesNLP2019"
    assert "fragments" in data


def test_get_source_not_found(client):
    token = _register_and_get_token(client)
    resp = client.get("/library/sources/nonexistent", headers=_auth_headers(token))
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Delete source
# ---------------------------------------------------------------------------


def test_delete_source(client):
    token = _register_and_get_token(client)
    client.post(
        "/library/sources",
        json={"citekey": "toDelete", "title": "Delete Me"},
        headers=_auth_headers(token),
    )
    resp = client.delete("/library/sources/toDelete", headers=_auth_headers(token))
    assert resp.status_code == 204

    # Verify it's gone
    resp = client.get("/library/sources/toDelete", headers=_auth_headers(token))
    assert resp.status_code == 404


def test_delete_source_not_found(client):
    token = _register_and_get_token(client)
    resp = client.delete("/library/sources/nonexistent", headers=_auth_headers(token))
    assert resp.status_code == 404
