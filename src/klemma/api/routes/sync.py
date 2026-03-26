"""Sync endpoints: git-native file sync + library bulk transfer.

Provides the server-side API for klemma-cli to synchronize markdown files
(via git) and structured data (library, embeddings, decisions) via REST.

Git repos are bare repos stored at KLEMMA_DATA_DIR/repos/{project_id}/.
Files are served via git show; commits are created via git subprocess.
"""

from __future__ import annotations

import base64
import logging
import os
import secrets
import struct
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from klemma.models import UserRecord

from ..auth.deps import get_current_user
from ..deps import get_paper_store, get_project_store, get_user_library

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _data_dir() -> Path:
    return Path(os.environ.get("KLEMMA_DATA_DIR", str(Path.home() / ".klemma")))


def _repos_dir() -> Path:
    d = _data_dir() / "repos"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _repo_path(project_id: str) -> Path:
    """Return path to the bare git repo for a project. Validates project_id.

    project_id format: "username/project-name" (like GitHub).
    Maps to: KLEMMA_DATA_DIR/repos/username/project-name/
    """
    # Reject traversal and dangerous chars, but allow single /
    if ".." in project_id or "\\" in project_id or project_id.startswith("/"):
        raise HTTPException(status_code=400, detail="Invalid project_id")
    parts = project_id.split("/")
    if len(parts) > 2 or any(not p or p.startswith("-") for p in parts):
        raise HTTPException(status_code=400, detail="Invalid project_id format")
    return _repos_dir() / project_id


def _run_git(args: list[str], cwd: Path, check: bool = True) -> subprocess.CompletedProcess:
    """Run a git command in the given directory."""
    result = subprocess.run(
        ["git"] + args,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=30,
    )
    if check and result.returncode != 0:
        logger.warning("git %s failed: %s", " ".join(args), result.stderr.strip())
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Git operation failed: {result.stderr.strip()[:200]}",
        )
    return result


def _ensure_repo_access(project_id: str, user: UserRecord) -> Path:
    """Return repo path, verifying it exists and the user owns the project."""
    repo = _repo_path(project_id)
    if not repo.exists():
        raise HTTPException(status_code=404, detail="Repository not found")
    # Check ownership — reject repos without owner file (corrupted state)
    token_file = repo / "klemma_owner"
    if not token_file.exists():
        raise HTTPException(status_code=403, detail="Repository has no owner")
    owner_id = token_file.read_text().strip()
    if owner_id != user.user_id:
        raise HTTPException(status_code=403, detail="Not your repository")
    return repo


# ---------------------------------------------------------------------------
# Schemas — Git repo management
# ---------------------------------------------------------------------------


class InitRepoRequest(BaseModel):
    project_id: str


class InitRepoResponse(BaseModel):
    git_url: str
    access_token: str
    project_id: str


class FileContentResponse(BaseModel):
    path: str
    content: str
    encoding: str = "utf-8"


class CommitRequest(BaseModel):
    file_path: str = Field(..., max_length=500)
    content: str = Field(..., max_length=1_000_000)  # 1MB max per file
    message: str = Field(default="edit from SaaS dashboard", max_length=500)


class CommitResponse(BaseModel):
    commit_hash: str
    message: str


class HistoryEntry(BaseModel):
    hash: str
    message: str
    author: str
    date: str


class RollbackRequest(BaseModel):
    steps: int = Field(default=1, ge=1, le=20)


# ---------------------------------------------------------------------------
# Schemas — Library bulk sync
# ---------------------------------------------------------------------------


class SourcePush(BaseModel):
    citekey: str
    paper_id: str = ""
    title: str = ""
    authors: str = ""
    year: Optional[int] = None
    doi: Optional[str] = None
    abstract: str = ""
    sections: list[str] = []
    status: str = "pending"


class FragmentPush(BaseModel):
    fragment_id: str
    paper_id: str
    text: str
    fragment_type: str = "key_idea"
    citation_intent: Optional[str] = None
    page: Optional[int] = None


class LibraryPushRequest(BaseModel):
    sources: list[SourcePush] = Field(default=[], max_length=1000)
    fragments: list[FragmentPush] = Field(default=[], max_length=10000)


class EmbeddingEntry(BaseModel):
    id: str
    vector_b64: str
    model: str = "specter2"


class EmbeddingsPushRequest(BaseModel):
    paper_embeddings: list[EmbeddingEntry] = Field(default=[], max_length=500)
    fragment_embeddings: list[EmbeddingEntry] = Field(default=[], max_length=5000)


