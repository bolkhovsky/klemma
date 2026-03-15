"""Analyze endpoints: status, coverage, gaps (ADR-009, #99)."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from klemma.models import UserRecord

from ..auth.deps import get_current_user
from ..deps import get_paper_store, get_project_store, get_user_library

router = APIRouter()


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class SourceStats(BaseModel):
    """Summary statistics for the user's sources."""

    total: int
    completed: int
    pending: int
    failed: int


class SectionCoverage(BaseModel):
    """Coverage data for a single section."""

    section: str
    source_count: int


class StatusResponse(BaseModel):
    """Full project status — the SaaS equivalent of `klemma status`."""

    sources: SourceStats
    coverage: list[SectionCoverage]
    total_fragments: int


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/status", response_model=StatusResponse)
async def get_status(
    user: UserRecord = Depends(get_current_user),
) -> StatusResponse:
    """Get project status: source counts, coverage by section, fragment count.

    This is the SaaS equivalent of `klemma status` — the most-used CLI command.
    Composes data from UserLibrary (source counts) and ProjectStore (coverage).
    """
    library = get_user_library()
    project_store = get_project_store()
    paper_store = get_paper_store()

    # Source counts by status
    all_sources = library.get_all_sources()
    completed = sum(1 for s in all_sources if s.status == "completed")
    pending = sum(1 for s in all_sources if s.status == "pending")
    failed = sum(1 for s in all_sources if s.status == "failed")

    # Coverage by section from ProjectStore
    stats = project_store.get_coverage_stats()
    sections = stats.get("sections", {})
    coverage = [
        SectionCoverage(section=str(sec), source_count=cnt)
        for sec, cnt in sorted(sections.items())
    ]

    # Total fragments across all sources
    total_fragments = 0
    for src in all_sources:
        frags = paper_store.get_fragments(src.paper_id)
        total_fragments += len(frags)

    return StatusResponse(
        sources=SourceStats(
            total=len(all_sources),
            completed=completed,
            pending=pending,
            failed=failed,
        ),
        coverage=coverage,
        total_fragments=total_fragments,
    )
