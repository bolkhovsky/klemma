"""Tests for LocalUserStore (ADR-009 Auth)."""

import sqlite3

import pytest

from klemma.stores.user_store import LocalUserStore

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def store(tmp_path) -> LocalUserStore:
    return LocalUserStore(tmp_path / "users.db")


# ---------------------------------------------------------------------------
# Schema / migration
# ---------------------------------------------------------------------------


def test_schema_version(tmp_path):
    db_path = tmp_path / "users.db"
    LocalUserStore(db_path)
    conn = sqlite3.connect(str(db_path))
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    conn.close()
    assert version == 13


def test_creates_db_file(tmp_path):
    db_path = tmp_path / "subdir" / "users.db"
    LocalUserStore(db_path)
    assert db_path.exists()


def test_fragment_curation_table_exists(tmp_path):
    db_path = tmp_path / "users.db"
    LocalUserStore(db_path)
    conn = sqlite3.connect(str(db_path))
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}
    conn.close()
    assert "fragment_curation" in tables


def _make_project(store):
    """Helper: create a user + project, return project_id."""
    user = store.create_user(email="test@test.com", password_hash="h", name="Test")
    proj = store.create_project(user.user_id, "Test Project")
    return proj["project_id"]


def test_curate_fragments(store):
    pid = _make_project(store)
    decisions = [
        {"fragment_id": "f1", "citekey": "smith2020", "verdict": "accepted", "assigned_section": "intro"},
        {"fragment_id": "f2", "citekey": "smith2020", "verdict": "rejected"},
    ]
    n = store.curate_fragments(pid, decisions)
    assert n == 2
    curated = store.get_curated(pid)
    assert len(curated) == 2
    accepted = store.get_curated(pid, verdict="accepted")
    assert len(accepted) == 1
    assert accepted[0]["fragment_id"] == "f1"
    assert accepted[0]["assigned_section"] == "intro"


def test_curation_stats(store):
    pid = _make_project(store)
    store.curate_fragments(pid, [
        {"fragment_id": "f1", "citekey": "ck1", "verdict": "accepted"},
        {"fragment_id": "f2", "citekey": "ck1", "verdict": "rejected"},
        {"fragment_id": "f3", "citekey": "ck1", "verdict": "accepted"},
    ])
    stats = store.get_curation_stats(pid, "ck1")
    assert stats["accepted"] == 2
    assert stats["rejected"] == 1
    assert stats["curated"] == 3


def test_get_curated_fragment_ids(store):
    pid = _make_project(store)
    store.curate_fragments(pid, [
        {"fragment_id": "f1", "citekey": "ck1", "verdict": "accepted"},
        {"fragment_id": "f2", "citekey": "ck1", "verdict": "rejected"},
    ])
    ids = store.get_curated_fragment_ids(pid)
    assert ids == {"f1", "f2"}


def test_update_curation(store):
    pid = _make_project(store)
    store.curate_fragments(pid, [
        {"fragment_id": "f1", "citekey": "ck1", "verdict": "accepted"},
    ])
    ok = store.update_curation(pid, "f1", note="my note", assigned_section="ch2")
    assert ok is True
    curated = store.get_curated(pid)
    assert curated[0]["note"] == "my note"
    assert curated[0]["assigned_section"] == "ch2"


def test_suggested_verdict_accepted(store):
    """verdict='suggested' is valid and queryable."""
    pid = _make_project(store)
    store.curate_fragments(pid, [
        {"fragment_id": "f1", "citekey": "ck1", "verdict": "suggested", "assigned_section": "1.1"},
    ])
    suggested = store.get_curated(pid, verdict="suggested")
    assert len(suggested) == 1
    assert suggested[0]["assigned_section"] == "1.1"


