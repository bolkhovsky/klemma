"""Tests for auth API endpoints (ADR-009)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from klemma.api.app import create_app
from klemma.api.auth.deps import set_user_store
from klemma.api.rate_limit import reset_rate_limiter
from klemma.stores.user_store import LocalUserStore


@pytest.fixture
def user_store(tmp_path) -> LocalUserStore:
    return LocalUserStore(tmp_path / "users.db")


@pytest.fixture
def client(user_store) -> TestClient:
    app = create_app()
    # Override the lifespan-set store with our test store
    set_user_store(user_store)
    reset_rate_limiter()
    return TestClient(app)


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def test_register_success(client):
    resp = client.post(
        "/auth/register",
        json={"email": "alice@example.com", "password": "secret123", "name": "Alice"},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert "access_token" in data
    assert "refresh_token" in data
    assert data["token_type"] == "bearer"


def test_register_duplicate_email(client):
    client.post(
        "/auth/register",
        json={"email": "alice@example.com", "password": "secret123"},
    )
    resp = client.post(
        "/auth/register",
        json={"email": "alice@example.com", "password": "othersecret"},
    )
    assert resp.status_code == 409
    assert "already registered" in resp.json()["detail"]


def test_register_invalid_email(client):
    resp = client.post(
        "/auth/register",
        json={"email": "not-an-email", "password": "secret123"},
    )
    assert resp.status_code == 422


def test_register_password_too_short(client):
    resp = client.post(
        "/auth/register",
        json={"email": "short@example.com", "password": "abc"},
    )
    assert resp.status_code == 422


def test_register_email_case_insensitive(client):
    client.post(
        "/auth/register",
        json={"email": "Alice@Example.COM", "password": "secret123"},
    )
    resp = client.post(
        "/auth/register",
        json={"email": "alice@example.com", "password": "othersecret"},
    )
    assert resp.status_code == 409


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------


def test_login_success(client):
    client.post(
        "/auth/register",
        json={"email": "bob@example.com", "password": "pass1234"},
    )
    resp = client.post(
        "/auth/login",
        json={"email": "bob@example.com", "password": "pass1234"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "access_token" in data
    assert "refresh_token" in data


def test_login_wrong_password(client):
    client.post(
        "/auth/register",
        json={"email": "bob@example.com", "password": "pass1234"},
    )
    resp = client.post(
        "/auth/login",
        json={"email": "bob@example.com", "password": "wrongpass"},
    )
    assert resp.status_code == 401


def test_login_nonexistent_user(client):
    resp = client.post(
        "/auth/login",
        json={"email": "nobody@example.com", "password": "password"},
    )
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# /auth/me
# ---------------------------------------------------------------------------


def test_me_authenticated(client):
    reg = client.post(
        "/auth/register",
        json={"email": "carol@example.com", "password": "carolpass", "name": "Carol"},
    )
    token = reg.json()["access_token"]
    resp = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["email"] == "carol@example.com"
    assert data["name"] == "Carol"
    assert data["email_verified"] is False


def test_me_no_token(client):
    resp = client.get("/auth/me")
    assert resp.status_code == 403  # HTTPBearer returns 403 when no credentials


def test_me_invalid_token(client):
    resp = client.get(
        "/auth/me", headers={"Authorization": "Bearer invalid.token.here"}
    )
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Refresh
# ---------------------------------------------------------------------------


def test_refresh_success(client):
    reg = client.post(
        "/auth/register",
        json={"email": "dan@example.com", "password": "danpass12"},
    )
    refresh_token = reg.json()["refresh_token"]
    resp = client.post("/auth/refresh", json={"refresh_token": refresh_token})
    assert resp.status_code == 200
    data = resp.json()
    assert "access_token" in data
    assert "refresh_token" in data
    # New refresh token should be different (rotation)
    assert data["refresh_token"] != refresh_token


def test_refresh_reuse_revokes_all(client):
    reg = client.post(
        "/auth/register",
        json={"email": "eve@example.com", "password": "evepass12"},
    )
    old_refresh = reg.json()["refresh_token"]
    # First refresh — succeeds
    client.post("/auth/refresh", json={"refresh_token": old_refresh})
    # Replay old token — should fail (token reuse detection)
    resp = client.post("/auth/refresh", json={"refresh_token": old_refresh})
    assert resp.status_code == 401


def test_refresh_invalid_token(client):
    resp = client.post("/auth/refresh", json={"refresh_token": "garbage"})
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------


def test_register_rate_limit(client):
    """Registration is limited to 3 requests per minute per IP."""
    for i in range(3):
        client.post(
            "/auth/register",
            json={"email": f"rate{i}@example.com", "password": "secret123"},
        )
    # 4th request should be throttled
    resp = client.post(
        "/auth/register",
        json={"email": "rate3@example.com", "password": "secret123"},
    )
    assert resp.status_code == 429
    assert "Retry-After" in resp.headers


def test_login_rate_limit(client):
    """Login is limited to 5 requests per minute per IP."""
    for i in range(5):
        client.post(
            "/auth/login",
            json={"email": f"nobody{i}@example.com", "password": "secret123"},
        )
    # 6th request should be throttled
    resp = client.post(
        "/auth/login",
        json={"email": "nobody5@example.com", "password": "secret123"},
    )
    assert resp.status_code == 429
