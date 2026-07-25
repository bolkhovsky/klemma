"""Tests for LocalPaperStore (ADR-014 Phase 1B)."""

import hashlib
import importlib.util
import sqlite3

import pytest

from klemma.models import FragmentRecord
from klemma.stores import LocalPaperStore
from klemma.stores.user_library import LocalUserLibrary

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


def test_get_latest_embedding_dim_returns_most_recent(store):
    """get_latest_embedding_dim returns the dim of the last-inserted embedding."""
    p1 = store.register_paper(title="Old Model", pdf_hash="old1")
    p2 = store.register_paper(title="New Model", pdf_hash="new1")
    # Embed p1 with old 2-dim model first, then p2 with new 4-dim model
    store.save_paper_embedding(p1, [0.1, 0.2], model="specter-v1")
    store.save_paper_embedding(p2, [0.1, 0.2, 0.3, 0.4], model="bge-m3")
    dim = store.get_latest_embedding_dim([p1, p2])
    # p2 was inserted after p1 → highest rowid → 4-dim wins
    assert dim == 4


def test_get_latest_embedding_dim_empty_returns_none(store):
    assert store.get_latest_embedding_dim([]) is None


def test_get_latest_embedding_dim_no_embeddings_returns_none(store):
    pid = store.register_paper(title="No Embedding", pdf_hash="noembhash")
    assert store.get_latest_embedding_dim([pid]) is None


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


# ---------------------------------------------------------------------------
# get_reference_gaps — user-scoped NOT EXISTS filter
# ---------------------------------------------------------------------------


def _title_hash(title: str) -> str:
    return hashlib.md5(title.lower().encode()).hexdigest()


def test_reference_gaps_excludes_only_current_user_papers(tmp_path):
    """NOT EXISTS filter must be scoped to the current user's library.

    If user B has uploaded a paper whose title matches a gap for user A,
    that gap must still appear in user A's recommendations.
    Before the fix, the global papers table was queried without user_id
    filtering — any user's upload would suppress gaps for all other users.
    """
    db_path = tmp_path / "library.db"
    store = LocalPaperStore(db_path)
    library = LocalUserLibrary(db_path)  # initializes user_sources table in same DB

    user_a = "user-a"
    user_b = "user-b"

    # User A has one paper that cites "Gap Paper"
    pid_a = store.register_paper(title="User A Paper", pdf_hash="hasha")
    store.save_citation_links(pid_a, [
        {"title": "Gap Paper", "authors": "X", "year": 2020}
    ])
    library.add_source(pid_a, "user_a_paper", status="completed", user_id=user_a)

    # User B has ALSO uploaded "Gap Paper" — it's in the global papers table
    pid_gap = store.register_paper(title="Gap Paper", pdf_hash="hashgap")
    library.add_source(pid_gap, "gap_paper", status="completed", user_id=user_b)

    # User A's gaps should still include "Gap Paper" — user B's ownership is irrelevant
    gaps, _ = store.get_reference_gaps(paper_ids=[pid_a], user_id=user_a, limit=50)
    gap_titles = [g["title"] for g in gaps]
    assert "Gap Paper" in gap_titles, (
        "Gap Paper should appear for user A even though user B uploaded it"
    )

    # If user A also uploads "Gap Paper", it should disappear from gaps
    library.add_source(pid_gap, "gap_paper_a", status="completed", user_id=user_a)

    gaps2, _ = store.get_reference_gaps(paper_ids=[pid_a], user_id=user_a, limit=50)
    gap_titles2 = [g["title"] for g in gaps2]
    assert "Gap Paper" not in gap_titles2, (
        "Gap Paper should NOT appear once user A has it in their own library"
    )


# ---------------------------------------------------------------------------
# M1: sqlite-vec KNN index
# ---------------------------------------------------------------------------

_VEC_AVAILABLE = importlib.util.find_spec("sqlite_vec") is not None

_skip_no_vec = pytest.mark.skipif(not _VEC_AVAILABLE, reason="sqlite-vec not installed")


_VEC_DIM = 1024  # must match vec table dimension (FLOAT[1024] default)


