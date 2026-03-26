"""Block draft endpoints: read/write Markdown files with git versioning.

Files are stored at KLEMMA_DATA_DIR/drafts/{project_id}/{section_id}/{block_id}.md
Each project directory is a git repo — every save creates a commit.

Routes are mounted under /projects in app.py:
  GET  /projects/{project_id}/blocks/{section_id}/{block_id}
  PUT  /projects/{project_id}/blocks/{section_id}/{block_id}
  GET  /projects/{project_id}/blocks/status
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from klemma.models import UserRecord

from ..auth.deps import get_current_user, get_user_store

logger = logging.getLogger(__name__)

router = APIRouter()

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SAFE_SEGMENT = re.compile(r"^[\w.\-]+$")


def _data_dir() -> Path:
    return Path(os.environ.get("KLEMMA_DATA_DIR", str(Path.home() / ".klemma")))


def _drafts_dir(project_id: str) -> Path:
    """Root git repo dir for a project's block drafts."""
    return _data_dir() / "drafts" / project_id


def _block_path(project_id: str, section_id: str, block_id: str) -> Path:
    return _drafts_dir(project_id) / section_id / f"{block_id}.md"


def _validate_segment(value: str, name: str) -> str:
    """Reject path traversal / shell-unsafe characters."""
    if not _SAFE_SEGMENT.match(value):
        raise HTTPException(status_code=400, detail=f"Invalid {name}: {value!r}")
    return value


def _ensure_git_repo(project_dir: Path) -> None:
    """Create a git repo at project_dir if it doesn't exist yet."""
    git_dir = project_dir / ".git"
    if git_dir.exists():
        return
    project_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "init", "-b", "main", str(project_dir)],
        capture_output=True, check=True, timeout=10,
    )
    subprocess.run(
        ["git", "-C", str(project_dir), "config", "user.email", "klemma@klemma.ai"],
        capture_output=True, check=True, timeout=5,
    )
    subprocess.run(
        ["git", "-C", str(project_dir), "config", "user.name", "Klemma SaaS"],
        capture_output=True, check=True, timeout=5,
    )


def _git_commit(project_dir: Path, file_path: Path, message: str) -> str:
    """Stage file and commit. Returns short commit hash."""
    rel = file_path.relative_to(project_dir)
    subprocess.run(
        ["git", "-C", str(project_dir), "add", str(rel)],
        capture_output=True, check=True, timeout=10,
    )
    result = subprocess.run(
        ["git", "-C", str(project_dir), "commit", "-m", message, "--allow-empty-message"],
        capture_output=True, text=True, timeout=10,
    )
    if result.returncode not in (0, 1):  # 1 = nothing to commit
        logger.warning("git commit warning: %s", result.stderr.strip())
    # Extract short hash from output like "[main abc1234] ..."
    match = re.search(r"\[(?:\w+ )?([0-9a-f]+)\]", result.stdout)
    return match.group(1) if match else ""


def _assert_project_owner(project_id: str, user: UserRecord) -> None:
    store = get_user_store()
    project = store.get_project_by_id(project_id)
    if not project or project["user_id"] != user.user_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")


def _word_count(text: str) -> int:
    return len(text.split()) if text.strip() else 0


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class BlockResponse(BaseModel):
    section_id: str
    block_id: str
    text: str
    word_count: int


class BlockSaveRequest(BaseModel):
    text: str


class BlockStatusEntry(BaseModel):
    has_draft: bool
    word_count: int


class BlockStatusResponse(BaseModel):
    statuses: dict[str, BlockStatusEntry]


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get(
    "/{project_id}/blocks/{section_id}/{block_id}",
    response_model=BlockResponse,
)
def get_block(
    project_id: str,
    section_id: str,
    block_id: str,
    user: UserRecord = Depends(get_current_user),
) -> BlockResponse:
    """Read a block draft. Returns empty text if the file doesn't exist yet."""
    _validate_segment(project_id, "project_id")
    _validate_segment(section_id, "section_id")
    _validate_segment(block_id, "block_id")
    _assert_project_owner(project_id, user)

    path = _block_path(project_id, section_id, block_id)
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    return BlockResponse(
        section_id=section_id,
        block_id=block_id,
        text=text,
        word_count=_word_count(text),
    )


@router.put(
    "/{project_id}/blocks/{section_id}/{block_id}",
    response_model=BlockResponse,
)
def save_block(
    project_id: str,
    section_id: str,
    block_id: str,
    body: BlockSaveRequest,
    user: UserRecord = Depends(get_current_user),
) -> BlockResponse:
    """Save a block draft to disk and commit to git."""
    _validate_segment(project_id, "project_id")
    _validate_segment(section_id, "section_id")
    _validate_segment(block_id, "block_id")
    _assert_project_owner(project_id, user)

    project_dir = _drafts_dir(project_id)
    _ensure_git_repo(project_dir)

    path = _block_path(project_id, section_id, block_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body.text, encoding="utf-8")

    wc = _word_count(body.text)
    commit_msg = f"block {section_id}/{block_id}: {wc}w"
    try:
        _git_commit(project_dir, path, commit_msg)
    except Exception as exc:
        # Commit failure is non-fatal — file is already saved
        logger.warning("git commit failed for %s/%s/%s: %s", project_id, section_id, block_id, exc)

    return BlockResponse(
        section_id=section_id,
        block_id=block_id,
        text=body.text,
        word_count=wc,
    )


@router.get(
    "/{project_id}/blocks/status",
    response_model=BlockStatusResponse,
)
def get_blocks_status(
    project_id: str,
    user: UserRecord = Depends(get_current_user),
) -> BlockStatusResponse:
    """Return word counts for all saved blocks in a project.

    Keys are '{section_id}/{block_id}' (without .md extension).
    """
    _validate_segment(project_id, "project_id")
    _assert_project_owner(project_id, user)

    statuses: dict[str, BlockStatusEntry] = {}
    project_dir = _drafts_dir(project_id)
    if project_dir.exists():
        for md_file in project_dir.rglob("*.md"):
            # path: {project_dir}/{section_id}/{block_id}.md
            try:
                rel = md_file.relative_to(project_dir)
                key = str(rel.with_suffix(""))  # e.g. "1.1/b1"
                text = md_file.read_text(encoding="utf-8")
                wc = _word_count(text)
                statuses[key] = BlockStatusEntry(has_draft=bool(text.strip()), word_count=wc)
            except Exception:
                continue

    return BlockStatusResponse(statuses=statuses)
