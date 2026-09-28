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


def test_schema_version_is_6(tmp_path):
    db_path = tmp_path / "project.db"
    LocalProjectStore(db_path)
    conn = sqlite3.connect(str(db_path))
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    conn.close()
    assert version == 6


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
    assert {"project_sources", "project_source_sections", "project_fragments", "prune_verdicts"} <= tables


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
    assert stats["sections"] == {}
    assert stats["chapters"] == {}


def test_coverage_stats_with_data(store):
    store.set_source_sections("a2020", "p1", ["1.1", "2.3"], [1, 2])
    store.set_source_sections("b2021", "p2", ["1.1"], [1])
    stats = store.get_coverage_stats()
    assert stats["total_sources"] == 2
    # sections and by_section are the same dict
    assert stats["by_section"]["1.1"] == 2
    assert stats["by_section"]["2.3"] == 1
    assert stats["sections"] is stats["by_section"]
    # chapters aggregation
    assert stats["chapters"][1] == 2  # a2020 + b2021 both in ch 1
    assert stats["chapters"][2] == 1  # a2020 in ch 2
    # StateManager-compat keys present
    assert "section_type_lookup" in stats
    assert "section_types" in stats


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


# ---------------------------------------------------------------------------
# prune_verdicts (schema v2)
# ---------------------------------------------------------------------------


def test_migration_v1_to_v2_idempotent(tmp_path):
    """Existing v1 DB (no prune_verdicts table) migrates cleanly to v2."""
    import sqlite3 as _sqlite3

    db_path = tmp_path / "project.db"
    # Manually create a v1 DB without prune_verdicts
    conn = _sqlite3.connect(str(db_path))
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS project_sources (
            citekey TEXT PRIMARY KEY, paper_id TEXT NOT NULL
        );
        PRAGMA user_version = 1;
        """
    )
    conn.commit()
    conn.close()

    # Opening via LocalProjectStore should migrate to v2
    store2 = LocalProjectStore(db_path)
    assert store2.count_sources() == 0  # data intact

    conn2 = _sqlite3.connect(str(db_path))
    version = conn2.execute("PRAGMA user_version").fetchone()[0]
    tables = {r[0] for r in conn2.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    conn2.close()
    assert version == 6
    assert "prune_verdicts" in tables


def test_base_schema_ensured_on_reopen(tmp_path):
    """Opening an existing v2 DB re-applies base schema idempotently (no table loss)."""
    db_path = tmp_path / "project.db"
    store1 = LocalProjectStore(db_path)
    store1.set_source_sections("ck1", "pid1", ["1.1"], [1])

    # Re-open — should not corrupt existing tables
    store2 = LocalProjectStore(db_path)
    assert store2.count_sources() == 1
    assert store2.get_source_sections("ck1") == ["1.1"]


def test_save_and_get_prune_verdicts_basic(store):
    """save_prune_verdicts stores drop and maybe; get_prune_verdicts returns all."""
    store.save_prune_verdicts(
        drop=[{"citekey": "old2010", "reason": "superseded"}],
        maybe=[{"citekey": "maybe2015", "reason": "low quality"}],
    )
    items = store.get_prune_verdicts()
    source_ids = {i["source_id"] for i in items}
    assert "old2010" in source_ids
    assert "maybe2015" in source_ids


def test_save_prune_verdicts_strips_at_prefix(store):
    """citekeys prefixed with @ are stored without the prefix."""
    store.save_prune_verdicts(
        drop=[{"citekey": "@foo2020", "reason": "test"}],
        maybe=[],
    )
    items = store.get_prune_verdicts()
    assert items[0]["source_id"] == "foo2020"


def test_save_prune_verdicts_replaces_all(store):
    """Second call to save_prune_verdicts replaces previous verdicts entirely."""
    store.save_prune_verdicts(
        drop=[{"citekey": "old2010", "reason": "first run"}],
        maybe=[],
    )
    store.save_prune_verdicts(
        drop=[{"citekey": "new2020", "reason": "second run"}],
        maybe=[],
    )
    items = store.get_prune_verdicts()
    source_ids = {i["source_id"] for i in items}
    assert "new2020" in source_ids
    assert "old2010" not in source_ids


def test_get_prune_drop_ids(store):
    """get_prune_drop_ids returns only sources with verdict='drop'."""
    store.save_prune_verdicts(
        drop=[{"citekey": "drop1", "reason": "old"}, {"citekey": "drop2", "reason": "dup"}],
        maybe=[{"citekey": "maybe1", "reason": "weak"}],
    )
    drop_ids = store.get_prune_drop_ids()
    assert "drop1" in drop_ids
    assert "drop2" in drop_ids
    assert "maybe1" not in drop_ids


def test_get_prune_summary_counts(store):
    """get_prune_summary returns correct drop/maybe counts."""
    store.save_prune_verdicts(
        drop=[{"citekey": "d1"}, {"citekey": "d2"}],
        maybe=[{"citekey": "m1"}],
    )
    summary = store.get_prune_summary()
    assert summary["drop"] == 2
    assert summary["maybe"] == 1
    assert summary["total"] == 3


def test_get_prune_summary_empty(store):
    """get_prune_summary returns zeros when no verdicts stored."""
    summary = store.get_prune_summary()
    assert summary == {"drop": 0, "maybe": 0, "total": 0}


def test_get_prune_verdicts_filter_by_verdict(store):
    """get_prune_verdicts(verdict='drop') returns only drop items."""
    store.save_prune_verdicts(
        drop=[{"citekey": "dropme", "reason": "old"}],
        maybe=[{"citekey": "maybe_me"}],
    )
    drops = store.get_prune_verdicts(verdict="drop")
    maybes = store.get_prune_verdicts(verdict="maybe")
    assert all(i["verdict"] == "drop" for i in drops)
    assert all(i["verdict"] == "maybe" for i in maybes)
    assert len(drops) == 1
    assert len(maybes) == 1


def test_clear_prune_verdict(store):
    """clear_prune_verdict removes a single source's verdict."""
    store.save_prune_verdicts(
        drop=[{"citekey": "todelete", "reason": "clear me"}, {"citekey": "keep"}],
        maybe=[],
    )
    store.clear_prune_verdict("todelete")
    items = store.get_prune_verdicts()
    source_ids = {i["source_id"] for i in items}
    assert "todelete" not in source_ids
    assert "keep" in source_ids


