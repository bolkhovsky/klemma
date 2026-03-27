"""Tests for library API endpoints (ADR-009, #99)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from klemma.api.app import create_app
from klemma.api.auth.deps import set_user_store
from klemma.api.deps import set_file_store, set_paper_store, set_project_store, set_user_library
from klemma.api.rate_limit import reset_rate_limiter
from klemma.stores.file_store import LocalFileStore
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
    file_store = LocalFileStore(tmp_path / "files")
    return user_store, paper_store, user_library, project_store, file_store


@pytest.fixture
def client(stores) -> TestClient:
    user_store, paper_store, user_library, project_store, file_store = stores
    app = create_app()
    set_user_store(user_store)
    set_paper_store(paper_store)
    set_user_library(user_library)
    set_project_store(project_store)
    set_file_store(file_store)
    reset_rate_limiter()
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


# ---------------------------------------------------------------------------
# PDF Upload
# ---------------------------------------------------------------------------


def _fake_pdf(size: int = 2048) -> bytes:
    """Create minimal bytes that pass the size check."""
    return b"%PDF-1.4 " + b"x" * (size - 9)


def test_upload_pdf(client):
    token = _register_and_get_token(client)
    resp = client.post(
        "/library/upload",
        files={"file": ("smith2020_ml.pdf", _fake_pdf(), "application/pdf")},
        headers=_auth_headers(token),
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["citekey"]  # generated from filename
    assert data["paper_id"]
    assert data["pdf_hash"]
    assert data["status"] == "pending"
    assert data["deduplicated"] is False


def test_upload_dedup(client):
    token = _register_and_get_token(client)
    pdf = _fake_pdf()
    r1 = client.post(
        "/library/upload",
        files={"file": ("paper_a.pdf", pdf, "application/pdf")},
        headers=_auth_headers(token),
    )
    r2 = client.post(
        "/library/upload",
        files={"file": ("paper_b.pdf", pdf, "application/pdf")},
        headers=_auth_headers(token),
    )
    assert r1.status_code == 201
    assert r2.status_code == 201
    # Same PDF → same paper_id
    assert r1.json()["paper_id"] == r2.json()["paper_id"]
    assert r2.json()["deduplicated"] is True


def test_upload_dedup_same_user_preserves_citekey(client):
    """Re-uploading the same PDF by the same user returns the original citekey (issue #268)."""
    token = _register_and_get_token(client)
    pdf = _fake_pdf()
    r1 = client.post(
        "/library/upload",
        files={"file": ("smith2020.pdf", pdf, "application/pdf")},
        headers=_auth_headers(token),
    )
    r2 = client.post(
        "/library/upload",
        files={"file": ("smith2020.pdf", pdf, "application/pdf")},
        headers=_auth_headers(token),
    )
    assert r1.status_code == 201
    assert r2.status_code == 201
    # Same user, same PDF → citekey must not change
    assert r1.json()["citekey"] == r2.json()["citekey"], (
        "Re-uploading the same PDF should return the existing citekey, "
        "not generate a new one with hash suffix"
    )
    assert r2.json()["deduplicated"] is True


def test_upload_rejects_non_pdf(client):
    token = _register_and_get_token(client)
    resp = client.post(
        "/library/upload",
        files={"file": ("readme.txt", b"hello world", "text/plain")},
        headers=_auth_headers(token),
    )
    assert resp.status_code == 400


def test_upload_rejects_tiny_file(client):
    token = _register_and_get_token(client)
    resp = client.post(
        "/library/upload",
        files={"file": ("tiny.pdf", b"%PDF", "application/pdf")},
        headers=_auth_headers(token),
    )
    assert resp.status_code == 400


def test_upload_requires_auth(client):
    resp = client.post(
        "/library/upload",
        files={"file": ("test.pdf", _fake_pdf(), "application/pdf")},
    )
    assert resp.status_code == 403
