"""Tests for block draft endpoints in the projects router."""

from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def data_dir(tmp_path):
    d = tmp_path / "klemma_data"
    d.mkdir()
    os.environ["KLEMMA_DATA_DIR"] = str(d)
    yield d
    os.environ.pop("KLEMMA_DATA_DIR", None)


@pytest.fixture
def mock_user():
    from klemma.models import UserRecord

    return UserRecord(
        user_id="test-user-123",
        email="test@example.com",
        password_hash="xxx",
        name="Test User",
        username="test-user",
    )


@pytest.fixture
def client_and_project(data_dir, mock_user):
    """Client with a pre-created project.

    Registers the mock user in the store (needed for FK constraint on projects table)
    then creates a project via the API.
    """
    from klemma.api.app import create_app
    from klemma.api.auth.deps import get_current_user, get_user_store

    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: mock_user

    with TestClient(app) as c:
        # Insert the mock user into the DB (lifespan has run, store is ready)
        store = get_user_store()
        import sqlite3
        db_path = data_dir / "users.db"
        with sqlite3.connect(str(db_path)) as conn:
            conn.execute(
                "INSERT OR IGNORE INTO users (user_id, email, password_hash, name) VALUES (?, ?, ?, ?)",
                (mock_user.user_id, mock_user.email, mock_user.password_hash, mock_user.name),
            )

        resp = c.post("/projects", json={"name": "Test Project", "type": "dissertation"})
        assert resp.status_code == 201
        project_id = resp.json()["project_id"]
        yield c, project_id


class TestBlockDraftStatus:
    def test_status_empty_no_drafts_dir(self, client_and_project):
        """Status endpoint returns empty dict when no drafts exist."""
        client, project_id = client_and_project
        resp = client.get(f"/projects/{project_id}/blocks/status")
        assert resp.status_code == 200
        assert resp.json() == {"statuses": {}}

    def test_status_after_save(self, client_and_project):
        """Status reflects a saved draft."""
        client, project_id = client_and_project

        # Save a draft
        client.put(f"/projects/{project_id}/blocks/1.1/b1", json={"text": "Hello world"})

        resp = client.get(f"/projects/{project_id}/blocks/status")
        assert resp.status_code == 200
        statuses = resp.json()["statuses"]
        assert "1.1/b1" in statuses
        assert statuses["1.1/b1"]["has_draft"] is True
        assert statuses["1.1/b1"]["word_count"] == 2

    def test_status_empty_text_not_draft(self, client_and_project):
        """A saved draft with empty text is not counted as has_draft."""
        client, project_id = client_and_project

        client.put(f"/projects/{project_id}/blocks/2.1/b1", json={"text": ""})

        resp = client.get(f"/projects/{project_id}/blocks/status")
        assert resp.status_code == 200
        statuses = resp.json()["statuses"]
        # File exists but is empty — has_draft should be False
        entry = statuses.get("2.1/b1")
        if entry is not None:
            assert entry["has_draft"] is False

    def test_status_multiple_sections(self, client_and_project):
        """Status returns all saved drafts across sections."""
        client, project_id = client_and_project

        client.put(f"/projects/{project_id}/blocks/1.1/b1", json={"text": "Section 1.1"})
        client.put(f"/projects/{project_id}/blocks/2.3/b1", json={"text": "Section 2.3"})

        resp = client.get(f"/projects/{project_id}/blocks/status")
        assert resp.status_code == 200
        statuses = resp.json()["statuses"]
        assert "1.1/b1" in statuses
        assert "2.3/b1" in statuses
        assert statuses["1.1/b1"]["has_draft"] is True
        assert statuses["2.3/b1"]["has_draft"] is True

    def test_status_wrong_project_returns_404(self, client_and_project):
        """Status endpoint rejects access to non-existent project."""
        client, _ = client_and_project
        resp = client.get("/projects/nonexistent-project/blocks/status")
        assert resp.status_code == 404
