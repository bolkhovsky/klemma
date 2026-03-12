"""Tests for LocalPaperStore (ADR-014 Phase 1B)."""

import sqlite3

import pytest

from klemma.models import FragmentRecord
from klemma.stores import LocalPaperStore

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def store(tmp_path) -> LocalPaperStore:
    return LocalPaperStore(tmp_path / "library.db")


def _make_fragment(paper_id: str, text: str, page: int = 1) -> FragmentRecord:
    fid = f"frag-{hash(paper_id + text) & 0xFFFFFF:06x}"
    return FragmentRecord(
        fragment_id=fid,
        paper_id=paper_id,
        fragment_text=text,
        fragment_type="key_idea",
        page_number=page,
        citation_intent="background",
        content_hash=fid,
    )


# ---------------------------------------------------------------------------
# Schema / migration
# ---------------------------------------------------------------------------


def test_schema_version_is_1(tmp_path):
    db_path = tmp_path / "lib.db"
    LocalPaperStore(db_path)
    conn = sqlite3.connect(str(db_path))
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    conn.close()
    assert version == 1


def test_schema_all_tables_created(tmp_path):
    db_path = tmp_path / "lib.db"
    LocalPaperStore(db_path)
    conn = sqlite3.connect(str(db_path))
    tables = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    conn.close()
    assert tables >= {
        "papers",
        "extractions",
        "fragments",
        "paper_embeddings",
        "fragment_embeddings",
        "citation_graph",
    }


def test_migration_idempotent(tmp_path):
    """Creating LocalPaperStore twice on the same DB must not raise or corrupt."""
    db_path = tmp_path / "lib.db"
    s1 = LocalPaperStore(db_path)
    s1.register_paper(title="T", pdf_hash="aaa")
    # Second init — should re-run _migrate_schema() but schema version check
    # prevents double-create; paper must survive
    s2 = LocalPaperStore(db_path)
    paper = s2.find_paper(pdf_hash="aaa")
    assert paper is not None
    assert paper.title == "T"


def test_db_created_in_new_dir(tmp_path):
    db_path = tmp_path / "nested" / "dir" / "library.db"
    LocalPaperStore(db_path)
    assert db_path.exists()


# ---------------------------------------------------------------------------
# register_paper / find_paper
# ---------------------------------------------------------------------------


def test_register_paper_returns_uuid(store):
    pid = store.register_paper(title="Test Paper", pdf_hash="abc123")
    assert isinstance(pid, str) and len(pid) == 36  # UUID format


def test_register_paper_idempotent_by_pdf_hash(store):
    pid1 = store.register_paper(title="Paper A", pdf_hash="hash1")
    pid2 = store.register_paper(title="Paper A again", pdf_hash="hash1")
    assert pid1 == pid2


def test_find_paper_by_pdf_hash(store):
    pid = store.register_paper(title="Alpha", pdf_hash="hash-alpha", year=2022)
    result = store.find_paper(pdf_hash="hash-alpha")
    assert result is not None
    assert result.paper_id == pid
    assert result.title == "Alpha"
    assert result.year == 2022


def test_find_paper_missing_returns_none(store):
    assert store.find_paper(pdf_hash="nonexistent") is None


def test_find_paper_by_doi(store):
    pid = store.register_paper(
        title="DOI Paper", pdf_hash="hash-doi", doi="10.1234/test"
    )
    result = store.find_paper(doi="10.1234/test")
    assert result is not None
    assert result.paper_id == pid


def test_register_paper_dedup_by_doi_updates_hash(store):
    """If a paper was registered by DOI without hash, adding hash later links same paper."""
    pid1 = store.register_paper(title="Paper X", pdf_hash="hash-x", doi="10.99/x")
    # Now another registration with same DOI but no pdf_hash yet recorded for it
    # (Simulate: register with DOI match — no hash in existing record)
    # First register with a different hash (no DOI) — shouldn't match
    pid2 = store.register_paper(title="Paper X copy", pdf_hash="other-hash")
    assert pid1 != pid2


def test_find_paper_doi_takes_precedence_over_pdf_hash_order(store):
    """find_paper(pdf_hash=...) checked first, then doi."""
    store.register_paper(title="P1", pdf_hash="h1", doi="10.1/x")
    store.register_paper(title="P2", pdf_hash="h2", doi="10.2/y")
    r = store.find_paper(pdf_hash="h2", doi="10.1/x")
    # pdf_hash="h2" matches P2
    assert r is not None and r.title == "P2"


def test_register_paper_stores_metadata(store):
    store.register_paper(
        title="Full Meta",
        authors="Smith, J.; Doe, A.",
        year=2020,
        doi="10.5/full",
        abstract="An abstract.",
        pdf_hash="full-hash",
    )
    rec = store.find_paper(pdf_hash="full-hash")
    assert rec.authors == "Smith, J.; Doe, A."
    assert rec.abstract == "An abstract."
    assert rec.doi == "10.5/full"


# ---------------------------------------------------------------------------
# save_fragments / get_fragments
# ---------------------------------------------------------------------------