def test_prune_verdicts_empty_citekeys_skipped(store):
    """Entries with empty citekey are silently skipped."""
    store.save_prune_verdicts(
        drop=[{"citekey": "", "reason": "invalid"}, {"citekey": "valid2020"}],
        maybe=[],
    )
    items = store.get_prune_verdicts()
    assert len(items) == 1
    assert items[0]["source_id"] == "valid2020"


# ---------------------------------------------------------------------------
# Multi-user isolation (v4 composite PK)
# ---------------------------------------------------------------------------


def test_same_citekey_different_users_no_collision(store):
    """Two users can register the same citekey without collision."""
    store.set_source_sections("shared_ck", "paper-a", ["1.1"], [1], user_id="user-A")
    store.set_source_sections("shared_ck", "paper-b", ["2.1"], [2], user_id="user-B")

    assert store.get_source_sections("shared_ck", user_id="user-A") == ["1.1"]
    assert store.get_source_sections("shared_ck", user_id="user-B") == ["2.1"]


def test_sources_by_section_user_scoped(store):
    """get_sources_by_section only returns citekeys for the requested user."""
    store.set_source_sections("src1", "p1", ["1.1"], [1], user_id="alice")
    store.set_source_sections("src2", "p2", ["1.1"], [1], user_id="bob")

    assert store.get_sources_by_section("1.1", user_id="alice") == ["src1"]
    assert store.get_sources_by_section("1.1", user_id="bob") == ["src2"]
    # Unscoped returns both
    assert set(store.get_sources_by_section("1.1")) == {"src1", "src2"}


def test_coverage_stats_user_scoped(store):
    """get_coverage_stats only counts sources for the requested user."""
    store.set_source_sections("s1", "p1", ["1.1", "1.2"], [1], user_id="u1")
    store.set_source_sections("s2", "p2", ["1.1"], [1], user_id="u2")
    store.set_source_sections("s3", "p3", ["2.1"], [2], user_id="u1")

    stats_u1 = store.get_coverage_stats(user_id="u1")
    assert stats_u1["total_sources"] == 2
    assert stats_u1["sections"].get("1.1") == 1
    assert stats_u1["sections"].get("2.1") == 1

    stats_u2 = store.get_coverage_stats(user_id="u2")
    assert stats_u2["total_sources"] == 1
    assert stats_u2["sections"].get("1.1") == 1
    assert stats_u2["sections"].get("2.1") is None


