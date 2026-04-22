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


def test_upload_citekey_collision_uses_bbt_suffix(client):
    """Same-author/same-year uploads from different PDFs must get a/b/c
    suffixes (BBT-compatible), not `_{hash[:6]}`.

    Example: two different Smith-2023 papers upload in sequence:
        1st → smith2023
        2nd → smith2023a
        3rd → smith2023b
    """
    token = _register_and_get_token(client)
    r1 = client.post(
        "/library/upload",
        files={"file": ("Smith - 2023 - Paper One.pdf", _fake_pdf(2048), "application/pdf")},
        headers=_auth_headers(token),
    )
    r2 = client.post(
        "/library/upload",
        files={"file": ("Smith - 2023 - Paper Two.pdf", _fake_pdf(3072), "application/pdf")},
        headers=_auth_headers(token),
    )
    r3 = client.post(
        "/library/upload",
        files={"file": ("Smith - 2023 - Paper Three.pdf", _fake_pdf(4096), "application/pdf")},
        headers=_auth_headers(token),
    )
    assert r1.status_code == 201
    assert r2.status_code == 201
    assert r3.status_code == 201
    assert r1.json()["citekey"] == "smith2023"
    assert r2.json()["citekey"] == "smith2023a"
    assert r3.json()["citekey"] == "smith2023b"
    # All three are distinct papers
    assert r1.json()["paper_id"] != r2.json()["paper_id"] != r3.json()["paper_id"]


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


# ---------------------------------------------------------------------------
# Reference gaps — scoring formula integration
# ---------------------------------------------------------------------------


def _setup_gaps_scenario(client, stores, *, gap_refs_by_paper: dict):
    """Seed a gaps scenario.

    gap_refs_by_paper: {paper_seed_title: [ref_dict, ...]}
    Each paper is registered in paper_store + user_library + user_sources.
    Returns user_id.
    """
    user_store, paper_store, user_library, project_store, file_store = stores
    token = _register_and_get_token(client)
    user_id = client.get("/auth/me", headers=_auth_headers(token)).json()["user_id"]

    for seed_title, refs in gap_refs_by_paper.items():
        pid = paper_store.register_paper(title=seed_title, pdf_hash=f"hash_{seed_title[:8]}")
        citekey = seed_title.replace(" ", "_")[:20].lower()
        user_library.add_source(pid, citekey, status="completed", user_id=user_id)
        paper_store.save_citation_links(pid, refs)

    return token, user_id


def test_gaps_requires_min_3_sources(client):
    """Fewer than 3 sources → empty gaps with detail message."""
    token = _register_and_get_token(client)
    resp = client.get("/library/gaps", headers=_auth_headers(token))
    assert resp.status_code == 200
    data = resp.json()
    assert data["gaps"] == []
    assert data["detail"]  # non-empty hint message


def test_gaps_scored_by_intent(client, stores):
    """Same citation count, different intents: method gap ranks above background gap."""
    user_store, paper_store, user_library, _, _ = stores
    token = _register_and_get_token(client)
    user_id = client.get("/auth/me", headers=_auth_headers(token)).json()["user_id"]

    # Three user papers: p1..p3 each cite both gaps once
    for i in range(1, 4):
        pid = paper_store.register_paper(title=f"User Paper {i}", pdf_hash=f"uhash{i}")
        citekey = f"user_paper_{i}"
        user_library.add_source(pid, citekey, status="completed", user_id=user_id)
        paper_store.save_citation_links(pid, [
            {"title": "Gap Method Paper", "authors": "A", "year": 2020, "citation_intent": "method"},
            {"title": "Gap Background Paper", "authors": "B", "year": 2020, "citation_intent": "background"},
        ])

    resp = client.get("/library/gaps", headers=_auth_headers(token))
    assert resp.status_code == 200
    gaps = resp.json()["gaps"]
    assert len(gaps) >= 2

    method_gap = next((g for g in gaps if "Method" in g["title"]), None)
    bg_gap = next((g for g in gaps if "Background" in g["title"]), None)
    assert method_gap is not None
    assert bg_gap is not None
    assert method_gap["score"] > bg_gap["score"]
    assert method_gap["intent_weight"] > bg_gap["intent_weight"]


