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


def test_schema_version_is_4(tmp_path):
    db_path = tmp_path / "users.db"
    LocalUserStore(db_path)
    conn = sqlite3.connect(str(db_path))
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    conn.close()
    assert version == 4


def test_creates_db_file(tmp_path):
    db_path = tmp_path / "subdir" / "users.db"
    LocalUserStore(db_path)
    assert db_path.exists()


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
