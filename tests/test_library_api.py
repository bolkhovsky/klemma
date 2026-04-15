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


# ---------------------------------------------------------------------------
# Metadata preview
# ---------------------------------------------------------------------------


def test_metadata_preview_returns_current_fields_and_null_doi(client, stores, tmp_path):
    """Preview for a source without a PDF file returns current fields + null DOI."""
    token = _register_and_get_token(client)
    _, paper_store, user_library, _, _ = stores

    # Add source directly (no PDF)
    paper_id = paper_store.register_paper(title="Sea Ice Paper", pdf_hash="abc123")
    paper_store.update_paper_metadata(paper_id, authors="Smith J", year=2021)
    user_id = client.get("/auth/me", headers=_auth_headers(token)).json()["user_id"]
    user_library.add_source(paper_id, "smith2021", status="completed", user_id=user_id)

    resp = client.get("/library/sources/smith2021/metadata-preview", headers=_auth_headers(token))
    assert resp.status_code == 200
    data = resp.json()
    assert data["current"]["title"] == "Sea Ice Paper"
    assert data["current"]["authors"] == "Smith J"
    assert data["current"]["year"] == 2021
    # No PDF file in store → suggested_doi should be null
    assert data["suggested_doi"] is None


def test_metadata_preview_ownership_404(client, stores):
    """Preview for a citekey that doesn't belong to the user returns 404."""
    token = _register_and_get_token(client)
    resp = client.get("/library/sources/nonexistent_citekey/metadata-preview", headers=_auth_headers(token))
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Enrich metadata
# ---------------------------------------------------------------------------


def test_enrich_metadata_by_doi(client, stores, monkeypatch):
    """Enrichment by DOI calls CrossRef DOI endpoint and updates the paper."""
    token = _register_and_get_token(client)
    _, paper_store, user_library, _, _ = stores

    paper_id = paper_store.register_paper(title="Old Title", pdf_hash="doi_hash_1")
    user_id = client.get("/auth/me", headers=_auth_headers(token)).json()["user_id"]
    user_library.add_source(paper_id, "smith2021doi", status="completed", user_id=user_id)

    mock_meta = {
        "title": "Enriched Title from CrossRef",
        "authors": "Smith J., Jones K.",
        "year": 2021,
        "doi": "10.1038/test",
        "abstract": "Abstract text from CrossRef.",
    }

    import klemma.literature.metadata as meta_mod
    monkeypatch.setattr(meta_mod, "lookup_crossref_by_doi", lambda *a, **kw: mock_meta)

    resp = client.post(
        "/library/sources/smith2021doi/enrich-metadata",
        json={"doi": "10.1038/test"},
        headers=_auth_headers(token),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["matched"] is True
    assert data["source"] == "doi"
    assert data["fields"]["title"] == "Enriched Title from CrossRef"
    assert data["fields"]["year"] == 2021


def test_enrich_metadata_matched_false_for_unknown_doi(client, stores, monkeypatch):
    """When DOI lookup returns None, matched=False and source='none'."""
    token = _register_and_get_token(client)
    _, paper_store, user_library, _, _ = stores

    paper_id = paper_store.register_paper(title="Unknown Paper", pdf_hash="doi_hash_2")
    user_id = client.get("/auth/me", headers=_auth_headers(token)).json()["user_id"]
    user_library.add_source(paper_id, "unknown2020", status="completed", user_id=user_id)

    import klemma.literature.metadata as meta_mod
    monkeypatch.setattr(meta_mod, "lookup_crossref_by_doi", lambda *a, **kw: None)

    resp = client.post(
        "/library/sources/unknown2020/enrich-metadata",
        json={"doi": "10.9999/nonexistent"},
        headers=_auth_headers(token),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["matched"] is False
    assert data["source"] == "none"


def test_enrich_metadata_abstract_override(client, stores, monkeypatch):
    """abstract_override is saved even when CrossRef returns no match."""
    token = _register_and_get_token(client)
    _, paper_store, user_library, _, _ = stores

    paper_id = paper_store.register_paper(title="Scan PDF", pdf_hash="doi_hash_3")
    user_id = client.get("/auth/me", headers=_auth_headers(token)).json()["user_id"]
    user_library.add_source(paper_id, "scanpdf2022", status="completed", user_id=user_id)

    import klemma.literature.metadata as meta_mod
    monkeypatch.setattr(meta_mod, "lookup_crossref_by_doi", lambda *a, **kw: None)

    resp = client.post(
        "/library/sources/scanpdf2022/enrich-metadata",
        json={"doi": "", "abstract_override": "Hand-typed abstract for scanned PDF."},
        headers=_auth_headers(token),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["fields"]["abstract"] == "Hand-typed abstract for scanned PDF."


def test_enrich_metadata_ownership_404(client):
    """Enriching a source that doesn't exist returns 404."""
    token = _register_and_get_token(client)
    resp = client.post(
        "/library/sources/ghost_citekey/enrich-metadata",
        json={"doi": "10.1234/x"},
        headers=_auth_headers(token),
    )
    assert resp.status_code == 404


def test_enrich_metadata_rate_limit_429(client, stores, monkeypatch):
    """11 consecutive requests trigger 429."""
    token = _register_and_get_token(client)
    _, paper_store, user_library, _, _ = stores

    paper_id = paper_store.register_paper(title="Rate Paper", pdf_hash="rate_hash")
    user_id = client.get("/auth/me", headers=_auth_headers(token)).json()["user_id"]
    user_library.add_source(paper_id, "ratepaper", status="completed", user_id=user_id)

    import klemma.literature.metadata as meta_mod
    monkeypatch.setattr(meta_mod, "lookup_crossref_by_doi", lambda *a, **kw: None)

    # Clear rate limiter state from previous test runs
    from klemma.api.routes.library import _enrich_rate_limit_store
    _enrich_rate_limit_store.clear()

    statuses = []
    for _ in range(12):
        r = client.post(
            "/library/sources/ratepaper/enrich-metadata",
            json={"doi": "10.1234/x"},
            headers=_auth_headers(token),
        )
        statuses.append(r.status_code)

    assert 429 in statuses, "Expected at least one 429 response"


def test_upload_no_longer_calls_resolve_metadata(client, stores, monkeypatch, tmp_path):
    """Critical: resolve_metadata must NOT be called from upload/process_source."""
    from unittest.mock import patch

    token = _register_and_get_token(client)

    resolve_calls = []

    def track_resolve(*args, **kwargs):
        resolve_calls.append(args)
        return {}

    with patch("klemma.literature.metadata.resolve_metadata", side_effect=track_resolve):
        resp = client.post(
            "/library/upload",
            files={"file": ("test_paper.pdf", _fake_pdf(), "application/pdf")},
            headers=_auth_headers(token),
        )

    assert resp.status_code == 201
    assert len(resolve_calls) == 0, (
        f"resolve_metadata was called {len(resolve_calls)} time(s) — it must not be "
        "called from the upload path after the lazy metadata enrichment refactor"
    )
