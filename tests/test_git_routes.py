"""Unit tests for git-http-backend route helpers."""

import base64
from unittest.mock import MagicMock

from klemma.api.routes.git import _extract_token, _parse_project_path

# ---------------------------------------------------------------------------
# _parse_project_path
# ---------------------------------------------------------------------------


def test_parse_project_path_with_service():
    assert _parse_project_path("user/repo.git/info/refs") == ("user/repo", "info/refs")


def test_parse_project_path_receive_pack():
    assert _parse_project_path("user/repo.git/git-receive-pack") == (
        "user/repo",
        "git-receive-pack",
    )


def test_parse_project_path_dot_git_only():
    assert _parse_project_path("user/repo.git") == ("user/repo", "")


def test_parse_project_path_no_dot_git():
    assert _parse_project_path("user/repo") == ("user/repo", "")


def test_parse_project_path_strips_slashes():
    assert _parse_project_path("/user/repo.git/") == ("user/repo", "")


def test_parse_project_path_namespaced():
    """project_id may be a single segment (no slash) if user omits namespace."""
    assert _parse_project_path("myproject.git") == ("myproject", "")


# ---------------------------------------------------------------------------
# _extract_token
# ---------------------------------------------------------------------------


def _make_request(auth_header: str) -> MagicMock:
    req = MagicMock()
    req.headers = {"authorization": auth_header} if auth_header else {}
    return req


def test_extract_token_basic():
    token = "mysecrettoken"
    encoded = base64.b64encode(f"token:{token}".encode()).decode()
    req = _make_request(f"Basic {encoded}")
    assert _extract_token(req) == token


def test_extract_token_bearer():
    req = _make_request("Bearer abc123")
    assert _extract_token(req) == "abc123"


def test_extract_token_empty_header():
    req = _make_request("")
    assert _extract_token(req) is None


def test_extract_token_no_password_in_basic():
    encoded = base64.b64encode(b"user:").decode()
    req = _make_request(f"Basic {encoded}")
    assert _extract_token(req) is None


def test_extract_token_unknown_scheme():
    req = _make_request("Digest abc123")
    assert _extract_token(req) is None