def test_suggested_excluded_from_decided_ids(store):
    """get_curated_fragment_ids returns only accepted/rejected, not suggested."""
    pid = _make_project(store)
    store.curate_fragments(pid, [
        {"fragment_id": "f1", "citekey": "ck1", "verdict": "accepted"},
        {"fragment_id": "f2", "citekey": "ck1", "verdict": "suggested"},
        {"fragment_id": "f3", "citekey": "ck1", "verdict": "rejected"},
    ])
    ids = store.get_curated_fragment_ids(pid)
    assert ids == {"f1", "f3"}


def test_curation_stats_includes_suggested(store):
    """get_curation_stats counts suggested separately."""
    pid = _make_project(store)
    store.curate_fragments(pid, [
        {"fragment_id": "f1", "citekey": "ck1", "verdict": "accepted"},
        {"fragment_id": "f2", "citekey": "ck1", "verdict": "suggested"},
        {"fragment_id": "f3", "citekey": "ck1", "verdict": "suggested"},
    ])
    stats = store.get_curation_stats(pid, "ck1")
    assert stats["accepted"] == 1
    assert stats["suggested"] == 2
    assert stats["curated"] == 3


def test_upsert_preserves_note_when_omitted(store):
    """Promoting suggested→accepted without a note keeps the existing note."""
    pid = _make_project(store)
    store.curate_fragments(pid, [
        {"fragment_id": "f1", "citekey": "ck1", "verdict": "suggested", "note": "important"},
    ])
    # Promote to accepted without sending note
    store.curate_fragments(pid, [
        {"fragment_id": "f1", "citekey": "ck1", "verdict": "accepted"},
    ])
    curated = store.get_curated(pid, verdict="accepted")
    assert len(curated) == 1
    assert curated[0]["note"] == "important"


def test_upsert_overwrites_note_when_provided(store):
    """Explicit note in upsert replaces the old one."""
    pid = _make_project(store)
    store.curate_fragments(pid, [
        {"fragment_id": "f1", "citekey": "ck1", "verdict": "suggested", "note": "old"},
    ])
    store.curate_fragments(pid, [
        {"fragment_id": "f1", "citekey": "ck1", "verdict": "accepted", "note": "new"},
    ])
    curated = store.get_curated(pid, verdict="accepted")
    assert curated[0]["note"] == "new"


# ---------------------------------------------------------------------------
# Suggested sentences (ADR-017, schema v13)
# ---------------------------------------------------------------------------


def test_v13_columns_exist(tmp_path):
    db_path = tmp_path / "users.db"
    LocalUserStore(db_path)
    conn = sqlite3.connect(str(db_path))
    cols = {r[1] for r in conn.execute("PRAGMA table_info(fragment_curation)").fetchall()}
    conn.close()
    assert "suggested_text" in cols
    assert "sentence_model" in cols


def test_v13_migration_idempotent(tmp_path):
    db_path = tmp_path / "users.db"
    LocalUserStore(db_path)
    LocalUserStore(db_path)  # second run — must not error
    conn = sqlite3.connect(str(db_path))
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    conn.close()
    assert version == 13


def test_suggested_text_roundtrip(store):
    pid = _make_project(store)
    store.curate_fragments(pid, [
        {
            "fragment_id": "f1",
            "citekey": "kvanum2024",
            "verdict": "suggested",
            "assigned_section": "3.2",
            "suggested_text": "Кванум и др. показали, что методы глубокого обучения превосходят динамические модели [@kvanum2024].",
            "sentence_model": "anthropic/claude-sonnet-4-20250514",
        },
    ])
    curated = store.get_curated(pid)
    assert curated[0]["suggested_text"].startswith("Кванум")
    assert curated[0]["sentence_model"] == "anthropic/claude-sonnet-4-20250514"


def test_suggested_text_null_for_legacy_rows(store):
    """Rows written without the new fields expose None gracefully."""
    pid = _make_project(store)
    store.curate_fragments(pid, [
        {"fragment_id": "f1", "citekey": "ck1", "verdict": "accepted"},
    ])
    curated = store.get_curated(pid)
    assert curated[0]["suggested_text"] is None
    assert curated[0]["sentence_model"] is None