class DecisionPush(BaseModel):
    decision_id: str = ""
    trigger_type: str = ""
    trigger_source: str = ""
    context_json: str = "{}"
    options_json: str = "{}"
    chosen_option: Optional[str] = None
    rationale: str = ""
    note: str = ""
    feedback: str = ""


class DecisionsPushRequest(BaseModel):
    decisions: list[DecisionPush] = []


class SourcePull(BaseModel):
    citekey: str
    paper_id: str
    title: str = ""
    authors: str = ""
    year: Optional[int] = None
    doi: Optional[str] = None
    abstract: str = ""
    sections: list[str] = []
    status: str = "pending"
    updated_at: str = ""


class FragmentPull(BaseModel):
    fragment_id: str
    paper_id: str
    text: str
    fragment_type: str = "key_idea"
    citation_intent: Optional[str] = None
    page: Optional[int] = None


class LibraryPullResponse(BaseModel):
    sources: list[SourcePull] = []
    fragments: list[FragmentPull] = []


class SyncStatusResponse(BaseModel):
    project_id: str
    source_count: int
    fragment_count: int
    last_commit: str = ""
    last_commit_date: str = ""
    head_hash: str = ""


# ---------------------------------------------------------------------------
# Git repo management endpoints
# ---------------------------------------------------------------------------


@router.post("/init-repo", response_model=InitRepoResponse, status_code=201)
async def init_repo(
    body: InitRepoRequest,
    user: UserRecord = Depends(get_current_user),
) -> InitRepoResponse:
    """Create a bare git repo for a project. Returns git URL + access token.

    project_id is namespaced with user_id prefix to prevent collision
    between users who choose the same project name (e.g. "dissertation").
    """
    # Namespace: username/project_id → like GitHub (e.g. "ilya-bolkhovsky/dissertation")
    if not user.username:
        raise HTTPException(status_code=400, detail="User has no username — re-login to generate one")
    namespaced_id = f"{user.username}/{body.project_id}"
    repo = _repo_path(namespaced_id)
    if repo.exists():
        raise HTTPException(status_code=409, detail="Repository already exists")

    repo.mkdir(parents=True)
    _run_git(["init", "--bare"], cwd=repo)
    # Enable git push via HTTP (disabled by default in git-http-backend)
    _run_git(["config", "http.receivepack", "true"], cwd=repo)

    # Store owner
    (repo / "klemma_owner").write_text(user.user_id)

    # Generate access token for git HTTP auth
    token = secrets.token_urlsafe(32)
    (repo / "klemma_token").write_text(token)

    # Construct git URL (token embedded for MVP — Option A from plan)
    api_base = os.environ.get("KLEMMA_API_URL", "https://litresearch.ru")
    git_url = f"{api_base}/git/{namespaced_id}.git"

    return InitRepoResponse(
        git_url=git_url,
        access_token=token,
        project_id=namespaced_id,
    )


@router.get("/file/{project_id:path}", response_model=FileContentResponse)
async def get_file(
    project_id: str,
    file_path: str = Query(..., description="Path to file in repo"),
    user: UserRecord = Depends(get_current_user),
) -> FileContentResponse:
    """Read a file from the project's git repo (HEAD revision).

    file_path is a query param (not path) because project_id itself contains slashes.
    Example: GET /sync/file/ilya-bolkhovsky/dissertation?file_path=KLEMMA.md
    """
    repo = _ensure_repo_access(project_id, user)

    # Validate path — prevent flag injection and path traversal
    if ".." in file_path or file_path.startswith("/") or file_path.startswith("-"):
        raise HTTPException(status_code=400, detail="Invalid file path")

    result = _run_git(["show", f"HEAD:{file_path}"], cwd=repo, check=False)
    if result.returncode != 0:
        raise HTTPException(status_code=404, detail=f"File not found: {file_path}")

    return FileContentResponse(path=file_path, content=result.stdout)


@router.get("/history/{project_id:path}")
async def get_history(
    project_id: str,
    user: UserRecord = Depends(get_current_user),
    limit: int = Query(default=20, ge=1, le=100),
) -> list[HistoryEntry]:
    """Return commit history for a project's git repo."""
    repo = _ensure_repo_access(project_id, user)

    result = _run_git(
        ["log", f"-{limit}", "--format=%H|%s|%an|%aI"],
        cwd=repo,
        check=False,
    )
    if result.returncode != 0:
        return []  # Empty repo, no commits yet

    entries = []
    for line in result.stdout.strip().split("\n"):
        if not line:
            continue
        parts = line.split("|", 3)
        if len(parts) >= 4:
            entries.append(HistoryEntry(
                hash=parts[0], message=parts[1],
                author=parts[2], date=parts[3],
            ))
    return entries