def test_gaps_quality_multiplier(client, stores):
    """avg_quality multiplies the score: higher quality source → higher gap score."""
    user_store, paper_store, user_library, _, _ = stores
    token = _register_and_get_token(client)
    user_id = client.get("/auth/me", headers=_auth_headers(token)).json()["user_id"]

    # Two groups of 3 papers; each group cites its own gap
    for i, (quality, gap_title) in enumerate([
        (5, "High Quality Gap"),
        (1, "Low Quality Gap"),
        (5, "High Quality Gap"),  # second paper same gap
        (1, "Low Quality Gap"),
        (5, "High Quality Gap"),  # third paper
        (1, "Low Quality Gap"),
    ]):
        pid = paper_store.register_paper(title=f"QualityPaper {i}", pdf_hash=f"qhash{i}")
        citekey = f"quality_paper_{i}"
        user_library.add_source(pid, citekey, status="completed", quality_score=quality, user_id=user_id)
        paper_store.save_citation_links(pid, [
            {"title": gap_title, "authors": "X", "year": 2021}
        ])

    resp = client.get("/library/gaps", headers=_auth_headers(token))
    assert resp.status_code == 200
    gaps = resp.json()["gaps"]

    high_q_gap = next(g for g in gaps if g["title"] == "High Quality Gap")
    low_q_gap = next(g for g in gaps if g["title"] == "Low Quality Gap")
    assert high_q_gap["score"] > low_q_gap["score"]
    assert high_q_gap["avg_quality"] > low_q_gap["avg_quality"]


def test_gaps_sections_served_populated(client, stores):
    """sections_served is populated from citing-paper section assignments."""
    user_store, paper_store, user_library, project_store, _ = stores
    token = _register_and_get_token(client)
    user_id = client.get("/auth/me", headers=_auth_headers(token)).json()["user_id"]

    paper_ids = []
    for i in range(1, 4):
        pid = paper_store.register_paper(title=f"SectionPaper {i}", pdf_hash=f"sphash{i}")
        citekey = f"section_paper_{i}"
        user_library.add_source(pid, citekey, status="completed", user_id=user_id)
        paper_store.save_citation_links(pid, [
            {"title": "Gap With Sections", "authors": "G", "year": 2022}
        ])
        paper_ids.append((pid, citekey))

    # Assign p1 and p2 to section "1.1", p3 to "2.1"
    project_store.set_source_sections(paper_ids[0][1], paper_ids[0][0], ["1.1"], [], user_id=user_id)
    project_store.set_source_sections(paper_ids[1][1], paper_ids[1][0], ["1.1"], [], user_id=user_id)
    project_store.set_source_sections(paper_ids[2][1], paper_ids[2][0], ["2.1"], [], user_id=user_id)

    resp = client.get("/library/gaps", headers=_auth_headers(token))
    assert resp.status_code == 200
    gaps = resp.json()["gaps"]
    gap = next(g for g in gaps if g["title"] == "Gap With Sections")

    sections_map = {s["section"]: s["count"] for s in gap["sections_served"]}
    assert sections_map.get("1.1", 0) == 2
    assert sections_map.get("2.1", 0) == 1


def test_gaps_legacy_background_neutralized(client, stores):
    """Papers cited with background intent get weight=1.0 (neutral, not penalized)."""
    user_store, paper_store, user_library, _, _ = stores
    token = _register_and_get_token(client)
    user_id = client.get("/auth/me", headers=_auth_headers(token)).json()["user_id"]

    for i in range(1, 4):
        pid = paper_store.register_paper(title=f"BgPaper {i}", pdf_hash=f"bghash{i}")
        citekey = f"bg_paper_{i}"
        user_library.add_source(pid, citekey, status="completed", user_id=user_id)
        paper_store.save_citation_links(pid, [
            {"title": "Background Only Gap", "authors": "Legacy", "year": 2019, "citation_intent": "background"}
        ])

    resp = client.get("/library/gaps", headers=_auth_headers(token))
    assert resp.status_code == 200
    gaps = resp.json()["gaps"]
    gap = next((g for g in gaps if g["title"] == "Background Only Gap"), None)
    assert gap is not None
    assert gap["score"] > 0
    assert abs(gap["intent_weight"] - 1.0) < 0.01  # neutral weight


# ---------------------------------------------------------------------------
# BBT import
# ---------------------------------------------------------------------------


def _make_bbt(items: list) -> bytes:
    import json as _json
    return _json.dumps({"items": items}).encode("utf-8")


