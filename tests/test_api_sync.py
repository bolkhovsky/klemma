"""Tests for the sync API router."""

from __future__ import annotations

import os
import shutil
import subprocess

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def data_dir(tmp_path):
    """Create a temporary data directory for repos."""
    d = tmp_path / "klemma_data"
    d.mkdir()
    os.environ["KLEMMA_DATA_DIR"] = str(d)
    yield d
    os.environ.pop("KLEMMA_DATA_DIR", None)


@pytest.fixture
def mock_user():
    """Create a mock user record."""
    from klemma.models import UserRecord

    return UserRecord(
        user_id="test-user-123",
        email="test@example.com",
        password_hash="xxx",
        name="Test User",
        username="test-user",
    )


@pytest.fixture
def client(data_dir, mock_user):
    """Create a test client with mocked auth and initialized stores."""
    from klemma.api.app import create_app
    from klemma.api.auth.deps import get_current_user

    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: mock_user

    # Use lifespan context to initialize stores
    with TestClient(app) as c:
        yield c


class TestInitRepo:
    def test_init_creates_bare_repo(self, client, data_dir):
        resp = client.post(
            "/sync/init-repo",
            json={"project_id": "my-project"},
        )
        assert resp.status_code == 201
        data = resp.json()
        # project_id is namespaced as username/project-name
        assert data["project_id"] == "test-user/my-project"
        assert "git_url" in data
        assert "access_token" in data
        assert "dashboard_project_id" in data  # field present (may be "" when user not in store)

        # Verify bare repo was created (namespaced path)
        repo_path = data_dir / "repos" / "test-user" / "my-project"
        assert repo_path.exists()
        assert (repo_path / "HEAD").exists()  # bare repo indicator
        assert (repo_path / "klemma_owner").read_text() == "test-user-123"

    def test_init_rejects_duplicate(self, client, data_dir):
        client.post("/sync/init-repo", json={"project_id": "dup-project"})
        resp = client.post("/sync/init-repo", json={"project_id": "dup-project"})
        assert resp.status_code == 409

    def test_init_rejects_path_traversal(self, client):
        resp = client.post("/sync/init-repo", json={"project_id": "../escape"})
        assert resp.status_code == 400


class TestDashboardProject:
    def test_dashboard_project_not_found(self, client):
        resp = client.get("/sync/dashboard-project", params={"project_id": "nonexistent/project"})
        assert resp.status_code == 404


class TestFileEndpoints:
    def _create_repo_with_file(self, data_dir, project_id, file_path, content):
        """Helper: create a bare repo with a file committed."""
        repo = data_dir / "repos" / project_id
        repo.mkdir(parents=True)
        subprocess.run(["git", "init", "--bare"], cwd=str(repo), capture_output=True)
        (repo / "klemma_owner").write_text("test-user-123")
        (repo / "klemma_token").write_text("test-token")

        # Create a temporary working copy to commit a file
        work = data_dir / "tmp_work"
        work.mkdir()
        subprocess.run(["git", "clone", str(repo), str(work)], capture_output=True)
        (work / file_path).parent.mkdir(parents=True, exist_ok=True)
        (work / file_path).write_text(content)
        subprocess.run(["git", "add", "."], cwd=str(work), capture_output=True)
        env = os.environ.copy()
        env["GIT_AUTHOR_NAME"] = "Test"
        env["GIT_AUTHOR_EMAIL"] = "test@test.com"
        env["GIT_COMMITTER_NAME"] = "Test"
        env["GIT_COMMITTER_EMAIL"] = "test@test.com"
        subprocess.run(
            ["git", "commit", "-m", "init"],
            cwd=str(work), capture_output=True, env=env,
        )
        subprocess.run(
            ["git", "push", "origin", "master"],
            cwd=str(work), capture_output=True,
        )
        shutil.rmtree(work)

    def test_get_file(self, client, data_dir):
        self._create_repo_with_file(data_dir, "test-proj", "KLEMMA.md", "# Test Project")
        resp = client.get("/sync/file/test-proj", params={"file_path": "KLEMMA.md"})
        assert resp.status_code == 200
        assert "# Test Project" in resp.json()["content"]

    def test_get_file_not_found(self, client, data_dir):
        self._create_repo_with_file(data_dir, "test-proj2", "KLEMMA.md", "test")
        resp = client.get("/sync/file/test-proj2", params={"file_path": "nonexistent.md"})
        assert resp.status_code == 404

    def test_get_file_wrong_user(self, client, data_dir):
        repo = data_dir / "repos" / "other-proj"
        repo.mkdir(parents=True)
        subprocess.run(["git", "init", "--bare"], cwd=str(repo), capture_output=True)
        (repo / "klemma_owner").write_text("other-user-456")
        resp = client.get("/sync/file/other-proj", params={"file_path": "KLEMMA.md"})
        assert resp.status_code == 403

    def test_get_history(self, client, data_dir):
        self._create_repo_with_file(data_dir, "hist-proj", "test.md", "content")
        resp = client.get("/sync/history/hist-proj")
        assert resp.status_code == 200
        entries = resp.json()
        assert len(entries) >= 1
        assert entries[0]["message"] == "init"

    def test_history_empty_repo(self, client, data_dir):
        namespaced = "test-use-empty-proj"
        repo = data_dir / "repos" / namespaced
        repo.mkdir(parents=True)
        subprocess.run(["git", "init", "--bare"], cwd=str(repo), capture_output=True)
        (repo / "klemma_owner").write_text("test-user-123")
        resp = client.get(f"/sync/history/{namespaced}")
        assert resp.status_code == 200
        assert resp.json() == []


