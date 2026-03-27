"""Draft endpoints: Markdown-first file management with section extraction.

Files are stored at KLEMMA_DATA_DIR/drafts/{project_id}/draft/ (ADR-016).
Git repo root: KLEMMA_DATA_DIR/drafts/{project_id}/
Draft files:   KLEMMA_DATA_DIR/drafts/{project_id}/draft/

Convention (ADR-016):
  dissertation: intro.md, chapter_1.md, chapter_2.md, ..., conclusion.md
  paper:        paper.md (single file, all sections as ## headings)

Routes mounted under /projects in app.py:
  GET  /projects/{id}/drafts                              → list files + parsed headings
  GET  /projects/{id}/drafts/{filename}                   → file content + sections
  PUT  /projects/{id}/drafts/{filename}                   → save full file (git commit)
  POST /projects/{id}/drafts/init                         → create file from outline
  PUT  /projects/{id}/drafts/{filename}/sections/{sec_id} → upsert one section (for CLI push)
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field

from klemma.models import UserRecord

from ..auth.deps import get_current_user, get_user_store

logger = logging.getLogger(__name__)

router = APIRouter()

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_DEFAULT_FILENAME = {"dissertation": "dissertation.md", "paper": "paper.md"}
_SAFE_FILENAME = re.compile(r"^[\w.\-]+\.md$")
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)
_SECTION_ID_RE = re.compile(r"^([\d]+(?:\.[\d]+)*)\s*(.*)")
_NON_WORD_RE = re.compile(r"[^\w\s-]")
_WHITESPACE_RE = re.compile(r"[\s_]+")


def _slugify(text: str) -> str:
    """Lowercase slug from heading text, preserving Unicode letters (Cyrillic etc.)."""
    slug = _NON_WORD_RE.sub("", text.lower())
    slug = _WHITESPACE_RE.sub("-", slug)
    return slug.strip("-")

# ---------------------------------------------------------------------------
# Helpers — filesystem / git
# ---------------------------------------------------------------------------


def _data_dir() -> Path:
    return Path(os.environ.get("KLEMMA_DATA_DIR", str(Path.home() / ".klemma")))


def _project_dir(project_id: str) -> Path:
    """Git repo root for a project's drafts."""
    return _data_dir() / "drafts" / project_id


def _drafts_dir(project_id: str) -> Path:
    """Directory where draft .md files live (inside the git repo)."""
    return _data_dir() / "drafts" / project_id / "draft"


def _validate_filename(name: str) -> str:
    if not _SAFE_FILENAME.match(name) or ".." in name:
        raise HTTPException(status_code=400, detail=f"Invalid filename: {name!r}")
    return name


def _assert_project_owner(project_id: str, user: UserRecord) -> dict:
    store = get_user_store()
    project = store.get_project_by_id(project_id)
    if not project or project["user_id"] != user.user_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    return project


def _ensure_git_repo(project_dir: Path) -> None:
    if (project_dir / ".git").exists():
        return
    project_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-b", "main", str(project_dir)],
                   capture_output=True, check=True, timeout=10)
    subprocess.run(["git", "-C", str(project_dir), "config", "user.email", "klemma@klemma.ai"],
                   capture_output=True, check=True, timeout=5)
    subprocess.run(["git", "-C", str(project_dir), "config", "user.name", "Klemma SaaS"],
                   capture_output=True, check=True, timeout=5)


def _git_remove(project_dir: Path, filepath: Path, message: str) -> str:
    """Stage deletion of a file and commit. Returns commit hash or ''."""
    rel = filepath.relative_to(project_dir)
    # `git rm --force` removes from both working tree and index; no-op if not tracked
    subprocess.run(["git", "-C", str(project_dir), "rm", "--force", str(rel)],
                   capture_output=True, timeout=10)
    result = subprocess.run(
        ["git", "-C", str(project_dir), "commit", "-m", message, "--allow-empty-message"],
        capture_output=True, text=True, timeout=10,
    )
    m = re.search(r"\[(?:\w+ )?([0-9a-f]+)\]", result.stdout)
    return m.group(1) if m else ""