@router.post("/commit/{project_id:path}", response_model=CommitResponse)
async def commit_file(
    project_id: str,
    body: CommitRequest,
    user: UserRecord = Depends(get_current_user),
) -> CommitResponse:
    """Commit a file change to the project repo (for browser edits).

    Works on a bare repo by using a temporary index.
    """
    repo = _ensure_repo_access(project_id, user)

    # Validate file_path — prevent traversal and flag injection
    if ".." in body.file_path or body.file_path.startswith("/") or body.file_path.startswith("-"):
        raise HTTPException(status_code=400, detail="Invalid file path")

    # Write content to a blob and update the tree
    blob_result = subprocess.run(
        ["git", "hash-object", "-w", "--stdin"],
        input=body.content,
        cwd=str(repo),
        capture_output=True,
        text=True,
        timeout=10,
    )
    if blob_result.returncode != 0:
        raise HTTPException(status_code=500, detail="Failed to write blob")

    blob_hash = blob_result.stdout.strip()

    # Read current tree (if any)
    tree_result = _run_git(["rev-parse", "HEAD^{tree}"], cwd=repo, check=False)
    # Minimal env — avoid leaking server secrets (JWT_SECRET, API keys) into subprocess
    env = {
        "HOME": os.environ.get("HOME", "/tmp"),
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "GIT_AUTHOR_NAME": user.name or user.email,
        "GIT_AUTHOR_EMAIL": user.email,
        "GIT_COMMITTER_NAME": user.name or user.email,
        "GIT_COMMITTER_EMAIL": user.email,
    }

    if tree_result.returncode == 0:
        # Update existing tree
        parent_tree = tree_result.stdout.strip()
        # Read tree into index, update the file, write tree
        tmp_index = repo / "tmp_index"
        env["GIT_INDEX_FILE"] = str(tmp_index)
        _run_git(["read-tree", parent_tree], cwd=repo)
        subprocess.run(
            ["git", "update-index", "--add", "--cacheinfo", f"100644,{blob_hash},{body.file_path}"],
            cwd=str(repo), env=env, capture_output=True, text=True, timeout=10,
        )
        new_tree_result = subprocess.run(
            ["git", "write-tree"],
            cwd=str(repo), env=env, capture_output=True, text=True, timeout=10,
        )
        new_tree = new_tree_result.stdout.strip()
        tmp_index.unlink(missing_ok=True)

        # Create commit with parent
        head = _run_git(["rev-parse", "HEAD"], cwd=repo).stdout.strip()
        commit_result = subprocess.run(
            ["git", "commit-tree", new_tree, "-p", head, "-m", body.message],
            cwd=str(repo), env=env, capture_output=True, text=True, timeout=10,
        )
    else:
        # First commit — create tree from scratch
        env["GIT_INDEX_FILE"] = str(repo / "tmp_index")
        subprocess.run(
            ["git", "update-index", "--add", "--cacheinfo", f"100644,{blob_hash},{body.file_path}"],
            cwd=str(repo), env=env, capture_output=True, text=True, timeout=10,
        )
        new_tree_result = subprocess.run(
            ["git", "write-tree"],
            cwd=str(repo), env=env, capture_output=True, text=True, timeout=10,
        )
        new_tree = new_tree_result.stdout.strip()
        (repo / "tmp_index").unlink(missing_ok=True)

        commit_result = subprocess.run(
            ["git", "commit-tree", new_tree, "-m", body.message],
            cwd=str(repo), env=env, capture_output=True, text=True, timeout=10,
        )

    if commit_result.returncode != 0:
        raise HTTPException(status_code=500, detail="Failed to create commit")

    commit_hash = commit_result.stdout.strip()
    # Update HEAD
    _run_git(["update-ref", "HEAD", commit_hash], cwd=repo)

    return CommitResponse(commit_hash=commit_hash, message=body.message)