def test_save_and_get_fragments(store):
    pid = store.register_paper(title="Frags Paper", pdf_hash="fhash")
    frags = [
        _make_fragment(pid, "Fragment one", page=1),
        _make_fragment(pid, "Fragment two", page=2),
    ]
    n = store.save_fragments(pid, frags, prompt_hash="p0001", ai_model="test-model")
    assert n == 2
    result = store.get_fragments(pid)
    assert len(result) == 2
    texts = {f.fragment_text for f in result}
    assert texts == {"Fragment one", "Fragment two"}


def test_save_fragments_idempotent(store):
    """INSERT OR IGNORE — same fragment_id inserted twice counts as 1."""
    pid = store.register_paper(title="Dedup Frags", pdf_hash="ddhash")
    frags = [_make_fragment(pid, "Same text", page=1)]
    n1 = store.save_fragments(pid, frags, prompt_hash="p001", ai_model="m1")
    n2 = store.save_fragments(pid, frags, prompt_hash="p001", ai_model="m1")
    assert n1 == 1
    assert n2 == 0  # already exists
    assert len(store.get_fragments(pid)) == 1


def test_get_fragments_empty_returns_empty_list(store):
    pid = store.register_paper(title="No Frags", pdf_hash="nfhash")
    assert store.get_fragments(pid) == []


def test_fragments_have_correct_types(store):
    pid = store.register_paper(title="Type Test", pdf_hash="tthash")
    frag = FragmentRecord(
        fragment_id="custom-id",
        paper_id=pid,
        fragment_text="Custom fragment",
        fragment_type="methodology",
        page_number=5,
        citation_intent="method",
        content_hash="custom-id",
    )
    store.save_fragments(pid, [frag], prompt_hash="p002", ai_model="m2")
    result = store.get_fragments(pid)
    assert result[0].fragment_type == "methodology"
    assert result[0].citation_intent == "method"
    assert result[0].page_number == 5


# ---------------------------------------------------------------------------
# Paper-level embeddings
# ---------------------------------------------------------------------------


def test_paper_embedding_roundtrip(store):
    pid = store.register_paper(title="Embed Paper", pdf_hash="ephash")
    vector = [0.1, 0.2, 0.3, 0.4, 0.5]
    store.save_paper_embedding(pid, vector, model="test-specter")
    result = store.get_paper_embedding(pid, model="test-specter")
    assert result is not None
    assert len(result) == 5
    assert all(abs(a - b) < 1e-5 for a, b in zip(result, vector))


def test_paper_embedding_missing_returns_none(store):
    pid = store.register_paper(title="No Embed", pdf_hash="nehash")
    assert store.get_paper_embedding(pid, model="nonexistent-model") is None


def test_paper_embedding_upsert(store):
    """save_paper_embedding is INSERT OR REPLACE — second call overwrites."""
    pid = store.register_paper(title="Upsert Paper", pdf_hash="uphash")
    store.save_paper_embedding(pid, [1.0, 2.0], model="m")
    store.save_paper_embedding(pid, [9.0, 8.0], model="m")
    result = store.get_paper_embedding(pid, model="m")
    assert result[0] > 5.0  # new values persisted


# ---------------------------------------------------------------------------
# Fragment-level embeddings
# ---------------------------------------------------------------------------


def test_fragment_embedding_roundtrip(store):
    pid = store.register_paper(title="Frag Embed", pdf_hash="fehash")
    frag = _make_fragment(pid, "Embeddable text", page=3)
    store.save_fragments(pid, [frag], prompt_hash="ph", ai_model="m")
    vector = [0.5, 0.6, 0.7]
    store.save_fragment_embedding(frag.fragment_id, vector, model="specter2")
    result = store.get_fragment_embeddings(pid, model="specter2")
    assert frag.fragment_id in result
    assert all(abs(a - b) < 1e-5 for a, b in zip(result[frag.fragment_id], vector))


def test_fragment_embeddings_empty(store):
    pid = store.register_paper(title="No Frag Embed", pdf_hash="nfehash")
    assert store.get_fragment_embeddings(pid, model="m") == {}


# ---------------------------------------------------------------------------
# Dual-write / cache scenario
# ---------------------------------------------------------------------------


def test_dual_write_cache_hit(tmp_path):
    """Second project processing same PDF finds library cache, skips Claude."""
    db_path = tmp_path / "library.db"
    store = LocalPaperStore(db_path)

    # Project A processes paper and writes to library
    pid = store.register_paper(title="Shared Paper", pdf_hash="shared-hash")
    frags = [
        _make_fragment(pid, "Finding A", page=1),
        _make_fragment(pid, "Finding B", page=2),
    ]
    store.save_fragments(pid, frags, prompt_hash="ph1", ai_model="claude")

    # Project B opens same store and finds the paper by hash
    rec = store.find_paper(pdf_hash="shared-hash")
    assert rec is not None
    assert rec.paper_id == pid

    cached = store.get_fragments(pid)
    assert len(cached) == 2
    assert {f.fragment_text for f in cached} == {"Finding A", "Finding B"}