def _unit_vec(hot_index: int, dim: int = _VEC_DIM) -> list[float]:
    """Return a unit vector with 1.0 at hot_index and 0.0 elsewhere."""
    v = [0.0] * dim
    v[hot_index] = 1.0
    return v


def _make_vec_db(tmp_path, user_id: str):
    """Return (store, library, paper_id, fragment_id) with seeded vec entries."""
    import os
    os.environ["KLEMMA_EMBEDDINGS_MODEL"] = "test-model"
    db_path = tmp_path / "library.db"
    store = LocalPaperStore(db_path)
    library = LocalUserLibrary(db_path)

    paper_id = store.register_paper(title="Test Paper", pdf_hash="hash1")
    library.add_source(paper_id, "paper1", status="completed", user_id=user_id)

    frag = _make_fragment(paper_id, "Fragment text about ice edge metrics", page=1)
    store.save_fragments(paper_id, [frag], prompt_hash="p", ai_model="m")

    store.save_fragment_embedding(frag.fragment_id, _unit_vec(0), "test-model")

    return store, library, paper_id, frag.fragment_id


@_skip_no_vec
def test_find_similar_fragments_returns_result(tmp_path):
    store, _, paper_id, frag_id = _make_vec_db(tmp_path, "user-a")
    results = store.find_similar_fragments(_unit_vec(0), user_id="user-a", limit=5)
    assert len(results) == 1
    assert results[0]["fragment_id"] == frag_id
    assert results[0]["similarity"] > 0.99


@_skip_no_vec
def test_find_similar_fragments_user_isolation(tmp_path):
    import os
    os.environ["KLEMMA_EMBEDDINGS_MODEL"] = "test-model"
    db_path = tmp_path / "library.db"
    store = LocalPaperStore(db_path)
    library = LocalUserLibrary(db_path)

    pid_a = store.register_paper(title="Paper A", pdf_hash="hasha")
    library.add_source(pid_a, "paper_a", status="completed", user_id="user-a")
    frag_a = _make_fragment(pid_a, "Fragment for user A")
    store.save_fragments(pid_a, [frag_a], prompt_hash="p", ai_model="m")
    store.save_fragment_embedding(frag_a.fragment_id, _unit_vec(0), "test-model")

    pid_b = store.register_paper(title="Paper B", pdf_hash="hashb")
    library.add_source(pid_b, "paper_b", status="completed", user_id="user-b")
    frag_b = _make_fragment(pid_b, "Fragment for user B")
    store.save_fragments(pid_b, [frag_b], prompt_hash="p", ai_model="m")
    store.save_fragment_embedding(frag_b.fragment_id, _unit_vec(1), "test-model")

    results_a = store.find_similar_fragments(_unit_vec(0), user_id="user-a", limit=10)
    ids_a = {r["fragment_id"] for r in results_a}
    assert frag_a.fragment_id in ids_a
    assert frag_b.fragment_id not in ids_a

    results_b = store.find_similar_fragments(_unit_vec(1), user_id="user-b", limit=10)
    ids_b = {r["fragment_id"] for r in results_b}
    assert frag_b.fragment_id in ids_b
    assert frag_a.fragment_id not in ids_b


@_skip_no_vec
def test_find_similar_fragments_citekey_filter(tmp_path):
    import os
    os.environ["KLEMMA_EMBEDDINGS_MODEL"] = "test-model"
    db_path = tmp_path / "library.db"
    store = LocalPaperStore(db_path)
    library = LocalUserLibrary(db_path)

    pid1 = store.register_paper(title="Paper 1", pdf_hash="h1")
    library.add_source(pid1, "paper1", status="completed", user_id="user-x")
    frag1 = _make_fragment(pid1, "Fragment one")
    store.save_fragments(pid1, [frag1], prompt_hash="p", ai_model="m")
    store.save_fragment_embedding(frag1.fragment_id, _unit_vec(0), "test-model")

    pid2 = store.register_paper(title="Paper 2", pdf_hash="h2")
    library.add_source(pid2, "paper2", status="completed", user_id="user-x")
    frag2 = _make_fragment(pid2, "Fragment two")
    store.save_fragments(pid2, [frag2], prompt_hash="p", ai_model="m")
    store.save_fragment_embedding(frag2.fragment_id, _unit_vec(0), "test-model")

    results = store.find_similar_fragments(_unit_vec(0), user_id="user-x", limit=10, citekey_filter="paper1")
    assert all(r["citekey"] == "paper1" for r in results)
    assert any(r["fragment_id"] == frag1.fragment_id for r in results)
    assert all(r["fragment_id"] != frag2.fragment_id for r in results)


