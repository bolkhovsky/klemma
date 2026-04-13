"""Tests for usage tracking API endpoints (#202)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from klemma.api.app import create_app
from klemma.api.auth.deps import set_user_store
from klemma.api.deps import set_file_store, set_paper_store, set_project_store, set_user_library
from klemma.api.rate_limit import reset_rate_limiter
from klemma.stores.file_store import LocalFileStore
from klemma.stores.paper_store import LocalPaperStore
from klemma.stores.project_store import LocalProjectStore
from klemma.stores.user_library import LocalUserLibrary
from klemma.stores.user_store import LocalUserStore


@pytest.fixture
def stores(tmp_path):
    user_store = LocalUserStore(tmp_path / "users.db")
    library_db = tmp_path / "library.db"
    paper_store = LocalPaperStore(library_db)
    user_library = LocalUserLibrary(library_db)
    project_store = LocalProjectStore(tmp_path / "project.db")
    file_store = LocalFileStore(tmp_path / "files")
    return user_store, paper_store, user_library, project_store, file_store


@pytest.fixture
def client(stores, monkeypatch) -> TestClient:
    # Disable the initial token grant so usage API tests start from a clean
    # 0/0/0 balance. The grant behavior itself is covered in test_auth_api.py.
    monkeypatch.setenv("KLEMMA_INITIAL_TOKEN_GRANT", "0")
    user_store, paper_store, user_library, project_store, file_store = stores
    app = create_app()
    set_user_store(user_store)
    set_paper_store(paper_store)
    set_user_library(user_library)
    set_project_store(project_store)
    set_file_store(file_store)
    reset_rate_limiter()
    return TestClient(app)


def _register_and_get_token(client: TestClient, email: str = "test@example.com") -> str:
    resp = client.post(
        "/auth/register",
        json={"email": email, "password": "secret123"},
    )
    return resp.json()["access_token"]


def _auth_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# GET /usage/me
# ---------------------------------------------------------------------------


def test_usage_me_empty(client):
    token = _register_and_get_token(client)
    resp = client.get("/usage/me", headers=_auth_headers(token))
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_granted"] == 0
    assert data["total_used"] == 0
    assert data["remaining"] == 0
    assert data["operations"] == []


def test_usage_me_requires_auth(client):
    resp = client.get("/usage/me")
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# POST /usage/grant
# ---------------------------------------------------------------------------


def test_grant_tokens_admin(client, stores):
    # First user is admin
    admin_token = _register_and_get_token(client, "admin@example.com")
    # Create second user
    user_token = _register_and_get_token(client, "user@example.com")

    # Get user_id of second user
    resp = client.get("/auth/me", headers=_auth_headers(user_token))
    user_id = resp.json()["user_id"]

    # Admin grants tokens
    resp = client.post(
        "/usage/grant",
        json={"user_id": user_id, "amount": 1000000},
        headers=_auth_headers(admin_token),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_granted"] == 1000000
    assert data["remaining"] == 1000000

    # Verify user sees the balance
    resp = client.get("/usage/me", headers=_auth_headers(user_token))
    assert resp.json()["total_granted"] == 1000000


def test_grant_tokens_non_admin_forbidden(client):
    # First user = admin
    _register_and_get_token(client, "admin@example.com")
    # Second user tries to grant
    user_token = _register_and_get_token(client, "user@example.com")
    resp = client.get("/auth/me", headers=_auth_headers(user_token))
    user_id = resp.json()["user_id"]

    resp = client.post(
        "/usage/grant",
        json={"user_id": user_id, "amount": 1000},
        headers=_auth_headers(user_token),
    )
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Token balance tracking
# ---------------------------------------------------------------------------


def test_record_usage_updates_balance(stores):
    user_store = stores[0]
    user = user_store.create_user(
        email="track@example.com", password_hash="hash"
    )
    user_store.grant_tokens(user.user_id, 100000)

    # Record usage
    user_store.record_usage(
        user_id=user.user_id,
        operation="process_source",
        model="test-model",
        input_tokens=5000,
        output_tokens=1000,
        citekey="smith2020",
    )

    balance = user_store.get_token_balance(user.user_id)
    assert balance["total_granted"] == 100000
    assert balance["total_used"] == 6000
    assert balance["remaining"] == 94000


def test_check_token_limit(stores):
    user_store = stores[0]
    user = user_store.create_user(
        email="limit@example.com", password_hash="hash"
    )

    # No tokens granted → limit exhausted
    assert user_store.check_token_limit(user.user_id) is False

    # Grant tokens
    user_store.grant_tokens(user.user_id, 1000)
    assert user_store.check_token_limit(user.user_id) is True

    # Use all tokens
    user_store.record_usage(
        user_id=user.user_id,
        operation="test",
        model="test",
        input_tokens=800,
        output_tokens=200,
    )
    assert user_store.check_token_limit(user.user_id) is False


def test_usage_summary(stores):
    user_store = stores[0]
    user = user_store.create_user(
        email="summary@example.com", password_hash="hash"
    )
    user_store.grant_tokens(user.user_id, 500000)

    user_store.record_usage(
        user_id=user.user_id, operation="process_source",
        model="claude", input_tokens=10000, output_tokens=2000,
    )
    user_store.record_usage(
        user_id=user.user_id, operation="process_source",
        model="claude", input_tokens=8000, output_tokens=1500,
    )
    user_store.record_usage(
        user_id=user.user_id, operation="generate_research",
        model="claude", input_tokens=25000, output_tokens=4000,
    )

    summary = user_store.get_usage_summary(user.user_id)
    assert summary["total_used"] == 50500
    assert summary["remaining"] == 449500
    assert len(summary["operations"]) == 2
    ops = {o["operation"]: o for o in summary["operations"]}
    assert ops["process_source"]["count"] == 2
    assert ops["process_source"]["tokens"] == 21500
    assert ops["generate_research"]["count"] == 1
