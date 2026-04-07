"""Write endpoints: research briefings and draft generation (ADR-009, #99)."""

from __future__ import annotations

import asyncio
import os
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from klemma.models import UserRecord

from ..auth.deps import get_current_user, get_user_store
from .process import _run_local_job

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
    word_target: int | None = None
    instruction: str | None = None


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
    if body.project_id:
        store = get_user_store()
        project = store.get_project_by_id(body.project_id)
        if not project or project["user_id"] != user.user_id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    return await _enqueue_write_task("generate_research", body.section, body.project_id, user.user_id)


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
    if body.project_id:
        store = get_user_store()
        project = store.get_project_by_id(body.project_id)
        if not project or project["user_id"] != user.user_id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    return await _enqueue_write_task(
        "generate_draft", body.section, body.project_id, user.user_id,
        body.word_target, body.instruction,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _enqueue_write_task(
    task_name: str, section: str, project_id: str | None = None, user_id: str = "",
    word_target: int | None = None, instruction: str | None = None,
) -> WriteJobResponse:
    """Enqueue a write task via rq, falling back to in-process thread when Redis is unavailable."""
    from ..tasks import generate_draft, generate_research

    data_dir = os.environ.get("KLEMMA_DATA_DIR", str(Path.home() / ".klemma"))
    fn = generate_research if task_name == "generate_research" else generate_draft
    if task_name == "generate_research":
        args = (section, project_id or "", data_dir, user_id)
    else:
        args = (section, data_dir, project_id or "", user_id, word_target or 0, instruction or "")

    # Try Redis first
    if _RQ_AVAILABLE:
        try:
            redis_url = os.getenv("REDIS_URL", "redis://localhost:6379")
            redis_conn = Redis.from_url(redis_url)
            q = Queue(connection=redis_conn)
            job = q.enqueue(fn, *args, job_timeout=600)
            return WriteJobResponse(job_id=job.id, status="queued", section=section, task_type=task_name)
        except Exception:
            pass  # Redis unavailable — fall through to local execution

    # Local fallback: run in asyncio thread pool
    job_id = str(uuid.uuid4())
    asyncio.create_task(_run_local_job(job_id, fn, *args))
    return WriteJobResponse(job_id=job_id, status="queued", section=section, task_type=task_name)
