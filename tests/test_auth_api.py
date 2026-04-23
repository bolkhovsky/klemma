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


def test_register_grants_initial_token_balance(client, user_store, monkeypatch):
    """New users get the default 1M token allowance so first action works."""
    monkeypatch.delenv("KLEMMA_INITIAL_TOKEN_GRANT", raising=False)
    resp = client.post(
        "/auth/register",
        json={"email": "fresh@example.com", "password": "secret123"},
    )
    assert resp.status_code == 201
    user_id = resp.json()["user_id"]
    bal = user_store.get_token_balance(user_id)
    assert bal["total_granted"] == 1_000_000
    assert bal["remaining"] == 1_000_000
    assert user_store.check_token_limit(user_id) is True


def test_register_initial_grant_configurable(client, user_store, monkeypatch):
    """KLEMMA_INITIAL_TOKEN_GRANT overrides the default amount."""
    monkeypatch.setenv("KLEMMA_INITIAL_TOKEN_GRANT", "50000")
    resp = client.post(
        "/auth/register",
        json={"email": "small@example.com", "password": "secret123"},
    )
    assert resp.status_code == 201
    bal = user_store.get_token_balance(resp.json()["user_id"])
    assert bal["total_granted"] == 50_000


def test_register_initial_grant_disabled(client, user_store, monkeypatch):
    """Setting KLEMMA_INITIAL_TOKEN_GRANT=0 skips the grant entirely."""
    monkeypatch.setenv("KLEMMA_INITIAL_TOKEN_GRANT", "0")
    resp = client.post(
        "/auth/register",
        json={"email": "paid@example.com", "password": "secret123"},
    )
    assert resp.status_code == 201
    bal = user_store.get_token_balance(resp.json()["user_id"])
    assert bal["total_granted"] == 0
    assert user_store.check_token_limit(resp.json()["user_id"]) is False


def test_register_initial_grant_invalid_falls_back_to_default(client, user_store, monkeypatch):
    """Invalid or negative env values warn and use the default."""
    monkeypatch.setenv("KLEMMA_INITIAL_TOKEN_GRANT", "not-a-number")
    resp = client.post(
        "/auth/register",
        json={"email": "bad@example.com", "password": "secret123"},
    )
    assert resp.status_code == 201
    bal = user_store.get_token_balance(resp.json()["user_id"])
    assert bal["total_granted"] == 1_000_000


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
    assert resp.status_code == 401  # HTTPBearer returns 403 when no credentials


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


# ---------------------------------------------------------------------------
# JWT claims
# ---------------------------------------------------------------------------


def test_access_token_contains_standard_claims(client):
    """Access tokens must include iat, iss, aud claims."""
    from klemma.api.auth.tokens import decode_token

    reg = client.post(
        "/auth/register",
        json={"email": "claims@example.com", "password": "secret123"},
    )
    token = reg.json()["access_token"]
    payload = decode_token(token)
    assert payload is not None
    assert "iat" in payload
    assert payload["iss"] == "klemma-api"
    assert payload["aud"] == "klemma"
    assert payload["type"] == "access"


# ---------------------------------------------------------------------------
# OpenAPI docs gating
# ---------------------------------------------------------------------------


def test_docs_available_in_development(client):
    """OpenAPI docs should be available when KLEMMA_ENV is not 'production'."""
    resp = client.get("/docs")
    assert resp.status_code == 200


def test_docs_disabled_in_production(tmp_path, monkeypatch):
    """OpenAPI docs should return 404 when KLEMMA_ENV=production."""
    monkeypatch.setenv("KLEMMA_ENV", "production")
    from klemma.stores.user_store import LocalUserStore

    store = LocalUserStore(tmp_path / "users.db")
    app = create_app()
    set_user_store(store)
    prod_client = TestClient(app)
    resp = prod_client.get("/docs")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# CORS
# ---------------------------------------------------------------------------


def test_cors_allows_configured_origin(client):
    """Dev origins should get CORS headers."""
    resp = client.options(
        "/auth/login",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "POST",
        },
    )
    assert resp.headers.get("access-control-allow-origin") == "http://localhost:3000"


def test_cors_blocks_unknown_origin(client):
    """Unknown origins should not get CORS allow headers."""
    resp = client.options(
        "/auth/login",
        headers={
            "Origin": "https://evil.com",
            "Access-Control-Request-Method": "POST",
        },
    )
    assert "access-control-allow-origin" not in resp.headers
