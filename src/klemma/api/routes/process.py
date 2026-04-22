"""Process endpoints: async job submission and status (ADR-009, #186)."""

from __future__ import annotations

import asyncio
import os
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel

from klemma.models import UserRecord

from ..auth.deps import get_current_user, get_user_store
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
# In-memory job store — Redis-free fallback for local development
# ---------------------------------------------------------------------------

# Keyed by job_id → {"status": str, "result": dict | None}
# Populated by _run_local_job(); checked by get_job_status() before Redis.
_local_jobs: dict[str, dict] = {}


async def _run_local_job(job_id: str, fn: Any, *args: Any) -> None:
    """Run fn(*args) in a thread pool; store result in _local_jobs."""
    _local_jobs[job_id] = {"status": "started", "result": None}
    try:
        result = await asyncio.to_thread(fn, *args)
        _local_jobs[job_id] = {"status": "finished", "result": result}
    except Exception as exc:
        _local_jobs[job_id] = {"status": "failed", "result": {"error": str(exc)}}


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


def _resolve_status(raw_status: str, result: Any) -> str:
    """Promote business-logic errors in the result payload to top-level 'failed'.

    Tasks return ``{"status": "error", "detail": ...}`` for recoverable failures
    (token exhaustion, missing PDF, AI timeout). Without this, callers see the
    outer status as 'finished' and must defensively unwrap result.status.
    """
    if raw_status == "finished" and isinstance(result, dict) and result.get("status") == "error":
        return "failed"
    return raw_status


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
    project_id: str | None = Query(default=None, description="Project for section assignment context"),
    force: bool = Query(default=False, description="Force reprocess even if already completed"),
) -> JobSubmitResponse:
    """Enqueue a source for async extraction processing.

    Returns 202 with a job_id that can be polled via GET /process/jobs/{job_id}.
    Falls back to in-process thread execution when Redis is unavailable.
    """
    library = get_user_library()
    # Dual-key: accept either internal citekey or external_citekey.
    src = library.get_source_by_any_key(citekey, user_id=user.user_id)
    if src is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Source '{citekey}' not found in library",
        )
    # Use internal citekey for all downstream DB writes (fragments, curation).
    citekey = src.citekey

    # Validate project ownership — project_id is a write path (auto-suggestion)
    if project_id:
        store = get_user_store()
        proj = store.get_project_by_id(project_id)
        if not proj or proj["user_id"] != user.user_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Project not found"
            )

    from ..tasks import process_source

    data_dir = os.environ.get("KLEMMA_DATA_DIR", str(Path.home() / ".klemma"))

    # Try Redis first; fall back to in-process thread when unavailable
    if _RQ_AVAILABLE:
        try:
            redis_url = os.getenv("REDIS_URL", "redis://localhost:6379")
            redis_conn = Redis.from_url(redis_url)
            q = Queue(connection=redis_conn)
            job = q.enqueue(
                process_source,
                src.paper_id,
                citekey,
                data_dir,
                user.user_id,
                project_id,
                force,
                job_timeout=300,
            )
            return JobSubmitResponse(job_id=job.id, status="queued", citekey=citekey)
        except Exception:
            pass  # Redis unavailable — fall through to local execution

    # Local fallback: run in asyncio thread pool, no external dependencies
    job_id = str(uuid.uuid4())
    asyncio.create_task(
        _run_local_job(job_id, process_source, src.paper_id, citekey, data_dir, user.user_id, project_id, force)
    )
    return JobSubmitResponse(job_id=job_id, status="queued", citekey=citekey)


@router.get("/jobs/{job_id}", response_model=JobStatusResponse)
async def get_job_status(
    job_id: str,
    user: UserRecord = Depends(get_current_user),
) -> JobStatusResponse:
    """Check the status of an async processing job."""
    # Check local job store first (populated when Redis is unavailable)
    if job_id in _local_jobs:
        j = _local_jobs[job_id]
        return JobStatusResponse(
            job_id=job_id,
            status=_resolve_status(j["status"], j["result"]),
            result=j["result"],
        )

    # Check Redis
    if not _RQ_AVAILABLE:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Job '{job_id}' not found",
        )

    from redis.exceptions import ConnectionError as RedisConnectionError

    try:
        redis_url = os.getenv("REDIS_URL", "redis://localhost:6379")
        redis_conn = Redis.from_url(redis_url)
        job = Job.fetch(job_id, connection=redis_conn)

        result = job.result if job.is_finished else None
        return JobStatusResponse(
            job_id=job_id,
            status=_resolve_status(job.get_status(), result),
            result=result,
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
