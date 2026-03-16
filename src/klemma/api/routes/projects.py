"""Project endpoints: user project CRUD + coverage stats + section assignments (ADR-009, #204)."""

from __future__ import annotations

import logging
import os
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from klemma.models import UserRecord

from ..auth.deps import get_current_user, get_user_store
from ..deps import get_project_store, get_user_library

try:
    from redis import Redis
    from rq import Queue

    _RQ_AVAILABLE = True
except ImportError:
    _RQ_AVAILABLE = False

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class OutlineSection(BaseModel):
    """A single section in the project outline."""

    id: str
    name: str


class ProjectResponse(BaseModel):
    """A user project."""

    project_id: str
    name: str
    type: str
    created_at: str = ""
    outline: list[OutlineSection] | None = None


class ProjectListResponse(BaseModel):
    projects: list[ProjectResponse]


class ProjectCreateRequest(BaseModel):
    name: str
    type: str = "dissertation"


class ProjectRenameRequest(BaseModel):
    name: str


class OutlineUpdateRequest(BaseModel):
    sections: list[OutlineSection]


class OutlineGenerateRequest(BaseModel):
    context_text: str


class CoverageStatsResponse(BaseModel):
    """Coverage statistics for the project."""

    total_sources: int
    sections: dict[str, int]
    chapters: dict[str, int]


class SectionSourcesResponse(BaseModel):
    """Sources assigned to a section."""

    section: str
    citekeys: list[str]
    count: int


class AssignSectionRequest(BaseModel):
    """Assign a source to sections."""

    citekey: str
    sections: list[str]
    chapters: list[int] = []


# ---------------------------------------------------------------------------
# Endpoints — Project CRUD
# ---------------------------------------------------------------------------


@router.get("", response_model=ProjectListResponse)
async def list_projects(
    user: UserRecord = Depends(get_current_user),
) -> ProjectListResponse:
    """List all projects for the current user."""
    store = get_user_store()
    rows = store.get_projects(user.user_id)
    return ProjectListResponse(
        projects=[ProjectResponse(**r) for r in rows]
    )


@router.post("", response_model=ProjectResponse, status_code=status.HTTP_201_CREATED)
async def create_project(
    body: ProjectCreateRequest,
    user: UserRecord = Depends(get_current_user),
) -> ProjectResponse:
    """Create a new project."""
    store = get_user_store()
    project = store.create_project(user.user_id, body.name, body.type)
    return ProjectResponse(**project)


@router.patch("/{project_id}", response_model=ProjectResponse)
async def rename_project(
    project_id: str,
    body: ProjectRenameRequest,
    user: UserRecord = Depends(get_current_user),
) -> ProjectResponse:
    """Rename a project. Only the owner can rename it."""
    store = get_user_store()
    project = store.get_project_by_id(project_id)
    if not project or project["user_id"] != user.user_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    store.rename_project(project_id, body.name)
    project["name"] = body.name
    return ProjectResponse(**project)


@router.patch("/{project_id}/outline", response_model=ProjectResponse)
async def update_project_outline(
    project_id: str,
    body: OutlineUpdateRequest,
    user: UserRecord = Depends(get_current_user),
) -> ProjectResponse:
    """Update the outline (section list) for a project. Only the owner can update it."""
    store = get_user_store()
    project = store.get_project_by_id(project_id)
    if not project or project["user_id"] != user.user_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    sections = [{"id": s.id, "name": s.name} for s in body.sections]
    store.update_project_outline(project_id, sections)
    project["outline"] = sections
    return ProjectResponse(**project)