@_skip_no_vec
def test_find_similar_fragments_unknown_citekey_filter(tmp_path):
    store, _, _, _ = _make_vec_db(tmp_path, "user-a")
    results = store.find_similar_fragments(_unit_vec(0), user_id="user-a", limit=5, citekey_filter="nonexistent")
    assert results == []


def test_find_similar_fragments_no_vec_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setattr("klemma.stores.paper_store._SQLITE_VEC_INSTALLED", False)
    store = LocalPaperStore(tmp_path / "library.db")
    results = store.find_similar_fragments(_unit_vec(0), user_id="user-a", limit=5)
    assert results == []


@_skip_no_vec
def test_ensure_vec_entries_for_user_paper(tmp_path):
    """Attaching an already-processed paper to a new user creates vec rows immediately."""
    import os
    os.environ["KLEMMA_EMBEDDINGS_MODEL"] = "test-model"
    db_path = tmp_path / "library.db"
    store = LocalPaperStore(db_path)
    library = LocalUserLibrary(db_path)

    # Process paper under user-a
    paper_id = store.register_paper(title="Shared Paper", pdf_hash="shared_hash")
    library.add_source(paper_id, "shared_paper", status="completed", user_id="user-a")
    frag = _make_fragment(paper_id, "Shared fragment")
    store.save_fragments(paper_id, [frag], prompt_hash="p", ai_model="m")
    store.save_fragment_embedding(frag.fragment_id, _unit_vec(0), "test-model")

    # Attach same paper to user-b WITHOUT re-embedding
    library.add_source(paper_id, "shared_paper_b", status="completed", user_id="user-b")
    created = store.ensure_vec_entries_for_user_paper("user-b", paper_id)
    assert created >= 1

    results = store.find_similar_fragments(_unit_vec(0), user_id="user-b", limit=5)
    assert any(r["fragment_id"] == frag.fragment_id for r in results)


@_skip_no_vec
def test_delete_fragments_cleans_vec_entries(tmp_path):
    """delete_fragments() must clean vec index before deleting from fragments (FK order)."""
    import os
    os.environ["KLEMMA_EMBEDDINGS_MODEL"] = "test-model"
    store, _, paper_id, frag_id = _make_vec_db(tmp_path, "user-a")

    # Sanity: fragment is findable
    results_before = store.find_similar_fragments(_unit_vec(0), user_id="user-a", limit=5)
    assert any(r["fragment_id"] == frag_id for r in results_before)

    # Delete should not raise FK error
    deleted = store.delete_fragments(paper_id)
    assert deleted >= 1

    # Fragment should no longer appear in vec search
    results_after = store.find_similar_fragments(_unit_vec(0), user_id="user-a", limit=5)
    assert all(r["fragment_id"] != frag_id for r in results_after)