def _git_commit(project_dir: Path, filepath: Path, message: str) -> str:
    # project_dir = git repo root; filepath is absolute, e.g. .../drafts/{id}/draft/chapter_1.md
    # rel becomes "draft/chapter_1.md" — the path git needs to stage
    rel = filepath.relative_to(project_dir)
    subprocess.run(["git", "-C", str(project_dir), "add", str(rel)],
                   capture_output=True, check=True, timeout=10)
    result = subprocess.run(
        ["git", "-C", str(project_dir), "commit", "-m", message, "--allow-empty-message"],
        capture_output=True, text=True, timeout=10,
    )
    m = re.search(r"\[(?:\w+ )?([0-9a-f]+)\]", result.stdout)
    return m.group(1) if m else ""


# ---------------------------------------------------------------------------
# Helpers — Markdown parsing
# ---------------------------------------------------------------------------


def _section_level(section_id: str) -> int:
    """'1' → 1, '1.1' → 2, '1.1.1' → 3"""
    return section_id.count(".") + 1


def _heading_marker(section_id: str) -> str:
    """Return ## or ### etc. for a section id. Level 1 → ##, level 2 → ###"""
    return "#" * (_section_level(section_id) + 1)


def parse_headings(content: str) -> list[dict]:
    """Return list of {level, section_id, title, full_title, line} from Markdown content.

    Numeric headings (e.g. "1", "1.4", "1.4.2") get their numeric section_id.
    Non-numeric headings get slug section_ids (e.g. "Introduction" → "introduction").
    """
    headings = []
    for i, line in enumerate(content.splitlines()):
        m = re.match(r"^(#{1,6})\s+(.+)$", line)
        if not m:
            continue
        level = len(m.group(1))
        full_title = m.group(2).strip()
        sid_m = _SECTION_ID_RE.match(full_title)
        if sid_m:
            section_id = sid_m.group(1)
            title = sid_m.group(2).strip() or section_id
        else:
            section_id = _slugify(full_title)
            title = full_title
        headings.append({
            "level": level,
            "section_id": section_id,
            "title": title,
            "full_title": full_title,
            "line": i,
        })
    return headings


def extract_section(content: str, section_id: str) -> tuple[str, int, int] | None:
    """Return (body_text, heading_line, end_line_exclusive) or None.

    End detection only stops at numeric section headings (e.g. "### 1.5 Title"),
    not at prose headings (e.g. "### Перспективы развития") inside the body.
    """
    lines = content.splitlines(keepends=True)
    start = None
    start_level = None

    for i, line in enumerate(lines):
        m = re.match(r"^(#{1,6})\s+([\d]+(?:\.[\d]+)*)", line)
        if m and m.group(2) == section_id:
            start = i
            start_level = len(m.group(1))
            break

    if start is None:
        return None

    for i in range(start + 1, len(lines)):
        # Only numeric section headings terminate a section — prose headings are body content
        m = re.match(r"^(#{1,6})\s+([\d]+(?:\.[\d]+)*)", lines[i])
        if m and len(m.group(1)) <= start_level:
            end = i
            break
    else:
        end = len(lines)

    body = "".join(lines[start + 1 : end]).strip()
    return body, start, end


def upsert_section(content: str, section_id: str, new_body: str,
                   heading_title: Optional[str] = None) -> str:
    """Replace section body in content. Appends new section if not found."""
    # Strip any leading section heading the AI or editor may have prepended to the body.
    # E.g. if new_body starts with "### 1.4 Title\n\n..." strip the heading line.
    stripped = new_body.lstrip("\n")
    m = re.match(r"^#{1,6}\s+" + re.escape(section_id) + r"[^\n]*\n?", stripped)
    if m:
        new_body = stripped[m.end():].lstrip("\n")

    result = extract_section(content, section_id)

    if result is None:
        # Append new section at end
        marker = _heading_marker(section_id)
        title_str = heading_title or section_id
        heading_line = f"{marker} {section_id} {title_str}".rstrip()
        return content.rstrip() + f"\n\n{heading_line}\n\n{new_body.strip()}\n"

    _body, start, end = result
    lines = content.splitlines(keepends=True)
    heading_line = lines[start]  # preserve original heading exactly
    new_lines_str = heading_line + "\n" + new_body.strip() + "\n\n"
    lines[start:end] = [new_lines_str]
    return "".join(lines)