def test_update_curation_overwrites_suggested_text(store):
    """User edits the textarea; update_curation stores the final value verbatim."""
    pid = _make_project(store)
    store.curate_fragments(pid, [
        {
            "fragment_id": "f1",
            "citekey": "ck1",
            "verdict": "suggested",
            "suggested_text": "Original AI sentence.",
            "sentence_model": "anthropic/claude-sonnet-4-20250514",
        },
    ])
    ok = store.update_curation(
        pid,
        "f1",
        verdict="accepted",
        suggested_text="User edited sentence.",
    )
    assert ok is True
    curated = store.get_curated(pid, verdict="accepted")
    assert curated[0]["suggested_text"] == "User edited sentence."
    # sentence_model untouched
    assert curated[0]["sentence_model"] == "anthropic/claude-sonnet-4-20250514"


def test_upsert_preserves_suggested_text_when_omitted(store):
    """Re-upserting without sentence fields keeps the stored value."""
    pid = _make_project(store)
    store.curate_fragments(pid, [
        {
            "fragment_id": "f1",
            "citekey": "ck1",
            "verdict": "suggested",
            "suggested_text": "Keep me.",
            "sentence_model": "anthropic/claude-sonnet-4-20250514",
        },
    ])
    store.curate_fragments(pid, [
        {"fragment_id": "f1", "citekey": "ck1", "verdict": "accepted"},
    ])
    curated = store.get_curated(pid, verdict="accepted")
    assert curated[0]["suggested_text"] == "Keep me."
    assert curated[0]["sentence_model"] == "anthropic/claude-sonnet-4-20250514"


# ---------------------------------------------------------------------------
# create_user
# ---------------------------------------------------------------------------


def test_create_user(store):
    user = store.create_user(email="a@b.com", password_hash="hash1", name="Alice")
    assert user.email == "a@b.com"
    assert user.name == "Alice"
    assert user.password_hash == "hash1"
    assert user.email_verified is False
    assert user.user_id  # non-empty


def test_create_duplicate_email_raises(store):
    store.create_user(email="a@b.com", password_hash="h1")
    with pytest.raises(ValueError, match="already exists"):
        store.create_user(email="a@b.com", password_hash="h2")


# ---------------------------------------------------------------------------
# get_user_by_email / get_user_by_id
# ---------------------------------------------------------------------------


def test_get_user_by_email(store):
    created = store.create_user(email="x@y.com", password_hash="h")
    found = store.get_user_by_email("x@y.com")
    assert found is not None
    assert found.user_id == created.user_id


def test_get_user_by_email_not_found(store):
    assert store.get_user_by_email("no@one.com") is None


def test_get_user_by_id(store):
    created = store.create_user(email="x@y.com", password_hash="h")
    found = store.get_user_by_id(created.user_id)
    assert found is not None
    assert found.email == "x@y.com"


def test_get_user_by_id_not_found(store):
    assert store.get_user_by_id("nonexistent") is None


# ---------------------------------------------------------------------------
# update_user
# ---------------------------------------------------------------------------


def test_update_name(store):
    user = store.create_user(email="a@b.com", password_hash="h")
    assert store.update_user(user.user_id, name="Bob")
    updated = store.get_user_by_id(user.user_id)
    assert updated is not None
    assert updated.name == "Bob"


def test_update_email_verified(store):
    user = store.create_user(email="a@b.com", password_hash="h")
    assert store.update_user(user.user_id, email_verified=True)
    updated = store.get_user_by_id(user.user_id)
    assert updated is not None
    assert updated.email_verified is True


def test_update_nonexistent_user(store):
    assert store.update_user("nouser", name="X") is False


# ---------------------------------------------------------------------------
# Refresh tokens
# ---------------------------------------------------------------------------