class TestCommit:
    def test_commit_new_file(self, client, data_dir):
        # Create empty bare repo
        repo = data_dir / "repos" / "commit-proj"
        repo.mkdir(parents=True)
        subprocess.run(["git", "init", "--bare"], cwd=str(repo), capture_output=True)
        (repo / "klemma_owner").write_text("test-user-123")

        resp = client.post("/sync/commit/commit-proj", json={
            "file_path": "notes/test.md",
            "content": "# Hello World",
            "message": "test commit",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert "commit_hash" in data
        assert data["message"] == "test commit"

        # Verify file is readable
        read_resp = client.get("/sync/file/commit-proj", params={"file_path": "notes/test.md"})
        assert read_resp.status_code == 200
        assert "Hello World" in read_resp.json()["content"]


class TestRollback:
    def test_rollback_rejects_too_many(self, client, data_dir):
        repo = data_dir / "repos" / "rb-proj"
        repo.mkdir(parents=True)
        subprocess.run(["git", "init", "--bare"], cwd=str(repo), capture_output=True)
        (repo / "klemma_owner").write_text("test-user-123")

        # Commit one file
        client.post("/sync/commit/rb-proj", json={
            "file_path": "test.md", "content": "v1",
        })

        resp = client.post("/sync/rollback/rb-proj", json={"steps": 5})
        assert resp.status_code == 400


class TestLibrarySync:
    def test_push_library(self, client):
        resp = client.post("/sync/push/library", json={
            "sources": [
                {
                    "citekey": "smith2022",
                    "title": "Test Paper",
                    "authors": "Smith",
                    "year": 2022,
                    "status": "completed",
                }
            ],
            "fragments": [],
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["sources_saved"] == 1

    def test_pull_library(self, client):
        # Push first
        client.post("/sync/push/library", json={
            "sources": [{"citekey": "jones2023", "title": "Another Paper", "status": "pending"}],
            "fragments": [],
        })

        # Pull
        resp = client.get("/sync/pull/library")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["sources"]) >= 1

    def test_push_embeddings(self, client):
        import base64
        import struct

        # First push a source so the paper_id exists
        client.post("/sync/push/library", json={
            "sources": [{"citekey": "emb-test", "title": "Embedding Test", "status": "completed"}],
            "fragments": [],
        })
        # Get the paper_id from a pull
        pull_resp = client.get("/sync/pull/library")
        sources = pull_resp.json()["sources"]
        paper_id = next(s["paper_id"] for s in sources if s["citekey"] == "emb-test")

        vector = struct.pack("3f", 0.1, 0.2, 0.3)
        b64 = base64.b64encode(vector).decode()

        resp = client.post("/sync/push/embeddings", json={
            "paper_embeddings": [{"id": paper_id, "vector_b64": b64, "model": "specter2"}],
            "fragment_embeddings": [],
        })
        assert resp.status_code == 200
        assert resp.json()["paper_embeddings_saved"] == 1


class TestVerifyGitToken:
    def test_valid_token(self, client, data_dir):
        repo = data_dir / "repos" / "token-proj"
        repo.mkdir(parents=True)
        (repo / "klemma_token").write_text("secret-token-123")

        resp = client.get("/sync/verify-git-token", params={
            "token": "secret-token-123",
            "project_id": "token-proj",
        })
        assert resp.status_code == 200

    def test_invalid_token(self, client, data_dir):
        repo = data_dir / "repos" / "token-proj2"
        repo.mkdir(parents=True)
        (repo / "klemma_token").write_text("correct-token")

        resp = client.get("/sync/verify-git-token", params={
            "token": "wrong-token",
            "project_id": "token-proj2",
        })
        assert resp.status_code == 401