def _init_content(project_type: str, outline: list[dict]) -> str:
    """Generate initial Markdown content from project outline."""
    doc_title = "Диссертация" if project_type == "dissertation" else "Статья"
    lines = [f"# {doc_title}", ""]
    for sec in outline:
        sid = sec.get("id", "")
        name = sec.get("name", sid)
        marker = _heading_marker(sid)
        lines.append(f"{marker} {sid} {name}")
        lines.append("")
        lines.append("> _Добавьте текст раздела здесь._")
        lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class HeadingInfo(BaseModel):
    level: int
    section_id: str
    title: str
    full_title: str
    line: int


class FileInfo(BaseModel):
    name: str
    headings: list[HeadingInfo]
    word_count: int
    updated_at: str = ""  # ISO 8601 UTC, e.g. "2026-03-28T12:00:00+00:00"


class FileListResponse(BaseModel):
    files: list[FileInfo]


class FileContentResponse(BaseModel):
    name: str
    content: str
    headings: list[HeadingInfo]
    word_count: int


class FileSaveRequest(BaseModel):
    content: str = Field(..., max_length=2_000_000)


class InitDraftRequest(BaseModel):
    filename: Optional[str] = None  # defaults to dissertation.md / paper.md


class SectionUpsertRequest(BaseModel):
    body: str = Field(..., max_length=500_000)
    heading_title: Optional[str] = None


class SectionUpsertResponse(BaseModel):
    section_id: str
    filename: str
    commit: str


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/{project_id}/drafts", response_model=FileListResponse)
def list_draft_files(
    project_id: str,
    user: UserRecord = Depends(get_current_user),
) -> FileListResponse:
    """List all .md draft files for a project with parsed headings."""
    _assert_project_owner(project_id, user)
    draft_dir = _drafts_dir(project_id)
    files = []
    if draft_dir.exists():
        for md in sorted(draft_dir.glob("*.md")):
            content = md.read_text(encoding="utf-8")
            headings = [HeadingInfo(**h) for h in parse_headings(content)]
            wc = len(content.split())
            mtime = md.stat().st_mtime
            updated_at = datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat()
            files.append(FileInfo(name=md.name, headings=headings, word_count=wc,
                                  updated_at=updated_at))
    return FileListResponse(files=files)


@router.get("/{project_id}/drafts/{filename}", response_model=FileContentResponse)
def get_draft_file(
    project_id: str,
    filename: str,
    user: UserRecord = Depends(get_current_user),
) -> FileContentResponse:
    """Get full content + parsed headings for a draft file."""
    _validate_filename(filename)
    _assert_project_owner(project_id, user)

    path = _drafts_dir(project_id) / filename
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"{filename} not found")

    content = path.read_text(encoding="utf-8")
    headings = [HeadingInfo(**h) for h in parse_headings(content)]
    return FileContentResponse(
        name=filename,
        content=content,
        headings=headings,
        word_count=len(content.split()),
    )


@router.put("/{project_id}/drafts/{filename}", response_model=FileContentResponse)
def save_draft_file(
    project_id: str,
    filename: str,
    body: FileSaveRequest,
    user: UserRecord = Depends(get_current_user),
) -> FileContentResponse:
    """Save full file content and commit to git."""
    _validate_filename(filename)
    _assert_project_owner(project_id, user)

    project_dir = _project_dir(project_id)
    draft_dir = _drafts_dir(project_id)
    _ensure_git_repo(project_dir)
    draft_dir.mkdir(parents=True, exist_ok=True)
    path = draft_dir / filename
    path.write_text(body.content, encoding="utf-8")

    wc = len(body.content.split())
    try:
        _git_commit(project_dir, path, f"{filename}: {wc}w")
    except Exception as exc:
        logger.warning("git commit failed for %s/%s: %s", project_id, filename, exc)

    headings = [HeadingInfo(**h) for h in parse_headings(body.content)]
    return FileContentResponse(name=filename, content=body.content,
                               headings=headings, word_count=wc)


