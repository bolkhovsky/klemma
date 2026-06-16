"""Tests for the sync API router."""

from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient


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
def client(mock_user):
    """Create a test client with mocked auth and initialized stores."""
    from klemma.api.app import create_app
    from klemma.api.auth.deps import get_current_user

    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: mock_user

    # Use lifespan context to initialize stores
    with TestClient(app) as c:
        yield c


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

    def test_push_and_pull_library_preserves_verbatim(self, client):
        token = uuid4().hex
        citekey = f"verbatim-{token}"
        paper_id = f"local-paper-{token}"
        fragment_id = f"frag-verbatim-{token}"
        push_resp = client.post("/sync/push/library", json={
            "sources": [
                {
                    "citekey": citekey,
                    "paper_id": paper_id,
                    "title": "Quoted Paper",
                    "status": "completed",
                }
            ],
            "fragments": [
                {
                    "fragment_id": fragment_id,
                    "paper_id": paper_id,
                    "text": "Exact quoted fragment.",
                    "fragment_type": "quote",
                    "citation_intent": "result",
                    "page": 7,
                    "verbatim": True,
                }
            ],
        })
        assert push_resp.status_code == 200
        assert push_resp.json()["fragments_saved"] == 1

        pull_resp = client.get("/sync/pull/library")
        assert pull_resp.status_code == 200
        fragments = pull_resp.json()["fragments"]
        fragment = next(f for f in fragments if f["fragment_id"] == fragment_id)
        assert fragment["verbatim"] is True

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


class TestIncrementalPullLibrary:
    """Tests for incremental pull with 'since' parameter — issue #260 item 7."""

    def test_pull_without_since_returns_all(self, client):
        """No since parameter returns all sources."""
        client.post("/sync/push/library", json={
            "sources": [
                {"citekey": "incr-a", "title": "Alpha", "status": "pending"},
                {"citekey": "incr-b", "title": "Beta", "status": "pending"},
            ],
            "fragments": [],
        })
        resp = client.get("/sync/pull/library")
        assert resp.status_code == 200
        citekeys = {s["citekey"] for s in resp.json()["sources"]}
        assert "incr-a" in citekeys
        assert "incr-b" in citekeys

    def test_pull_with_future_since_returns_empty(self, client):
        """since=far-future returns zero sources (everything is older)."""
        client.post("/sync/push/library", json={
            "sources": [{"citekey": "incr-old", "title": "Old", "status": "pending"}],
            "fragments": [],
        })
        resp = client.get("/sync/pull/library", params={"since": "2099-01-01T00:00:00+00:00"})
        assert resp.status_code == 200
        assert resp.json()["sources"] == []

    def test_pull_with_epoch_since_returns_all(self, client):
        """since=epoch (far past) returns all sources."""
        client.post("/sync/push/library", json={
            "sources": [{"citekey": "incr-epoch", "title": "Old", "status": "pending"}],
            "fragments": [],
        })
        resp = client.get("/sync/pull/library", params={"since": "2000-01-01T00:00:00+00:00"})
        assert resp.status_code == 200
        citekeys = {s["citekey"] for s in resp.json()["sources"]}
        assert "incr-epoch" in citekeys
