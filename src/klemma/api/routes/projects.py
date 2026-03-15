"""Project endpoints: coverage stats and section assignments (ADR-009, #99)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from klemma.models import UserRecord

from ..auth.deps import get_current_user
from ..deps import get_project_store, get_user_library

router = APIRouter()


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


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
# Endpoints
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