def test_store_and_verify_refresh_token(store):
    user = store.create_user(email="a@b.com", password_hash="h")
    store.store_refresh_token(user.user_id, "tokenhash1", "2099-01-01T00:00:00")
    assert store.verify_refresh_token(user.user_id, "tokenhash1")


def test_expired_refresh_token_not_valid(store):
    user = store.create_user(email="a@b.com", password_hash="h")
    store.store_refresh_token(user.user_id, "tokenhash1", "2000-01-01T00:00:00")
    assert not store.verify_refresh_token(user.user_id, "tokenhash1")


def test_wrong_hash_not_valid(store):
    user = store.create_user(email="a@b.com", password_hash="h")
    store.store_refresh_token(user.user_id, "tokenhash1", "2099-01-01T00:00:00")
    assert not store.verify_refresh_token(user.user_id, "wronghash")


def test_revoke_refresh_tokens(store):
    user = store.create_user(email="a@b.com", password_hash="h")
    store.store_refresh_token(user.user_id, "t1", "2099-01-01T00:00:00")
    store.store_refresh_token(user.user_id, "t2", "2099-01-01T00:00:00")
    count = store.revoke_refresh_tokens(user.user_id)
    assert count == 2
    assert not store.verify_refresh_token(user.user_id, "t1")
    assert not store.verify_refresh_token(user.user_id, "t2")


# ---------------------------------------------------------------------------
# Project management
# ---------------------------------------------------------------------------


def test_create_project(store):
    user = store.create_user(email="p@example.com", password_hash="h")
    project = store.create_project(user.user_id, "My Dissertation")
    assert project["project_id"]
    assert project["name"] == "My Dissertation"
    assert project["type"] == "dissertation"
    assert project["user_id"] == user.user_id


def test_get_projects_empty(store):
    user = store.create_user(email="p@example.com", password_hash="h")
    assert store.get_projects(user.user_id) == []


def test_get_projects_multiple(store):
    user = store.create_user(email="p@example.com", password_hash="h")
    store.create_project(user.user_id, "Dissertation", "dissertation")
    store.create_project(user.user_id, "Paper 1", "article")
    projects = store.get_projects(user.user_id)
    assert len(projects) == 2
    names = {p["name"] for p in projects}
    assert names == {"Dissertation", "Paper 1"}


def test_get_project_by_id(store):
    user = store.create_user(email="p@example.com", password_hash="h")
    created = store.create_project(user.user_id, "Test Project")
    fetched = store.get_project_by_id(created["project_id"])
    assert fetched is not None
    assert fetched["name"] == "Test Project"


def test_get_project_by_id_missing(store):
    assert store.get_project_by_id("nonexistent") is None


def test_rename_project(store):
    user = store.create_user(email="p@example.com", password_hash="h")
    project = store.create_project(user.user_id, "Old Name")
    assert store.rename_project(project["project_id"], "New Name") is True
    updated = store.get_project_by_id(project["project_id"])
    assert updated["name"] == "New Name"


def test_projects_isolated_between_users(store):
    u1 = store.create_user(email="u1@example.com", password_hash="h")
    u2 = store.create_user(email="u2@example.com", password_hash="h")
    store.create_project(u1.user_id, "User1 Project")
    assert store.get_projects(u2.user_id) == []


def test_project_outline_default_null(store):
    user = store.create_user(email="outline@example.com", password_hash="h")
    project = store.create_project(user.user_id, "Test")
    assert project.get("outline") is None
    fetched = store.get_project_by_id(project["project_id"])
    assert fetched["outline"] is None


def test_update_project_outline(store):
    user = store.create_user(email="outline2@example.com", password_hash="h")
    project = store.create_project(user.user_id, "Test")
    sections = [{"id": "1.1", "name": "Введение"}, {"id": "1.2", "name": "Обзор"}]
    result = store.update_project_outline(project["project_id"], sections)
    assert result is True
    fetched = store.get_project_by_id(project["project_id"])
    assert fetched["outline"] == sections


