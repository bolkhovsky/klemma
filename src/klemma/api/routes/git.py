"""Git smart HTTP transport via git-http-backend CGI wrapper.

Serves the git smart HTTP protocol so klemma-cli can push/pull markdown
files using standard git tooling.

Route mounted at root (prefix="") in app.py:
  GET  /git/{project_path:path}  → info/refs discovery
  POST /git/{project_path:path}  → git-receive-pack / git-upload-pack

Auth: HTTP Basic, password = klemma_token stored in the bare repo.
Repos: KLEMMA_DATA_DIR/repos/{username}/{project}/  (bare, no .git suffix)
URL:   https://litresearch.ru/git/username/project.git  (.git suffix stripped here)

NOTE: proc.communicate() buffers the entire git pack in memory.
Acceptable for < 1MB dissertation repos. Would need streaming for large repos.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import os
import secrets
from pathlib import Path

from fastapi import APIRouter, Request, Response

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _repos_dir() -> Path:
    return Path(os.environ.get("KLEMMA_DATA_DIR", str(Path.home() / ".klemma"))) / "repos"


def _extract_token(request: Request) -> str | None:
    """Extract bearer/basic auth token from request headers."""
    auth = request.headers.get("authorization", "")
    if auth.startswith("Basic "):
        # Pad to valid base64 length before decoding
        decoded = base64.b64decode(auth[6:] + "==").decode("latin-1")
        _, _, pwd = decoded.partition(":")
        return pwd or None
    if auth.startswith("Bearer "):
        return auth[7:] or None
    return None


def _parse_project_path(project_path: str) -> tuple[str, str]:
    """Split a git URL path into (repo_id, service_path).

    Examples:
      'user/repo.git/info/refs'  → ('user/repo', 'info/refs')
      'user/repo.git'            → ('user/repo', '')
      'user/repo'                → ('user/repo', '')
    """
    if ".git/" in project_path:
        repo_part, _, service = project_path.partition(".git/")
        return repo_part.strip("/"), service
    if project_path.endswith(".git"):
        return project_path[:-4].strip("/"), ""
    return project_path.strip("/"), ""


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------


@router.api_route("/git/{project_path:path}", methods=["GET", "POST"])
async def git_http_backend(project_path: str, request: Request) -> Response:
    """Serve git smart HTTP protocol via git-http-backend CGI."""
    repo_id, service_path = _parse_project_path(project_path)

    # Reject path traversal and dangerous characters
    if ".." in repo_id or "\\" in repo_id or repo_id.startswith("/"):
        return Response(status_code=400)
    parts = repo_id.split("/")
    if len(parts) > 2 or any(not p or p.startswith("-") for p in parts):
        return Response(status_code=400)

    repo = _repos_dir() / repo_id
    token_file = repo / "klemma_token"
    if not token_file.exists():
        return Response(status_code=404)

    # Auth check — constant-time comparison
    stored_token = token_file.read_text().strip()
    provided_token = _extract_token(request)
    if not provided_token or not secrets.compare_digest(provided_token, stored_token):
        return Response(
            status_code=401,
            headers={"WWW-Authenticate": 'Basic realm="klemma"'},
        )

    body = await request.body()
    env = {
        "GIT_HTTP_EXPORT_ALL": "1",
        "GIT_HTTP_RECEIVE_PACK": "true",  # enable git push (receive-pack)
        "GIT_PROJECT_ROOT": str(_repos_dir()),
        "PATH_INFO": "/" + repo_id + ("/" + service_path if service_path else ""),
        "REQUEST_METHOD": request.method,
        "QUERY_STRING": str(request.url.query),
        "CONTENT_TYPE": request.headers.get("content-type", ""),
        "CONTENT_LENGTH": request.headers.get("content-length", ""),
        "PATH": os.environ.get("PATH", "/usr/bin:/usr/local/bin:/bin"),
    }

    try:
        proc = await asyncio.create_subprocess_exec(
            "git", "http-backend",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(input=body), timeout=120)
    except asyncio.TimeoutError:
        logger.warning("git-http-backend timed out for %s", repo_id)
        return Response(status_code=504)
    except FileNotFoundError:
        logger.error("git not found in PATH — cannot serve git transport")
        return Response(status_code=503)

    if stderr:
        logger.warning("git-http-backend stderr for %s: %s", repo_id, stderr.decode()[:200])

    # Parse CGI response: status line(s) + headers + blank line + body
    sep = b"\r\n\r\n" if b"\r\n\r\n" in stdout else b"\n\n"
    if sep not in stdout:
        logger.error("git-http-backend returned no header separator for %s", repo_id)
        return Response(status_code=502)

    header_block, _, response_body = stdout.partition(sep)

    headers: dict[str, str] = {}
    status_code = 200
    for line in header_block.split(b"\n"):
        line = line.rstrip(b"\r")
        if b":" not in line:
            continue
        k, _, v = line.partition(b":")
        key, val = k.decode().strip(), v.decode().strip()
        if key.lower() == "status":
            try:
                status_code = int(val.split()[0])
            except (ValueError, IndexError):
                pass
        else:
            headers[key] = val

    return Response(content=response_body, status_code=status_code, headers=headers)
