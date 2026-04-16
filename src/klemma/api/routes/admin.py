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
