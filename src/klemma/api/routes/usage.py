"""Usage tracking endpoints: token balance and admin operations (#202)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from klemma.models import UserRecord

from ..auth.deps import get_current_user, get_user_store

router = APIRouter()


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class TokenBalanceResponse(BaseModel):
    """Token balance for the current user."""

    total_granted: int
    total_used: int
    remaining: int
    operations: list[dict] = []


class GrantTokensRequest(BaseModel):
    """Admin request to grant tokens to a user."""

    user_id: str
    amount: int


class GrantTokensResponse(BaseModel):
    """Response after granting tokens."""

    user_id: str
    total_granted: int
    total_used: int
    remaining: int


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/me", response_model=TokenBalanceResponse)
async def get_my_usage(
    user: UserRecord = Depends(get_current_user),
) -> TokenBalanceResponse:
    """Get token balance and usage summary for the current user."""
    store = get_user_store()
    summary = store.get_usage_summary(user.user_id)
    return TokenBalanceResponse(**summary)


@router.post("/grant", response_model=GrantTokensResponse)
async def grant_tokens(
    body: GrantTokensRequest,
    user: UserRecord = Depends(get_current_user),
) -> GrantTokensResponse:
    """Grant tokens to a user. Admin only (first registered user is admin)."""
    store = get_user_store()

    # Simple admin check: first user in the database is admin
    # TODO: proper role-based access when multi-user is implemented
    with store._conn() as conn:
        first = conn.execute(
            "SELECT user_id FROM users ORDER BY created_at ASC LIMIT 1"
        ).fetchone()
    if not first or first["user_id"] != user.user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only admin can grant tokens",
        )

    # Verify target user exists
    target = store.get_user_by_id(body.user_id)
    if not target:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User {body.user_id} not found",
        )

    balance = store.grant_tokens(body.user_id, body.amount)
    return GrantTokensResponse(user_id=body.user_id, **balance)