def test_import_bbt_doi_match(client, stores):
    """DOI-exact match: external_citekey is set on the library row."""
    token = _register_and_get_token(client)
    _, paper_store, user_library, _, _ = stores
    user_id = client.get("/auth/me", headers=_auth_headers(token)).json()["user_id"]

    pid = paper_store.register_paper(
        title="Some Paper", pdf_hash="hash1", doi="10.1234/abc"
    )
    user_library.add_source(pid, "ugly_ck", status="completed", user_id=user_id)

    bbt = _make_bbt([
        {"itemType": "journalArticle", "citationKey": "smith2020",
         "title": "Some Paper", "DOI": "10.1234/abc",
         "creators": [{"creatorType": "author", "lastName": "Smith"}],
         "date": "2020"},
    ])
    resp = client.post(
        "/library/import-bbt",
        files={"file": ("refs.json", bbt, "application/json")},
        headers=_auth_headers(token),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["matched"]) == 1
    assert data["matched"][0] == {
        "citekey": "ugly_ck",
        "external_citekey": "smith2020",
        "strategy": "doi",
        "title": "Some Paper",
    }
    assert data["unmatched"] == []
    assert data["ambiguous"] == []
    # Persisted
    src = user_library.get_source_by_citekey("ugly_ck", user_id=user_id)
    assert src.external_citekey == "smith2020"


def test_import_bbt_fuzzy_match(client, stores):
    """No DOI — match by title prefix + author + year."""
    token = _register_and_get_token(client)
    _, paper_store, user_library, _, _ = stores
    user_id = client.get("/auth/me", headers=_auth_headers(token)).json()["user_id"]

    pid = paper_store.register_paper(title="Arctic Sea Ice Forecasting Methods", pdf_hash="h2")
    paper_store.update_paper_metadata(pid, authors="Andersson K", year=2021)
    user_library.add_source(pid, "andersson2021", status="completed", user_id=user_id)

    bbt = _make_bbt([
        {"itemType": "journalArticle", "citationKey": "ak2021",
         "title": "Arctic Sea Ice Forecasting Methods",
         "creators": [{"creatorType": "author", "lastName": "Andersson"}],
         "date": "2021"},
    ])
    resp = client.post(
        "/library/import-bbt",
        files={"file": ("refs.json", bbt, "application/json")},
        headers=_auth_headers(token),
    )
    data = resp.json()
    assert len(data["matched"]) == 1
    assert data["matched"][0]["external_citekey"] == "ak2021"
    assert data["matched"][0]["strategy"] == "fuzzy"


def test_import_bbt_fuzzy_cyrillic_match(client, stores):
    """Russian author + title fuzzy match."""
    token = _register_and_get_token(client)
    _, paper_store, user_library, _, _ = stores
    user_id = client.get("/auth/me", headers=_auth_headers(token)).json()["user_id"]

    pid = paper_store.register_paper(title="Основные направления СМП", pdf_hash="h3")
    paper_store.update_paper_metadata(pid, authors="Воронина", year=2023)
    user_library.add_source(pid, "воронина2023_ugly", status="completed", user_id=user_id)

    bbt = _make_bbt([
        {"itemType": "journalArticle", "citationKey": "voronina2023",
         "title": "Основные направления СМП",
         "creators": [{"creatorType": "author", "lastName": "Воронина"}],
         "date": "2023"},
    ])
    resp = client.post(
        "/library/import-bbt",
        files={"file": ("refs.json", bbt, "application/json")},
        headers=_auth_headers(token),
    )
    data = resp.json()
    assert len(data["matched"]) == 1
    assert data["matched"][0]["external_citekey"] == "voronina2023"


def test_import_bbt_unmatched(client, stores):
    """BBT entry with no corresponding library source → unmatched list."""
    token = _register_and_get_token(client)

    bbt = _make_bbt([
        {"itemType": "journalArticle", "citationKey": "nonexistent2020",
         "title": "Not in library", "DOI": "10.99/x",
         "creators": [{"creatorType": "author", "lastName": "Stranger"}],
         "date": "2020"},
    ])
    resp = client.post(
        "/library/import-bbt",
        files={"file": ("refs.json", bbt, "application/json")},
        headers=_auth_headers(token),
    )
    data = resp.json()
    assert data["matched"] == []
    assert len(data["unmatched"]) == 1
    assert data["unmatched"][0]["bbt_citekey"] == "nonexistent2020"
    assert data["unmatched"][0]["doi"] == "10.99/x"
    assert data["ambiguous"] == []


