"""Write endpoints: research briefings and draft generation (ADR-009, #99)."""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from klemma.models import UserRecord

from ..auth.deps import get_current_user

try:
    from redis import Redis
    from rq import Queue

    _RQ_AVAILABLE = True
except ImportError:
    _RQ_AVAILABLE = False

router = APIRouter()


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class WriteJobRequest(BaseModel):
    """Request to generate research briefing or draft for a section."""

    section: str
    project_id: str | None = None


class WriteJobResponse(BaseModel):
    """Response when a write job is enqueued."""

    job_id: str
    status: str
    section: str
    task_type: str


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post(
    "/research",
    response_model=WriteJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def submit_research_job(
    body: WriteJobRequest,
    user: UserRecord = Depends(get_current_user),
) -> WriteJobResponse:
    """Enqueue a research briefing generation for a section.

    Returns 202 with a job_id. Poll status via GET /process/jobs/{job_id}.
    """
    return _enqueue_write_task("generate_research", body.section, body.project_id, user.user_id)


@router.post(
    "/draft",
    response_model=WriteJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def submit_draft_job(
    body: WriteJobRequest,
    user: UserRecord = Depends(get_current_user),
) -> WriteJobResponse:
    """Enqueue a section draft generation.

    Returns 202 with a job_id. Poll status via GET /process/jobs/{job_id}.
    """
    return _enqueue_write_task("generate_draft", body.section)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _enqueue_write_task(
    task_name: str, section: str, project_id: str | None = None, user_id: str = ""
) -> WriteJobResponse:
    """Enqueue a write task (research or draft) via rq."""
    if not _RQ_AVAILABLE:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Redis/rq not available — install klemma[api]",
        )

    try:
        redis_url = os.getenv("REDIS_URL", "redis://localhost:6379")
        redis_conn = Redis.from_url(redis_url)
        q = Queue(connection=redis_conn)

        from ..tasks import generate_draft, generate_research

        data_dir = os.environ.get("KLEMMA_DATA_DIR", str(Path.home() / ".klemma"))

        if task_name == "generate_research":
            job = q.enqueue(
                generate_research, section, project_id or "", data_dir, user_id,
                job_timeout=600,
            )
        else:
            job = q.enqueue(generate_draft, section, data_dir, job_timeout=600)

        return WriteJobResponse(
            job_id=job.id, status="queued", section=section, task_type=task_name
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Redis unavailable: {type(exc).__name__}",
        )
