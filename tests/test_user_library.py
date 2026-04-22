"""Tests for LocalUserLibrary (ADR-014 Phase 1C)."""

import sqlite3

import pytest

from klemma.stores import LocalUserLibrary


@pytest.fixture
def lib(tmp_path) -> LocalUserLibrary:
    return LocalUserLibrary(tmp_path / "library.db")


# ---------------------------------------------------------------------------
# Schema / migration
# ---------------------------------------------------------------------------


def test_schema_version_is_6(tmp_path):
    db_path = tmp_path / "library.db"
    LocalUserLibrary(db_path)
    conn = sqlite3.connect(str(db_path))
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    conn.close()
    assert version == 6


def test_tables_created(tmp_path):
    db_path = tmp_path / "library.db"
    LocalUserLibrary(db_path)
    conn = sqlite3.connect(str(db_path))
    tables = {
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    conn.close()
    assert {"user_sources", "user_source_chapters", "user_source_sections"} <= tables


def test_paper_store_schema_coexists(tmp_path):
    """LocalPaperStore (v1) + LocalUserLibrary (v2) on same db_path."""
    from klemma.stores import LocalPaperStore

    db_path = tmp_path / "library.db"
    LocalPaperStore(db_path)  # creates v1 tables
    LocalUserLibrary(db_path)  # upgrades to v2, adds user_sources

    conn = sqlite3.connect(str(db_path))
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    tables = {
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    conn.close()
    assert version == 6
    # Both sets of tables present
    assert "papers" in tables
    assert "user_sources" in tables


def test_migration_idempotent(tmp_path):
    db_path = tmp_path / "library.db"
    lib1 = LocalUserLibrary(db_path)
    lib1.add_source("pid1", "smith2022")
    lib2 = LocalUserLibrary(db_path)  # second init — no data lost
    assert lib2.resolve_paper_id("smith2022") == "pid1"


# ---------------------------------------------------------------------------
# add_source / get_source_by_citekey
# ---------------------------------------------------------------------------


def test_add_source_basic(lib):
    lib.add_source("paper-uuid-1", "jones2023ml")
    src = lib.get_source_by_citekey("jones2023ml")
    assert src is not None
    assert src.citekey == "jones2023ml"
    assert src.paper_id == "paper-uuid-1"
    assert src.status == "pending"


def test_add_source_with_metadata(lib):
    lib.add_source(
        "pid2",
        "doe2021",
        status="completed",
        pdf_path="/tmp/doe2021.pdf",
        quality_score=4,
        chapters=[1, 2],
        sections=["1.1", "2.3"],
    )
    src = lib.get_source_by_citekey("doe2021")
    assert src.status == "completed"
    assert src.pdf_path == "/tmp/doe2021.pdf"
    assert src.quality_score == 4
    assert 1 in src.chapters
    assert "1.1" in src.sections
    assert "2.3" in src.sections


def test_add_source_upserts(lib):
    lib.add_source("pid-orig", "smith2020", status="pending")
    lib.add_source("pid-new", "smith2020", status="completed")
    src = lib.get_source_by_citekey("smith2020")
    assert src.paper_id == "pid-new"
    assert src.status == "completed"


def test_get_source_missing_returns_none(lib):
    assert lib.get_source_by_citekey("nonexistent") is None


# ---------------------------------------------------------------------------
# resolve_paper_id
# ---------------------------------------------------------------------------


def test_resolve_paper_id(lib):
    lib.add_source("uuid-abc", "wang2019")
    assert lib.resolve_paper_id("wang2019") == "uuid-abc"


def test_resolve_paper_id_missing(lib):
    assert lib.resolve_paper_id("ghost") is None


# ---------------------------------------------------------------------------
# get_existing_citekeys
# ---------------------------------------------------------------------------


def test_get_existing_citekeys_empty(lib):
    assert lib.get_existing_citekeys() == set()


def test_get_existing_citekeys_multiple(lib):
    lib.add_source("p1", "a2020")
    lib.add_source("p2", "b2021")
    lib.add_source("p3", "c2022")
    assert lib.get_existing_citekeys() == {"a2020", "b2021", "c2022"}


# ---------------------------------------------------------------------------
# update_status / count
# ---------------------------------------------------------------------------


def test_update_status(lib):
    lib.add_source("pid", "test2022", status="pending")
    lib.update_status("test2022", "completed")
    src = lib.get_source_by_citekey("test2022")
    assert src.status == "completed"


def test_count(lib):
    assert lib.count() == 0
    lib.add_source("p1", "a")
    lib.add_source("p2", "b")
    assert lib.count() == 2


# ---------------------------------------------------------------------------
# sections and chapters
# ---------------------------------------------------------------------------


def test_chapters_replaced_on_upsert(lib):
    lib.add_source("pid", "src", chapters=[1])
    lib.add_source("pid", "src", chapters=[2, 3])
    src = lib.get_source_by_citekey("src")
    assert src.chapters == [2, 3]


def test_sections_replaced_on_upsert(lib):
    lib.add_source("pid", "src2", sections=["1.1"])
    lib.add_source("pid", "src2", sections=["2.1", "3.2"])
    src = lib.get_source_by_citekey("src2")
    assert set(src.sections) == {"2.1", "3.2"}


def test_project_id_filter(lib):
    lib.add_source("pid1", "src_a", project_id="proj1")
    lib.add_source("pid2", "src_b", project_id="proj2")
    lib.add_source("pid3", "src_c")  # no project

    in_proj1 = lib.get_all_sources(project_id="proj1")
    assert {s.citekey for s in in_proj1} == {"src_a", "src_c"}  # includes NULL-project sources

    in_proj2 = lib.get_all_sources(project_id="proj2")
    assert {s.citekey for s in in_proj2} == {"src_b", "src_c"}  # includes NULL-project sources

    all_sources = lib.get_all_sources()
    assert len(all_sources) == 3


def test_project_id_preserved_on_status_upsert(lib):
    """project_id is not overwritten when updating status without project_id."""
    lib.add_source("pid1", "src_x", project_id="proj1")
    lib.add_source("pid1", "src_x", status="completed")  # no project_id
    src = lib.get_source_by_citekey("src_x")
    assert src.status == "completed"


def test_get_all_sources_since_future_returns_empty(lib):
    """since=far-future filters out all existing sources."""
    lib.add_source("pid1", "alpha")
    lib.add_source("pid2", "beta")
    sources = lib.get_all_sources(since="2099-01-01T00:00:00")
    assert sources == []


def test_get_all_sources_since_past_returns_all(lib):
    """since=far-past returns all sources."""
    lib.add_source("pid1", "alpha")
    lib.add_source("pid2", "beta")
    sources = lib.get_all_sources(since="2000-01-01T00:00:00")
    assert {s.citekey for s in sources} == {"alpha", "beta"}


# ---------------------------------------------------------------------------
# Multi-user isolation (v5 composite PK)
# ---------------------------------------------------------------------------


def test_same_citekey_different_users_no_collision(lib):
    """Two users can register the same citekey without collision."""
    lib.add_source("paper-a", "shared_ck", user_id="user-A", status="pending")
    lib.add_source("paper-b", "shared_ck", user_id="user-B", status="completed")

    src_a = lib.get_source_by_citekey("shared_ck", user_id="user-A")
    src_b = lib.get_source_by_citekey("shared_ck", user_id="user-B")
    assert src_a is not None
    assert src_b is not None
    assert src_a.paper_id == "paper-a"
    assert src_b.paper_id == "paper-b"
    assert src_a.status == "pending"
    assert src_b.status == "completed"


def test_update_status_user_scoped(lib):
    """update_status for one user does not affect another user's same citekey."""
    lib.add_source("p1", "ck", user_id="alice", status="pending")
    lib.add_source("p2", "ck", user_id="bob", status="pending")

    lib.update_status("ck", "completed", user_id="alice")

    assert lib.get_source_by_citekey("ck", user_id="alice").status == "completed"
    assert lib.get_source_by_citekey("ck", user_id="bob").status == "pending"


def test_remove_source_user_scoped(lib):
    """Removing a source for one user doesn't affect another user's same citekey."""
    lib.add_source("p1", "ck", user_id="alice")
    lib.add_source("p2", "ck", user_id="bob")

    lib.remove_source("ck", user_id="alice")

    assert lib.get_source_by_citekey("ck", user_id="alice") is None
    assert lib.get_source_by_citekey("ck", user_id="bob") is not None


def test_get_all_sources_user_scoped(lib):
    """get_all_sources respects user_id scoping."""
    lib.add_source("p1", "src1", user_id="x")
    lib.add_source("p2", "src2", user_id="y")
    lib.add_source("p3", "src3", user_id="x")

    x_sources = lib.get_all_sources(user_id="x")
    y_sources = lib.get_all_sources(user_id="y")
    assert {s.citekey for s in x_sources} == {"src1", "src3"}
    assert {s.citekey for s in y_sources} == {"src2"}


def test_count_user_scoped(lib):
    """count() respects user_id scoping."""
    lib.add_source("p1", "a", user_id="x")
    lib.add_source("p2", "b", user_id="y")
    lib.add_source("p3", "c", user_id="x")

    assert lib.count(user_id="x") == 2
    assert lib.count(user_id="y") == 1
    assert lib.count() == 3


def test_resolve_paper_id_user_scoped(lib):
    """resolve_paper_id returns the correct paper for the right user."""
    lib.add_source("paper-A", "shared", user_id="u1")
    lib.add_source("paper-B", "shared", user_id="u2")

    assert lib.resolve_paper_id("shared", user_id="u1") == "paper-A"
    assert lib.resolve_paper_id("shared", user_id="u2") == "paper-B"


def test_get_existing_citekeys_user_scoped(lib):
    """get_existing_citekeys returns only the specified user's citekeys."""
    lib.add_source("p1", "a", user_id="alice")
    lib.add_source("p2", "b", user_id="alice")
    lib.add_source("p3", "c", user_id="bob")

    assert lib.get_existing_citekeys(user_id="alice") == {"a", "b"}
    assert lib.get_existing_citekeys(user_id="bob") == {"c"}
    assert lib.get_existing_citekeys() == {"a", "b", "c"}


def test_chapters_sections_user_scoped(lib):
    """Chapters and sections are scoped to the user."""
    lib.add_source("p1", "ck", user_id="u1", chapters=[1, 2], sections=["1.1", "2.1"])
    lib.add_source("p2", "ck", user_id="u2", chapters=[3], sections=["3.1"])

    src_u1 = lib.get_source_by_citekey("ck", user_id="u1")
    src_u2 = lib.get_source_by_citekey("ck", user_id="u2")
    assert src_u1.chapters == [1, 2]
    assert set(src_u1.sections) == {"1.1", "2.1"}
    assert src_u2.chapters == [3]
    assert src_u2.sections == ["3.1"]