def test_outline_in_project_list(store):
    user = store.create_user(email="outline3@example.com", password_hash="h")
    project = store.create_project(user.user_id, "Test")
    sections = [{"id": "2.1", "name": "Методология"}]
    store.update_project_outline(project["project_id"], sections)
    projects = store.get_projects(user.user_id)
    assert projects[0]["outline"] == sections


def test_delete_project(store):
    user = store.create_user(email="del@example.com", password_hash="h")
    project = store.create_project(user.user_id, "To Delete")
    pid = project["project_id"]

    result = store.delete_project(pid)

    assert result is True
    assert store.get_project_by_id(pid) is None
    assert store.get_projects(user.user_id) == []


def test_delete_project_nonexistent(store):
    result = store.delete_project("does-not-exist")
    assert result is False


def test_delete_project_cascades_research_reports(store):
    import json

    user = store.create_user(email="cascade@example.com", password_hash="h")
    project = store.create_project(user.user_id, "Cascade Test")
    pid = project["project_id"]

    # Insert a research report directly
    store.save_research_report(
        pid, "1.1", json.dumps({"blocks": []}), "report text", "test-model"
    )

    # Verify it exists
    assert store.get_research_report(pid, "1.1") is not None

    # Delete project — research_reports should cascade
    store.delete_project(pid)

    # Project gone
    assert store.get_project_by_id(pid) is None
    # Research report also gone (ON DELETE CASCADE)
    assert store.get_research_report(pid, "1.1") is None


# ---------------------------------------------------------------------------
# git_project_id (schema v10, issue #260 item 5)
# ---------------------------------------------------------------------------


def test_schema_v10_git_project_id_column(tmp_path):
    """Schema v10 adds git_project_id column to projects table."""
    import sqlite3

    db_path = tmp_path / "v10.db"
    LocalUserStore(db_path)
    conn = sqlite3.connect(str(db_path))
    cols = {row[1] for row in conn.execute("PRAGMA table_info(projects)").fetchall()}
    conn.close()
    assert "git_project_id" in cols


def test_get_project_by_git_id_found(store):
    """get_project_by_git_id returns project dict when association is set."""
    user = store.create_user(email="gitid@example.com", password_hash="h")
    project = store.create_project(user.user_id, "My Dissertation")
    pid = project["project_id"]

    store.set_git_project_id(pid, "username/dissertation")

    result = store.get_project_by_git_id("username/dissertation")
    assert result is not None
    assert result["project_id"] == pid


def test_get_project_by_git_id_not_found(store):
    """get_project_by_git_id returns None when no association exists."""
    assert store.get_project_by_git_id("nobody/nothing") is None


def test_set_git_project_id_updates_existing(store):
    """set_git_project_id can be called twice — updates the association."""
    user = store.create_user(email="gitid2@example.com", password_hash="h")
    project = store.create_project(user.user_id, "Test")
    pid = project["project_id"]

    store.set_git_project_id(pid, "user/old-name")
    store.set_git_project_id(pid, "user/new-name")

    assert store.get_project_by_git_id("user/new-name") is not None
    # Old association no longer resolvable
    assert store.get_project_by_git_id("user/old-name") is None


def test_git_project_id_unique_per_project(store):
    """Each project can have at most one git_project_id."""
    user = store.create_user(email="gitid3@example.com", password_hash="h")
    p1 = store.create_project(user.user_id, "Proj1")
    p2 = store.create_project(user.user_id, "Proj2")

    store.set_git_project_id(p1["project_id"], "user/proj1")
    store.set_git_project_id(p2["project_id"], "user/proj2")

    r1 = store.get_project_by_git_id("user/proj1")
    r2 = store.get_project_by_git_id("user/proj2")
    assert r1["project_id"] == p1["project_id"]
    assert r2["project_id"] == p2["project_id"]