def test_import_bbt_ambiguous(client, stores):
    """Two library sources match the same fuzzy predicate → ambiguous, no external_citekey set."""
    token = _register_and_get_token(client)
    _, paper_store, user_library, _, _ = stores
    user_id = client.get("/auth/me", headers=_auth_headers(token)).json()["user_id"]

    # Two different papers with same title-prefix + author + year
    for i, pdf_hash in enumerate(["h_a", "h_b"]):
        pid = paper_store.register_paper(title="Same Title Here", pdf_hash=pdf_hash)
        paper_store.update_paper_metadata(pid, authors="Smith", year=2023)
        user_library.add_source(pid, f"smith2023_{i}", status="completed", user_id=user_id)

    bbt = _make_bbt([
        {"itemType": "journalArticle", "citationKey": "smith2023",
         "title": "Same Title Here",
         "creators": [{"creatorType": "author", "lastName": "Smith"}],
         "date": "2023"},
    ])
    resp = client.post(
        "/library/import-bbt",
        files={"file": ("refs.json", bbt, "application/json")},
        headers=_auth_headers(token),
    )
    data = resp.json()
    assert data["matched"] == []
    assert len(data["ambiguous"]) == 1
    amb = data["ambiguous"][0]
    assert amb["bbt_citekey"] == "smith2023"
    assert set(amb["candidates"]) == {"smith2023_0", "smith2023_1"}
    # Neither source got external_citekey set
    for i in range(2):
        src = user_library.get_source_by_citekey(f"smith2023_{i}", user_id=user_id)
        assert src.external_citekey is None


def test_import_bbt_idempotent(client, stores):
    """Re-running the import produces the same result."""
    token = _register_and_get_token(client)
    _, paper_store, user_library, _, _ = stores
    user_id = client.get("/auth/me", headers=_auth_headers(token)).json()["user_id"]

    pid = paper_store.register_paper(title="X", pdf_hash="h", doi="10.1/x")
    user_library.add_source(pid, "ck", status="completed", user_id=user_id)

    bbt = _make_bbt([
        {"itemType": "journalArticle", "citationKey": "new_ck",
         "title": "X", "DOI": "10.1/x",
         "creators": [{"creatorType": "author", "lastName": "Y"}],
         "date": "2020"},
    ])
    r1 = client.post(
        "/library/import-bbt",
        files={"file": ("refs.json", bbt, "application/json")},
        headers=_auth_headers(token),
    )
    r2 = client.post(
        "/library/import-bbt",
        files={"file": ("refs.json", bbt, "application/json")},
        headers=_auth_headers(token),
    )
    assert r1.json() == r2.json()
    src = user_library.get_source_by_citekey("ck", user_id=user_id)
    assert src.external_citekey == "new_ck"


def test_import_bbt_rejects_non_json_extension(client):
    token = _register_and_get_token(client)
    resp = client.post(
        "/library/import-bbt",
        files={"file": ("refs.bib", b"@article{x,title={Y}}", "text/plain")},
        headers=_auth_headers(token),
    )
    assert resp.status_code == 400


def test_import_bbt_rejects_malformed_json(client):
    token = _register_and_get_token(client)
    resp = client.post(
        "/library/import-bbt",
        files={"file": ("refs.json", b"not json", "application/json")},
        headers=_auth_headers(token),
    )
    assert resp.status_code == 400


def test_import_bbt_requires_auth(client):
    resp = client.post(
        "/library/import-bbt",
        files={"file": ("refs.json", b'{"items":[]}', "application/json")},
    )
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Gap recency filter (existing)
# ---------------------------------------------------------------------------


def test_gaps_recency_filter(client, stores):
    """Papers older than 10 years with cited_by_count<3 are filtered out."""
    user_store, paper_store, user_library, _, _ = stores
    token = _register_and_get_token(client)
    user_id = client.get("/auth/me", headers=_auth_headers(token)).json()["user_id"]

    from datetime import date
    old_year = date.today().year - 15  # 15 years old → filtered if count < 3
    recent_year = date.today().year - 2  # recent → kept

    for i in range(1, 4):
        pid = paper_store.register_paper(title=f"FilterPaper {i}", pdf_hash=f"fhash{i}")
        citekey = f"filter_paper_{i}"
        user_library.add_source(pid, citekey, status="completed", user_id=user_id)
        paper_store.save_citation_links(pid, [
            {"title": "Old Obscure Gap", "authors": "O", "year": old_year},
            {"title": "Recent Gap", "authors": "R", "year": recent_year},
        ])

    resp = client.get("/library/gaps", headers=_auth_headers(token))
    assert resp.status_code == 200
    gaps = resp.json()["gaps"]
    titles = [g["title"] for g in gaps]
    # Old gap with only 3 citations: count=3 ≥ classic_min_cited_by → KEPT
    # (the threshold is <3; 3 is exactly the minimum to be kept as a "classic")
    assert "Recent Gap" in titles
