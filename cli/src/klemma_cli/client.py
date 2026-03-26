"""HTTP client for Klemma API — handles auth headers and token refresh."""

from __future__ import annotations

from typing import Any

import requests

from .auth import load_auth, refresh_access_token


class KlemmaClient:
    """HTTP client with automatic token refresh for Klemma API."""

    def __init__(self, api_url: str | None = None, access_token: str | None = None) -> None:
        auth = load_auth()
        self.api_url = api_url or (auth["api_url"] if auth else "")
        self._access_token = access_token or (auth["access_token"] if auth else "")
        self._refresh_token = auth["refresh_token"] if auth else ""

        if not self.api_url:
            raise RuntimeError(
                "Not logged in. Run 'klemma-cli link' first."
            )

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._access_token}",
            "Content-Type": "application/json",
        }

    def _maybe_refresh(self, resp: requests.Response) -> bool:
        """Try to refresh token on 401. Returns True if refreshed."""
        if resp.status_code != 401 or not self._refresh_token:
            return False
        result = refresh_access_token(self.api_url, self._refresh_token)
        if result:
            self._access_token = result["access_token"]
            self._refresh_token = result["refresh_token"]
            return True
        return False

    def get(self, path: str, **kwargs: Any) -> requests.Response:
        """GET request with auto-refresh on 401."""
        url = f"{self.api_url}{path}"
        resp = requests.get(url, headers=self._headers(), timeout=30, **kwargs)
        if self._maybe_refresh(resp):
            resp = requests.get(url, headers=self._headers(), timeout=30, **kwargs)
        resp.raise_for_status()
        return resp

    def post(self, path: str, json: Any = None, **kwargs: Any) -> requests.Response:
        """POST request with auto-refresh on 401."""
        url = f"{self.api_url}{path}"
        resp = requests.post(url, headers=self._headers(), json=json, timeout=30, **kwargs)
        if self._maybe_refresh(resp):
            resp = requests.post(url, headers=self._headers(), json=json, timeout=30, **kwargs)
        resp.raise_for_status()
        return resp

    def put(self, path: str, json: Any = None, **kwargs: Any) -> requests.Response:
        """PUT request with auto-refresh on 401."""
        url = f"{self.api_url}{path}"
        resp = requests.put(url, headers=self._headers(), json=json, timeout=60, **kwargs)
        if self._maybe_refresh(resp):
            resp = requests.put(url, headers=self._headers(), json=json, timeout=60, **kwargs)
        resp.raise_for_status()
        return resp