@_skip_no_vec
def test_rebuild_updates_dimensions_state(tmp_path):
    """Switching embedding model to a different dimension must update stored dimensions."""
    import os
    import sqlite3

    os.environ["KLEMMA_EMBEDDINGS_MODEL"] = "model-a"
    db_path = tmp_path / "library.db"
    store = LocalPaperStore(db_path)
    library = LocalUserLibrary(db_path)

    paper_id = store.register_paper(title="Dim Paper", pdf_hash="dim_hash")
    library.add_source(paper_id, "dim_paper", status="completed", user_id="user-a")
    frag = _make_fragment(paper_id, "Dimension test fragment")
    store.save_fragments(paper_id, [frag], prompt_hash="p", ai_model="m")
    store.save_fragment_embedding(frag.fragment_id, _unit_vec(0), "model-a")

    conn = sqlite3.connect(str(db_path))
    dim_before = conn.execute(
        "SELECT state_value FROM fragments_vec_state WHERE state_key='dimensions'"
    ).fetchone()[0]
    conn.close()
    assert dim_before == str(_VEC_DIM)

    # Simulate model switch to a different model stored with 512-dim vectors
    # by writing a fake embedding with dim=512, then triggering rebuild
    short_vec = [1.0] + [0.0] * 511
    short_blob = __import__("struct").pack(f"{512}f", *short_vec)
    raw_conn = sqlite3.connect(str(db_path))
    raw_conn.execute(
        "INSERT OR IGNORE INTO fragment_embeddings(fragment_id, model_name, vector, dimensions)"
        " VALUES (?, ?, ?, ?)",
        (frag.fragment_id, "model-b", short_blob, 512),
    )
    raw_conn.commit()
    raw_conn.close()

    os.environ["KLEMMA_EMBEDDINGS_MODEL"] = "model-b"
    LocalPaperStore(db_path)  # triggers _maybe_rebuild_vec_index with placeholder dim

    conn2 = sqlite3.connect(str(db_path))
    dim_after = conn2.execute(
        "SELECT state_value FROM fragments_vec_state WHERE state_key='dimensions'"
    ).fetchone()[0]
    model_after = conn2.execute(
        "SELECT state_value FROM fragments_vec_state WHERE state_key='active_model'"
    ).fetchone()[0]
    conn2.close()

    assert dim_after == "512", f"dimensions state must be updated to 512, got {dim_after}"
    assert model_after == "model-b"


@_skip_no_vec
def test_dim_mismatch_inline_rebuild(tmp_path):
    """First write with a new-dim vector triggers inline rebuild when model switches
    before any embeddings for the new model exist at rebuild time.

    Scenario:
    1. Model A writes 1024-dim embeddings → vec table = FLOAT[1024].
    2. Model B is set as active; no model-B embeddings exist yet → rebuild uses stored
       placeholder dim (1024), actual_dim_known=False.
    3. First save_fragment_embedding() with model B's 512-dim vector hits dimension
       mismatch → _rebuild_vec_table_with_dim() called inline.
    4. After rebuild, vec table is FLOAT[512] and find_similar_fragments() works.
    """
    import os
    import sqlite3 as _sqlite3

    os.environ["KLEMMA_EMBEDDINGS_MODEL"] = "model-a"
    db_path = tmp_path / "library.db"
    store = LocalPaperStore(db_path)
    library = LocalUserLibrary(db_path)

    paper_id = store.register_paper(title="Mismatch Paper", pdf_hash="mm_hash")
    library.add_source(paper_id, "mm_paper", status="completed", user_id="user-mm")
    frag = _make_fragment(paper_id, "Inline rebuild test fragment")
    store.save_fragments(paper_id, [frag], prompt_hash="p", ai_model="m")

    # Write model-A embedding (1024-dim) so vec table is FLOAT[1024]
    store.save_fragment_embedding(frag.fragment_id, _unit_vec(0, _VEC_DIM), "model-a")

    # Switch to model-B with a DIFFERENT dimension (512) with NO model-B embeddings yet
    os.environ["KLEMMA_EMBEDDINGS_MODEL"] = "model-b"
    # Instantiate new store → _maybe_rebuild_vec_index fires, but actual_dim_known=False
    # because no model-b rows exist → table stays/recreated at stored_dim=1024 (placeholder)
    store2 = LocalPaperStore(db_path)

    # First write with model-B's actual dim (512) should trigger inline dim-fix rebuild
    short_vec = [1.0] + [0.0] * 511  # 512-dim
    store2.save_fragment_embedding(frag.fragment_id, short_vec, "model-b")

    # Verify state updated to 512
    raw = _sqlite3.connect(str(db_path))
    dim_state = raw.execute(
        "SELECT state_value FROM fragments_vec_state WHERE state_key='dimensions'"
    ).fetchone()[0]
    raw.close()
    assert dim_state == "512", f"expected dim=512 in state after inline rebuild, got {dim_state}"

    # Verify find_similar_fragments works with a 512-dim query
    results = store2.find_similar_fragments(short_vec, user_id="user-mm", limit=5)
    assert len(results) == 1, f"expected 1 result after inline rebuild, got {len(results)}"
    assert results[0]["fragment_id"] == frag.fragment_id
