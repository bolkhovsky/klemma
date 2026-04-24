"""Admin endpoints for maintenance operations (backfills, data migrations).

All endpoints require admin privileges (first registered user).
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel

from klemma.models import UserRecord

from ..auth.deps import get_current_user, get_user_store

router = APIRouter()
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Admin guard
# ---------------------------------------------------------------------------


def _require_admin(user: UserRecord) -> None:
    """Raise HTTP 403 unless the user is the first registered user (admin).

    Matches the convention used by usage.py /usage/grant.
    """
    store = get_user_store()
    with store._conn() as conn:
        first = conn.execute(
            "SELECT user_id FROM users ORDER BY created_at ASC LIMIT 1"
        ).fetchone()
    if not first or first["user_id"] != user.user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin only",
        )


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class BackfillIntentsResponse(BaseModel):
    """Result of a citation intent backfill batch."""

    processed: int
    skipped_no_raw_text: int
    failed: int
    next_cursor: str | None
    remaining: int


class ReprocessAllRequest(BaseModel):
    user_id: str
    dry_run: bool = True
    min_fragments: int = 0
    allow_inline: bool = False


class ReprocessAllResponse(BaseModel):
    papers: int
    enqueued: int
    dry_run: bool
    estimated_chunks: int


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post(
    "/backfill/citation-intents",
    response_model=BackfillIntentsResponse,
    summary="Backfill citation intents for a user's papers",
)
async def backfill_citation_intents_route(
    target_user_id: str = Query(..., description="user_id whose papers to backfill"),
    batch_size: int = Query(default=20, ge=1, le=100, description="Papers per batch"),
    cursor: str | None = Query(default=None, description="Resume cursor from previous call"),
    dry_run: bool = Query(default=False, description="Run AI extraction but skip DB writes"),
    user: UserRecord = Depends(get_current_user),
) -> BackfillIntentsResponse:
    """Backfill citation_intent for existing citation_graph entries.

    Uses the paper's cached raw_text to call the AI and infer citation intents
    for bibliography entries that were extracted before intent detection was added.

    Cursor-based pagination: pass ``next_cursor`` from the previous response
    to process the next batch. Repeat until ``remaining == 0``.

    Set ``dry_run=true`` to see what would be updated without mutating the DB.

    Returns 503 if the AI provider is not configured.
    """
    _require_admin(user)

    data_dir = os.environ.get("KLEMMA_DATA_DIR", str(Path.home() / ".klemma"))

    from ..tasks import backfill_citation_intents

    try:
        result = backfill_citation_intents(
            user_id=target_user_id,
            data_dir=data_dir,
            batch_size=batch_size,
            cursor=cursor or None,
            dry_run=dry_run,
        )
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        )

    return BackfillIntentsResponse(**result)


@router.post(
    "/backfill/reprocess-all",
    response_model=ReprocessAllResponse,
    summary="Re-extract fragments for all completed papers (chunked extraction)",
)
async def backfill_reprocess_all(
    body: ReprocessAllRequest,
    user: UserRecord = Depends(get_current_user),
) -> ReprocessAllResponse:
    """Enqueue reprocess_paper jobs for all completed sources of a user.

    **dry_run=true** (default): returns paper count and estimated chunk count
    without enqueueing any jobs.  Set ``dry_run=false`` to actually enqueue.

    ``min_fragments``: skip papers that already have ≥ N fragments (0 = reprocess all).

    ``allow_inline``: when Redis is unavailable, fall back to running
    reprocess_paper() synchronously inside the ASGI handler.  Default ``false``
    — when Redis is unavailable and allow_inline is not set, the endpoint
    returns HTTP 503 so the caller is not silently blocked.  Only set
    ``allow_inline=true`` for tiny sets (≤ 5 papers); the endpoint enforces
    this cap automatically.

    Each enqueued job runs reprocess_paper() which atomically swaps old
    fragments with new chunked-extraction results.  Old data is preserved if
    extraction fails.

    Note: running inline (allow_inline=True) blocks the ASGI worker for the
    duration of all reprocess calls.  Use only for very small sets where Redis
    is genuinely not available.
    """
    _require_admin(user)

    data_dir = os.environ.get("KLEMMA_DATA_DIR", str(Path.home() / ".klemma"))
    data_path = Path(data_dir)
    library_db = data_path / "library.db"

    from klemma.stores.paper_store import LocalPaperStore
    from klemma.stores.user_library import LocalUserLibrary

    paper_store = LocalPaperStore(library_db)
    user_library = LocalUserLibrary(library_db)

    # Collect distinct paper_ids for this user's completed sources
    all_sources = user_library.get_all_sources(user_id=body.user_id)
    completed_paper_ids = list({
        s.paper_id for s in all_sources if s.status == "completed"
    })

    # Apply min_fragments filter
    candidate_ids: list[str] = []
    for pid in completed_paper_ids:
        if body.min_fragments > 0:
            frags = paper_store.get_fragments(pid)
            if len(frags) >= body.min_fragments:
                continue
        candidate_ids.append(pid)

    # Compute estimated chunks from actual raw_text length where available.
    # Each chunk is ~25K chars; fall back to 1 chunk per paper when raw_text is not cached.
    import math
    estimated_chunks = 0
    for pid in candidate_ids:
        raw = paper_store.get_raw_text(pid)
        chars = len(raw) if raw else 25_000  # default 1 chunk if no raw_text cached
        estimated_chunks += math.ceil(chars / 25_000)

    if body.dry_run:
        return ReprocessAllResponse(
            papers=len(candidate_ids),
            enqueued=0,
            dry_run=True,
            estimated_chunks=estimated_chunks,
        )

    # Enqueue via rq when available, otherwise return 503 unless allow_inline is set.
    from ..tasks import reprocess_paper

    enqueued = 0
    try:
        from redis import Redis
        from rq import Queue as RQueue

        redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379")
        q = RQueue(connection=Redis.from_url(redis_url), default_timeout=1800)
        for pid in candidate_ids:
            q.enqueue(reprocess_paper, pid, data_dir)
            enqueued += 1
    except Exception:
        # Redis unavailable — refuse unless allow_inline=True (blocking, small sets only)
        if not body.allow_inline:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=(
                    "Redis is unavailable and allow_inline is false. "
                    "Start Redis and retry, or set allow_inline=true (cap: 5 papers)."
                ),
            )
        inline_ids = candidate_ids[:5]
        logger.warning(
            "Redis unavailable; running reprocess_paper inline for %d paper(s) (allow_inline=True, cap=5)",
            len(inline_ids),
        )
        for pid in inline_ids:
            reprocess_paper(pid, data_dir)
            enqueued += 1

    return ReprocessAllResponse(
        papers=len(candidate_ids),
        enqueued=enqueued,
        dry_run=False,
        estimated_chunks=estimated_chunks,
    )
