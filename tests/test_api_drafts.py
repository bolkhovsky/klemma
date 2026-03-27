"""Tests for file-based draft endpoints (drafts.py).

Covers:
- GET  /projects/{id}/drafts              (list)
- GET  /projects/{id}/drafts/{filename}   (get)
- PUT  /projects/{id}/drafts/{filename}   (save)
- DELETE /projects/{id}/drafts/{filename} (delete — #260 item 6)
- PUT  /projects/{id}/drafts/{filename}/sections/{sec_id}  (upsert section)
"""

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
    """Client with a pre-created project."""
    import sqlite3

    from klemma.api.app import create_app
    from klemma.api.auth.deps import get_current_user

    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: mock_user

    with TestClient(app) as c:
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


class TestListDraftFiles:
    def test_list_empty_project(self, client_and_project):
        client, project_id = client_and_project
        resp = client.get(f"/projects/{project_id}/drafts")
        assert resp.status_code == 200
        assert resp.json()["files"] == []

    def test_list_after_save(self, client_and_project):
        client, project_id = client_and_project
        client.put(f"/projects/{project_id}/drafts/chapter_1.md",
                   json={"content": "## 1 Введение\n\nТекст главы."})
        resp = client.get(f"/projects/{project_id}/drafts")
        assert resp.status_code == 200
        files = resp.json()["files"]
        assert len(files) == 1
        assert files[0]["name"] == "chapter_1.md"
        assert files[0]["word_count"] > 0

    def test_list_wrong_project_returns_404(self, client_and_project):
        client, _ = client_and_project
        resp = client.get("/projects/nonexistent/drafts")
        assert resp.status_code == 404


class TestGetDraftFile:
    def test_get_existing_file(self, client_and_project):
        client, project_id = client_and_project
        content = "## 1 Глава\n\n### 1.1 Подраздел\n\nТекст."
        client.put(f"/projects/{project_id}/drafts/chapter_1.md",
                   json={"content": content})
        resp = client.get(f"/projects/{project_id}/drafts/chapter_1.md")
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "chapter_1.md"
        assert data["content"] == content

    def test_get_nonexistent_file_returns_404(self, client_and_project):
        client, project_id = client_and_project
        resp = client.get(f"/projects/{project_id}/drafts/missing.md")
        assert resp.status_code == 404

    def test_invalid_filename_returns_400(self, client_and_project):
        # Starlette resolves /../ before the handler, so path traversal attempts
        # are either blocked with 400 at the handler or 404 at the router level.
        client, project_id = client_and_project
        resp = client.get(f"/projects/{project_id}/drafts/../secret.md")
        assert resp.status_code in (400, 404, 422)


class TestSaveDraftFile:
    def test_save_creates_file(self, client_and_project):
        client, project_id = client_and_project
        resp = client.put(f"/projects/{project_id}/drafts/intro.md",
                          json={"content": "# Введение\n\nТекст."})
        assert resp.status_code == 200
        assert resp.json()["name"] == "intro.md"

    def test_save_idempotent(self, client_and_project):
        client, project_id = client_and_project
        client.put(f"/projects/{project_id}/drafts/intro.md",
                   json={"content": "v1"})
        resp = client.put(f"/projects/{project_id}/drafts/intro.md",
                          json={"content": "v2"})
        assert resp.status_code == 200
        assert resp.json()["content"] == "v2"


class TestDeleteDraftFile:
    """Tests for DELETE /projects/{id}/drafts/{filename} — issue #260 item 6."""

    def test_delete_existing_file(self, client_and_project):
        """DELETE returns 204 and file is gone."""
        client, project_id = client_and_project
        # Create the file first
        client.put(f"/projects/{project_id}/drafts/chapter_1.md",
                   json={"content": "## 1 Глава\n\nТекст."})

        resp = client.delete(f"/projects/{project_id}/drafts/chapter_1.md")
        assert resp.status_code == 204

        # Confirm gone
        get_resp = client.get(f"/projects/{project_id}/drafts/chapter_1.md")
        assert get_resp.status_code == 404

    def test_delete_nonexistent_file_returns_404(self, client_and_project):
        """DELETE on missing file returns 404."""
        client, project_id = client_and_project
        resp = client.delete(f"/projects/{project_id}/drafts/missing.md")
        assert resp.status_code == 404

    def test_delete_invalid_filename_returns_400(self, client_and_project):
        """DELETE with path-traversal filename is rejected (400 or router-level 404)."""
        client, project_id = client_and_project
        resp = client.delete(f"/projects/{project_id}/drafts/../etc.md")
        assert resp.status_code in (400, 404, 422)

    def test_delete_wrong_project_returns_404(self, client_and_project):
        """DELETE on another user's project is rejected."""
        client, _ = client_and_project
        resp = client.delete("/projects/nonexistent/drafts/chapter_1.md")
        assert resp.status_code == 404

    def test_delete_removes_from_list(self, client_and_project):
        """After DELETE, file no longer appears in list endpoint."""
        client, project_id = client_and_project
        client.put(f"/projects/{project_id}/drafts/chapter_1.md",
                   json={"content": "text"})
        client.put(f"/projects/{project_id}/drafts/chapter_2.md",
                   json={"content": "text"})

        client.delete(f"/projects/{project_id}/drafts/chapter_1.md")

        resp = client.get(f"/projects/{project_id}/drafts")
        names = [f["name"] for f in resp.json()["files"]]
        assert "chapter_1.md" not in names
        assert "chapter_2.md" in names

    def test_delete_idempotent_second_call_returns_404(self, client_and_project):
        """Second DELETE on the same file returns 404."""
        client, project_id = client_and_project
        client.put(f"/projects/{project_id}/drafts/chapter_1.md",
                   json={"content": "text"})
        client.delete(f"/projects/{project_id}/drafts/chapter_1.md")
        resp = client.delete(f"/projects/{project_id}/drafts/chapter_1.md")
        assert resp.status_code == 404


class TestSectionUpsert:
    def test_upsert_appends_section_to_empty_file(self, client_and_project):
        client, project_id = client_and_project
        # Create empty file first
        client.put(f"/projects/{project_id}/drafts/chapter_1.md",
                   json={"content": ""})
        resp = client.put(
            f"/projects/{project_id}/drafts/chapter_1.md/sections/1.1",
            json={"body": "Текст подраздела.", "heading_title": "Введение"},
        )
        assert resp.status_code == 200
        assert resp.json()["section_id"] == "1.1"

    def test_upsert_replaces_section_body(self, client_and_project):
        client, project_id = client_and_project
        initial = "## 1 Глава\n\n### 1.1 Подраздел\n\nСтарый текст.\n"
        client.put(f"/projects/{project_id}/drafts/chapter_1.md",
                   json={"content": initial})
        client.put(
            f"/projects/{project_id}/drafts/chapter_1.md/sections/1.1",
            json={"body": "Новый текст.", "heading_title": "Подраздел"},
        )
        get_resp = client.get(f"/projects/{project_id}/drafts/chapter_1.md")
        assert "Новый текст." in get_resp.json()["content"]
        assert "Старый текст." not in get_resp.json()["content"]