@router.post("/rollback/{project_id:path}")
async def rollback(
    project_id: str,
    body: RollbackRequest,
    user: UserRecord = Depends(get_current_user),
) -> dict:
    """Revert the last N commits in the project repo."""
    repo = _ensure_repo_access(project_id, user)

    # Check we have enough commits
    result = _run_git(
        ["rev-list", "--count", "HEAD"],
        cwd=repo,
        check=False,
    )
    if result.returncode != 0:
        raise HTTPException(status_code=400, detail="No commits to rollback")

    count = int(result.stdout.strip())
    if body.steps >= count:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot rollback {body.steps} commits — only {count} exist",
        )

    # Get the target commit
    target = _run_git(
        ["rev-parse", f"HEAD~{body.steps}"],
        cwd=repo,
    ).stdout.strip()

    # Reset HEAD to target (safe for bare repos)
    _run_git(["update-ref", "HEAD", target], cwd=repo)

    return {
        "rolled_back_to": target,
        "steps": body.steps,
        "message": f"Rolled back {body.steps} commit(s)",
    }


# ---------------------------------------------------------------------------
# Library bulk sync endpoints
# ---------------------------------------------------------------------------


@router.post("/push/library")
async def push_library(
    body: LibraryPushRequest,
    user: UserRecord = Depends(get_current_user),
) -> dict:
    """Batch upsert sources + fragments from CLI to server."""
    paper_store = get_paper_store()
    library = get_user_library()
    project_store = get_project_store()

    sources_saved = 0
    fragments_saved = 0

    # Map client paper_id → server paper_id (server may assign new UUIDs)
    paper_id_map: dict[str, str] = {}

    for src in body.sources:
        client_paper_id = src.paper_id
        server_paper_id = client_paper_id

        if not client_paper_id:
            existing = None
            if src.doi:
                existing = paper_store.find_paper(doi=src.doi)
            if existing:
                server_paper_id = existing.paper_id
            else:
                server_paper_id = paper_store.register_paper(
                    title=src.title, authors=src.authors,
                    year=src.year, doi=src.doi, abstract=src.abstract,
                    pdf_hash="",
                )
        else:
            existing = paper_store.get_paper_by_id(client_paper_id)
            if not existing:
                server_paper_id = paper_store.register_paper(
                    title=src.title, authors=src.authors,
                    year=src.year, doi=src.doi, abstract=src.abstract,
                    pdf_hash="",
                )
            else:
                server_paper_id = existing.paper_id
                paper_store.update_paper_metadata(
                    server_paper_id, title=src.title, authors=src.authors,
                    year=src.year, doi=src.doi, abstract=src.abstract,
                )

        if client_paper_id:
            paper_id_map[client_paper_id] = server_paper_id

        library.add_source(
            server_paper_id, src.citekey,
            status=src.status,
            user_id=user.user_id,
        )

        if src.sections:
            project_store.set_source_sections(
                src.citekey, server_paper_id, src.sections, [],
                user_id=user.user_id,
            )

        sources_saved += 1

    for frag in body.fragments:
        from klemma.models import FragmentRecord
        # Resolve client paper_id to server paper_id
        resolved_paper_id = paper_id_map.get(frag.paper_id, frag.paper_id)
        record = FragmentRecord(
            fragment_id=frag.fragment_id,
            paper_id=resolved_paper_id,
            fragment_text=frag.text,
            fragment_type=frag.fragment_type,
            page_number=frag.page,
            citation_intent=frag.citation_intent,
        )
        paper_store.save_fragments(
            resolved_paper_id, [record],
            prompt_hash="cli-sync",
            ai_model="cli-sync",
        )
        fragments_saved += 1

    return {
        "sources_saved": sources_saved,
        "fragments_saved": fragments_saved,
    }


@router.post("/push/embeddings")
async def push_embeddings(
    body: EmbeddingsPushRequest,
    user: UserRecord = Depends(get_current_user),
) -> dict:
    """Batch upsert embedding vectors (base64-encoded float32)."""
    paper_store = get_paper_store()
    paper_count = 0
    fragment_count = 0

    for emb in body.paper_embeddings:
        vector = _decode_vector(emb.vector_b64)
        paper_store.save_paper_embedding(emb.id, vector, emb.model)
        paper_count += 1

    for emb in body.fragment_embeddings:
        vector = _decode_vector(emb.vector_b64)
        paper_store.save_fragment_embedding(emb.id, vector, emb.model)
        fragment_count += 1

    return {
        "paper_embeddings_saved": paper_count,
        "fragment_embeddings_saved": fragment_count,
    }


