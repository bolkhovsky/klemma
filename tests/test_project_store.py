"""Tests for LocalProjectStore (ADR-014 Phase 1C)."""

import sqlite3

import pytest

from klemma.stores import LocalProjectStore


@pytest.fixture
def store(tmp_path) -> LocalProjectStore:
    return LocalProjectStore(tmp_path / "project.db")


# ---------------------------------------------------------------------------
# Schema / migration
# ---------------------------------------------------------------------------


def test_schema_version_is_1(tmp_path):
    db_path = tmp_path / "project.db"
    LocalProjectStore(db_path)
    conn = sqlite3.connect(str(db_path))
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    conn.close()
    assert version == 1


def test_tables_created(tmp_path):
    db_path = tmp_path / "project.db"
    LocalProjectStore(db_path)
    conn = sqlite3.connect(str(db_path))
    tables = {
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    conn.close()
    assert {"project_sources", "project_source_sections", "project_fragments"} <= tables


def test_migration_idempotent(tmp_path):
    db_path = tmp_path / "project.db"
    store1 = LocalProjectStore(db_path)
    store1.set_source_sections("smith2022", "pid1", ["1.1"], [1])
    store2 = LocalProjectStore(db_path)  # second init — no data lost
    assert store2.count_sources() == 1


def test_creates_parent_dirs(tmp_path):
    db_path = tmp_path / "nested" / "data" / "project.db"
    LocalProjectStore(db_path)
    assert db_path.exists()


# ---------------------------------------------------------------------------
# set_source_sections / get_source_sections
# ---------------------------------------------------------------------------


def test_set_source_sections_basic(store):
    store.set_source_sections("jones2023", "uuid-1", ["1.1", "2.3"], [1, 2])
    sections = store.get_source_sections("jones2023")
    assert "1.1" in sections
    assert "2.3" in sections


def test_set_source_sections_upserts(store):
    store.set_source_sections("doe2021", "pid-a", ["1.1"], [1])
    store.set_source_sections("doe2021", "pid-b", ["2.1", "3.2"], [2, 3])
    sections = store.get_source_sections("doe2021")
    assert set(sections) == {"2.1", "3.2"}


def test_set_source_sections_replaces_old(store):
    store.set_source_sections("wang2019", "pid", ["1.1", "1.2"], [1])
    store.set_source_sections("wang2019", "pid", ["3.1"], [3])
    sections = store.get_source_sections("wang2019")
    assert sections == ["3.1"]


def test_set_source_sections_empty_sections(store):
    store.set_source_sections("minimal2020", "pid", [], [])
    sections = store.get_source_sections("minimal2020")
    assert sections == []


def test_get_source_sections_missing(store):
    assert store.get_source_sections("nonexistent") == []


# ---------------------------------------------------------------------------
# get_sources_by_section
# ---------------------------------------------------------------------------


def test_get_sources_by_section(store):
    store.set_source_sections("a2020", "p1", ["1.1", "2.3"], [1, 2])
    store.set_source_sections("b2021", "p2", ["1.1"], [1])
    store.set_source_sections("c2022", "p3", ["3.1"], [3])
    result = store.get_sources_by_section("1.1")
    assert set(result) == {"a2020", "b2021"}


def test_get_sources_by_section_empty(store):
    assert store.get_sources_by_section("9.9") == []


# ---------------------------------------------------------------------------
# get_coverage_stats
# ---------------------------------------------------------------------------


def test_coverage_stats_empty(store):
    stats = store.get_coverage_stats()
    assert stats["total_sources"] == 0
    assert stats["by_section"] == {}


def test_coverage_stats_with_data(store):
    store.set_source_sections("a2020", "p1", ["1.1", "2.3"], [1, 2])
    store.set_source_sections("b2021", "p2", ["1.1"], [1])
    stats = store.get_coverage_stats()
    assert stats["total_sources"] == 2
    assert stats["by_section"]["1.1"] == 2
    assert stats["by_section"]["2.3"] == 1


# ---------------------------------------------------------------------------
# get_reference_gaps (Phase 1C stub)
# ---------------------------------------------------------------------------


def test_reference_gaps_returns_empty_list(store):
    result = store.get_reference_gaps()
    assert result == []


def test_reference_gaps_ignores_kwargs(store):
    result = store.get_reference_gaps(section="1.1", chapter=1)
    assert result == []


# ---------------------------------------------------------------------------
# register_fragment
# ---------------------------------------------------------------------------


def test_register_fragment_basic(store):
    store.set_source_sections("src2023", "pid", ["1.1"], [1])
    store.register_fragment(
        "frag-id-001",
        citekey="src2023",
        section="1.1",
        section_type="introduction",
        chapter=1,
        relevance_score=4,
    )
    # Verify via raw DB
    conn = sqlite3.connect(str(store._db_path))
    row = conn.execute(
        "SELECT * FROM project_fragments WHERE fragment_id='frag-id-001'"
    ).fetchone()
    conn.close()
    assert row is not None


def test_register_fragment_insert_or_ignore(store):
    # Inserting same fragment_id twice should not raise
    store.register_fragment("frag-dup", citekey="src", section="1.1")
    store.register_fragment("frag-dup", citekey="src", section="1.1")
    conn = sqlite3.connect(str(store._db_path))
    count = conn.execute(
        "SELECT COUNT(*) FROM project_fragments WHERE fragment_id='frag-dup'"
    ).fetchone()[0]
    conn.close()
    assert count == 1


# ---------------------------------------------------------------------------
# count_sources
# ---------------------------------------------------------------------------


def test_count_sources_empty(store):
    assert store.count_sources() == 0


def test_count_sources_multiple(store):
    store.set_source_sections("a", "p1", ["1.1"], [1])
    store.set_source_sections("b", "p2", ["2.1"], [2])
    store.set_source_sections("c", "p3", ["3.1"], [3])
    assert store.count_sources() == 3