def test_count_sources_user_scoped(store):
    """count_sources respects user_id scoping."""
    store.set_source_sections("a", "p1", ["1.1"], [1], user_id="x")
    store.set_source_sections("b", "p2", ["1.1"], [1], user_id="y")
    store.set_source_sections("c", "p3", ["1.1"], [1], user_id="x")

    assert store.count_sources(user_id="x") == 2
    assert store.count_sources(user_id="y") == 1
    assert store.count_sources() == 3


# ---------------------------------------------------------------------------
# Prune verdicts — multi-user isolation (v5)
# ---------------------------------------------------------------------------


def test_prune_verdicts_user_scoped_save(store):
    """save_prune_verdicts scoped to user — doesn't wipe other user's verdicts."""
    store.save_prune_verdicts(
        drop=[{"citekey": "alice_drop", "reason": "old"}],
        maybe=[],
        user_id="alice",
    )
    store.save_prune_verdicts(
        drop=[{"citekey": "bob_drop", "reason": "dup"}],
        maybe=[{"citekey": "bob_maybe", "reason": "weak"}],
        user_id="bob",
    )
    # Alice's verdicts untouched by Bob's save
    alice_items = store.get_prune_verdicts(user_id="alice")
    bob_items = store.get_prune_verdicts(user_id="bob")
    assert {i["source_id"] for i in alice_items} == {"alice_drop"}
    assert {i["source_id"] for i in bob_items} == {"bob_drop", "bob_maybe"}


def test_prune_verdicts_same_citekey_different_users(store):
    """Same citekey can have different verdicts for different users."""
    store.save_prune_verdicts(
        drop=[{"citekey": "shared_ck", "reason": "alice says drop"}],
        maybe=[],
        user_id="alice",
    )
    store.save_prune_verdicts(
        drop=[],
        maybe=[{"citekey": "shared_ck", "reason": "bob says maybe"}],
        user_id="bob",
    )
    alice = store.get_prune_verdicts(user_id="alice")
    bob = store.get_prune_verdicts(user_id="bob")
    assert alice[0]["verdict"] == "drop"
    assert bob[0]["verdict"] == "maybe"


def test_prune_summary_user_scoped(store):
    """get_prune_summary returns counts only for the specified user."""
    store.save_prune_verdicts(
        drop=[{"citekey": "d1"}, {"citekey": "d2"}],
        maybe=[{"citekey": "m1"}],
        user_id="alice",
    )
    store.save_prune_verdicts(
        drop=[{"citekey": "d3"}],
        maybe=[],
        user_id="bob",
    )
    alice_summary = store.get_prune_summary(user_id="alice")
    bob_summary = store.get_prune_summary(user_id="bob")
    assert alice_summary == {"drop": 2, "maybe": 1, "total": 3}
    assert bob_summary == {"drop": 1, "maybe": 0, "total": 1}


def test_prune_drop_ids_user_scoped(store):
    """get_prune_drop_ids returns only the specified user's drops."""
    store.save_prune_verdicts(
        drop=[{"citekey": "a_drop"}], maybe=[], user_id="alice"
    )
    store.save_prune_verdicts(
        drop=[{"citekey": "b_drop"}], maybe=[], user_id="bob"
    )
    assert store.get_prune_drop_ids(user_id="alice") == {"a_drop"}
    assert store.get_prune_drop_ids(user_id="bob") == {"b_drop"}
    # Unscoped returns both
    assert store.get_prune_drop_ids() == {"a_drop", "b_drop"}


def test_clear_prune_verdict_user_scoped(store):
    """clear_prune_verdict only removes verdict for the specified user."""
    store.save_prune_verdicts(
        drop=[{"citekey": "shared"}], maybe=[], user_id="alice"
    )
    store.save_prune_verdicts(
        drop=[{"citekey": "shared"}], maybe=[], user_id="bob"
    )
    store.clear_prune_verdict("shared", user_id="alice")
    assert store.get_prune_verdicts(user_id="alice") == []
    assert len(store.get_prune_verdicts(user_id="bob")) == 1
