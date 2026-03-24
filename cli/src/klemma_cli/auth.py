"""Authentication — login to Klemma API, store/refresh tokens."""

from __future__ import annotations

import json
from pathlib import Path

import requests


def _auth_file() -> Path:
    return Path.home() / ".klemma-cli" / "auth.json"


def load_auth() -> dict | None:
    """Load stored auth credentials (access_token, refresh_token, api_url)."""
    path = _auth_file()
    if not path.exists():
        return None
    return json.loads(path.read_text())


def save_auth(data: dict) -> None:
    """Save auth credentials to ~/.klemma-cli/auth.json."""
    path = _auth_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n")
    path.chmod(0o600)


def login(api_url: str, email: str, password: str) -> dict:
    """Login to Klemma API. Returns auth data dict with tokens.

    Raises requests.HTTPError on failure.
    """
    resp = requests.post(
        f"{api_url}/auth/login",
        json={"email": email, "password": password},
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    auth_data = {
        "api_url": api_url,
        "access_token": data["access_token"],
        "refresh_token": data["refresh_token"],
        "user_id": data.get("user_id", ""),
        "username": data.get("username", ""),
        "email": email,
    }
    save_auth(auth_data)
    return auth_data


def refresh_access_token(api_url: str, refresh_token: str) -> dict | None:
    """Refresh the access token using the refresh token.

    Returns updated auth data or None on failure.
    """
    try:
        resp = requests.post(
            f"{api_url}/auth/refresh",
            json={"refresh_token": refresh_token},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        auth = load_auth() or {}
        auth["access_token"] = data["access_token"]
        auth["refresh_token"] = data["refresh_token"]
        save_auth(auth)
        return auth
    except Exception:
        return None
