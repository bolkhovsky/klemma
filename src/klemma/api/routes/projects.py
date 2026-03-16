"""Project endpoints: user project CRUD + coverage stats + section assignments (ADR-009, #204)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from klemma.models import UserRecord

from ..auth.deps import get_current_user, get_user_store
from ..deps import get_project_store, get_user_library

router = APIRouter()


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class ProjectResponse(BaseModel):
    """A user project."""

    project_id: str
    name: str
    type: str
    created_at: str = ""


class ProjectListResponse(BaseModel):
    projects: list[ProjectResponse]


class ProjectCreateRequest(BaseModel):
    name: str
    type: str = "dissertation"


class ProjectRenameRequest(BaseModel):
    name: str


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