@router.post("/{project_id}/outline/generate")
async def generate_project_outline(
    project_id: str,
    body: OutlineGenerateRequest,
    user: UserRecord = Depends(get_current_user),
) -> dict:
    """Enqueue AI outline generation from a plan-prospekt or thesis text."""
    store = get_user_store()
    project = store.get_project_by_id(project_id)
    if not project or project["user_id"] != user.user_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")

    if not _RQ_AVAILABLE:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Job queue unavailable",
        )

    try:
        redis_url = os.getenv("REDIS_URL", "redis://localhost:6379")
        redis_conn = Redis.from_url(redis_url)
        q = Queue(connection=redis_conn)
        from ..tasks import generate_outline_saas

        data_dir = os.environ.get("KLEMMA_DATA_DIR", str(Path.home() / ".klemma"))
        job = q.enqueue(
            generate_outline_saas,
            project_id,
            body.context_text,
            project.get("type", "dissertation"),
            data_dir,
            user.user_id,
            job_timeout=120,
        )
        return {"job_id": job.id, "status": "queued"}
    except Exception as exc:
        logger.error("Outline generation enqueue failed for project %s: %s", project_id, exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to enqueue outline generation job",
        )


# ---------------------------------------------------------------------------
# Endpoints — Coverage & Sections
# ---------------------------------------------------------------------------


@router.get("/coverage", response_model=CoverageStatsResponse)
async def get_coverage(
    user: UserRecord = Depends(get_current_user),
) -> CoverageStatsResponse:
    """Get coverage statistics for the project."""
    store = get_project_store()
    stats = store.get_coverage_stats()
    return CoverageStatsResponse(
        total_sources=stats["total_sources"],
        sections={str(k): v for k, v in stats.get("sections", {}).items()},
        chapters={str(k): v for k, v in stats.get("chapters", {}).items()},
    )


@router.get("/sections/{section}/sources", response_model=SectionSourcesResponse)
async def get_section_sources(
    section: str,
    user: UserRecord = Depends(get_current_user),
) -> SectionSourcesResponse:
    """List sources assigned to a specific section."""
    store = get_project_store()
    citekeys = store.get_sources_by_section(section)
    return SectionSourcesResponse(
        section=section,
        citekeys=citekeys,
        count=len(citekeys),
    )


@router.post("/sections/assign", status_code=status.HTTP_200_OK)
async def assign_source_sections(
    body: AssignSectionRequest,
    user: UserRecord = Depends(get_current_user),
) -> dict:
    """Assign a source to sections in the project."""
    store = get_project_store()
    library = get_user_library()

    # Verify source exists in user's library
    src = library.get_source_by_citekey(body.citekey)
    if src is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Source '{body.citekey}' not found in library",
        )

    store.set_source_sections(
        body.citekey, src.paper_id, body.sections, body.chapters
    )
    return {"citekey": body.citekey, "sections": body.sections}


@router.get("/sources/{citekey}/sections")
async def get_source_sections(
    citekey: str,
    user: UserRecord = Depends(get_current_user),
) -> dict:
    """Get sections assigned to a source in the project."""
    store = get_project_store()
    sections = store.get_source_sections(citekey)
    return {"citekey": citekey, "sections": sections}


# ---------------------------------------------------------------------------
# Endpoints — Research Reports
# ---------------------------------------------------------------------------


@router.get("/{project_id}/research")
async def list_research_reports(
    project_id: str,
    user: UserRecord = Depends(get_current_user),
) -> dict:
    """List all research reports for a project."""
    store = get_user_store()
    project = store.get_project_by_id(project_id)
    if not project or project["user_id"] != user.user_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    reports = store.get_project_research_reports(project_id)
    return {"project_id": project_id, "reports": reports}


@router.get("/{project_id}/research/{section:path}")
async def get_research_report(
    project_id: str,
    section: str,
    user: UserRecord = Depends(get_current_user),
) -> dict:
    """Get the stored research report for a project section."""
    store = get_user_store()
    project = store.get_project_by_id(project_id)
    if not project or project["user_id"] != user.user_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    report = store.get_research_report(project_id, section)
    if not report:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No report for this section")
    return {
        "section": report["section"],
        "report_text": report["report_text"],
        "model": report["model"],
        "created_at": report["created_at"],
        "input_tokens": report.get("input_tokens", 0),
        "output_tokens": report.get("output_tokens", 0),
    }
