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


def test_schema_version_is_4(tmp_path):
    db_path = tmp_path / "library.db"
    LocalUserLibrary(db_path)
    conn = sqlite3.connect(str(db_path))
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    conn.close()
    assert version == 4


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
    assert version == 4
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
