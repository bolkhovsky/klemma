"""Process endpoints: async job submission and status (ADR-009, #186)."""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from klemma.models import UserRecord

from ..auth.deps import get_current_user
from ..deps import get_user_library

try:
    from redis import Redis
    from rq import Queue
    from rq.job import Job

    _RQ_AVAILABLE = True
except ImportError:
    _RQ_AVAILABLE = False

router = APIRouter()


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class JobSubmitResponse(BaseModel):
    """Response when a job is enqueued."""

    job_id: str
    status: str
    citekey: str


class JobStatusResponse(BaseModel):
    """Job status check response."""

    job_id: str
    status: str  # queued, started, finished, failed, deferred
    result: dict | None = None


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post(
    "/sources/{citekey}",
    response_model=JobSubmitResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def submit_process_job(
    citekey: str,
    user: UserRecord = Depends(get_current_user),
) -> JobSubmitResponse:
    """Enqueue a source for async extraction processing.

    Returns 202 with a job_id that can be polled via GET /process/jobs/{job_id}.
    """
    if not _RQ_AVAILABLE:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Redis/rq not available — install klemma[api]",
        )

    library = get_user_library()
    src = library.get_source_by_citekey(citekey)
    if src is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Source '{citekey}' not found in library",
        )

    try:
        redis_url = os.getenv("REDIS_URL", "redis://localhost:6379")
        redis_conn = Redis.from_url(redis_url)
        q = Queue(connection=redis_conn)

        from ..tasks import process_source

        data_dir = os.environ.get("KLEMMA_DATA_DIR", str(Path.home() / ".klemma"))
        job = q.enqueue(
            process_source,
            src.paper_id,
            citekey,
            data_dir,
            job_timeout=300,
        )
        return JobSubmitResponse(job_id=job.id, status="queued", citekey=citekey)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Redis unavailable: {type(exc).__name__}",
        )


@router.get("/jobs/{job_id}", response_model=JobStatusResponse)
async def get_job_status(
    job_id: str,
    user: UserRecord = Depends(get_current_user),
) -> JobStatusResponse:
    """Check the status of an async processing job."""
    if not _RQ_AVAILABLE:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Redis/rq not available",
        )

    from redis.exceptions import ConnectionError as RedisConnectionError

    try:
        redis_url = os.getenv("REDIS_URL", "redis://localhost:6379")
        redis_conn = Redis.from_url(redis_url)
        job = Job.fetch(job_id, connection=redis_conn)

        return JobStatusResponse(
            job_id=job_id,
            status=job.get_status(),
            result=job.result if job.is_finished else None,
        )
    except RedisConnectionError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Redis unavailable",
        )
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Job '{job_id}' not found",
        )