@router.post("/push/decisions")
async def push_decisions(
    body: DecisionsPushRequest,
    user: UserRecord = Depends(get_current_user),
) -> dict:
    """Batch upsert decisions from CLI."""
    # Decisions are stored in the project's state.db — for now, store as
    # JSON in a simple key-value table. Full integration deferred to Phase 3.
    count = len(body.decisions)
    logger.info("Received %d decisions from user %s (storage deferred)", count, user.user_id)
    return {"decisions_received": count, "status": "acknowledged"}


@router.get("/pull/library", response_model=LibraryPullResponse)
async def pull_library(
    user: UserRecord = Depends(get_current_user),
    since: Optional[str] = Query(default=None, description="ISO 8601 timestamp for incremental pull"),
) -> LibraryPullResponse:
    """Pull library data (sources + fragments) from server to CLI."""
    library = get_user_library()
    paper_store = get_paper_store()

    all_sources = library.get_all_sources(user_id=user.user_id)
    project_store = get_project_store()

    sources = []
    fragments = []

    for src in all_sources:
        paper = paper_store.get_paper_by_id(src.paper_id)
        project_sections = project_store.get_source_sections(
            src.citekey, user_id=user.user_id
        )
        sections = project_sections if project_sections else src.sections

        sources.append(SourcePull(
            citekey=src.citekey,
            paper_id=src.paper_id,
            title=paper.title if paper else "",
            authors=paper.authors if paper else "",
            year=paper.year if paper else None,
            doi=paper.doi if paper else None,
            abstract=paper.abstract if paper else "",
            sections=sections,
            status=src.status,
            updated_at=datetime.now(timezone.utc).isoformat(),
        ))

        # Include fragments for each source
        if paper:
            for frag in paper_store.get_fragments(src.paper_id):
                fragments.append(FragmentPull(
                    fragment_id=frag.fragment_id,
                    paper_id=frag.paper_id,
                    text=frag.fragment_text,
                    fragment_type=frag.fragment_type,
                    citation_intent=frag.citation_intent,
                    page=frag.page_number,
                ))

    return LibraryPullResponse(sources=sources, fragments=fragments)


@router.get("/pull/decisions")
async def pull_decisions(
    user: UserRecord = Depends(get_current_user),
    since: Optional[str] = Query(default=None),
) -> dict:
    """Pull decisions from server (Phase 3 — stub)."""
    return {"decisions": []}


@router.get("/status/{project_id:path}", response_model=SyncStatusResponse)
async def sync_status(
    project_id: str,
    user: UserRecord = Depends(get_current_user),
) -> SyncStatusResponse:
    """Summary: file hashes, library counts, last sync time."""
    repo = _ensure_repo_access(project_id, user)

    library = get_user_library()
    paper_store = get_paper_store()

    source_count = library.count(user_id=user.user_id)

    # Count fragments across all user's sources
    all_sources = library.get_all_sources(user_id=user.user_id)
    fragment_count = sum(
        len(paper_store.get_fragments(s.paper_id)) for s in all_sources
    )

    # Get last commit info
    result = _run_git(
        ["log", "-1", "--format=%H|%s|%aI"],
        cwd=repo,
        check=False,
    )
    head_hash = ""
    last_commit = ""
    last_commit_date = ""
    if result.returncode == 0 and result.stdout.strip():
        parts = result.stdout.strip().split("|", 2)
        if len(parts) >= 3:
            head_hash = parts[0]
            last_commit = parts[1]
            last_commit_date = parts[2]

    return SyncStatusResponse(
        project_id=project_id,
        source_count=source_count,
        fragment_count=fragment_count,
        last_commit=last_commit,
        last_commit_date=last_commit_date,
        head_hash=head_hash,
    )


# ---------------------------------------------------------------------------
# Token verification endpoint (for Caddy/nginx auth_request)
# ---------------------------------------------------------------------------


@router.get("/verify-git-token")
async def verify_git_token(
    token: str = Query(...),
    project_id: str = Query(...),
) -> dict:
    """Verify a git access token for a project. Used by reverse proxy auth."""
    repo = _repo_path(project_id)
    token_file = repo / "klemma_token"
    if not token_file.exists():
        raise HTTPException(status_code=401, detail="Invalid token")

    stored_token = token_file.read_text().strip()
    if not secrets.compare_digest(token, stored_token):
        raise HTTPException(status_code=401, detail="Invalid token")

    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _decode_vector(b64: str) -> list[float]:
    """Decode base64-encoded float32 vector."""
    raw = base64.b64decode(b64)
    count = len(raw) // 4
    return list(struct.unpack(f"{count}f", raw))