@router.post("/{project_id}/drafts/init", response_model=FileContentResponse,
             status_code=status.HTTP_201_CREATED)
def init_draft_file(
    project_id: str,
    body: InitDraftRequest,
    user: UserRecord = Depends(get_current_user),
) -> FileContentResponse:
    """Create the initial draft file from the project outline.

    Uses the project's type to pick a default filename (dissertation.md / paper.md).
    Idempotent: if the file already exists, returns it unchanged.
    """
    project = _assert_project_owner(project_id, user)
    project_type = project.get("type", "dissertation")
    filename = body.filename or _DEFAULT_FILENAME.get(project_type, "draft.md")
    _validate_filename(filename)

    project_dir = _project_dir(project_id)
    draft_dir = _drafts_dir(project_id)
    _ensure_git_repo(project_dir)
    draft_dir.mkdir(parents=True, exist_ok=True)
    path = draft_dir / filename

    if path.exists():
        content = path.read_text(encoding="utf-8")
    else:
        import json
        outline_raw = project.get("outline") or "[]"
        outline = json.loads(outline_raw) if isinstance(outline_raw, str) else outline_raw
        content = _init_content(project_type, outline)
        path.write_text(content, encoding="utf-8")
        try:
            _git_commit(project_dir, path, f"init {filename}")
        except Exception as exc:
            logger.warning("git commit failed on init: %s", exc)

    headings = [HeadingInfo(**h) for h in parse_headings(content)]
    return FileContentResponse(name=filename, content=content,
                               headings=headings, word_count=len(content.split()))


@router.delete("/{project_id}/drafts/{filename}", status_code=status.HTTP_204_NO_CONTENT)
def delete_draft_file(
    project_id: str,
    filename: str,
    user: UserRecord = Depends(get_current_user),
) -> Response:
    """Delete a draft file and record the deletion in git.

    Returns 404 if the file does not exist. The deletion is committed to the
    project's draft git repo so the history is preserved.
    """
    _validate_filename(filename)
    _assert_project_owner(project_id, user)

    path = _drafts_dir(project_id) / filename
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"{filename} not found")

    project_dir = _project_dir(project_id)
    try:
        _git_remove(project_dir, path, f"delete {filename}")
    except Exception as exc:
        logger.warning("git remove failed for %s/%s: %s", project_id, filename, exc)
        # File may not be in git yet — remove from filesystem directly
        if path.exists():
            path.unlink()

    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.put("/{project_id}/drafts/{filename}/sections/{section_id}",
            response_model=SectionUpsertResponse)
def upsert_draft_section(
    project_id: str,
    filename: str,
    section_id: str,
    body: SectionUpsertRequest,
    user: UserRecord = Depends(get_current_user),
) -> SectionUpsertResponse:
    """Replace (or append) a section's body within a file. Used by klemma-cli push."""
    _validate_filename(filename)
    _assert_project_owner(project_id, user)

    project_dir = _project_dir(project_id)
    draft_dir = _drafts_dir(project_id)
    _ensure_git_repo(project_dir)
    draft_dir.mkdir(parents=True, exist_ok=True)
    path = draft_dir / filename

    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    updated = upsert_section(existing, section_id, body.body, body.heading_title)
    path.write_text(updated, encoding="utf-8")

    wc = len(body.body.split())
    commit_hash = ""
    try:
        commit_hash = _git_commit(project_dir, path,
                                  f"section {section_id}: {wc}w")
    except Exception as exc:
        logger.warning("git commit failed for section %s: %s", section_id, exc)

    return SectionUpsertResponse(section_id=section_id, filename=filename,
                                 commit=commit_hash)
