"""Auth endpoints: register, login, refresh, me (ADR-009)."""

from __future__ import annotations

import logging
import os

from fastapi import APIRouter, Depends, HTTPException, status

from klemma.models import UserRecord

from ..auth.deps import get_current_user, get_user_store
from ..auth.password import hash_password, verify_password
from ..auth.schemas import (
    RefreshRequest,
    TokenResponse,
    UserCreate,
    UserLogin,
    UserResponse,
)
from ..auth.tokens import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_token,
    refresh_token_expires_at,
)
from ..rate_limit import rate_limit

router = APIRouter()
logger = logging.getLogger(__name__)

_DEFAULT_INITIAL_TOKEN_GRANT = 1_000_000


def _initial_token_grant() -> int:
    """Read the initial token allowance for new users from env, default 1M.

    Configurable via ``KLEMMA_INITIAL_TOKEN_GRANT``. Set to 0 to disable
    auto-grant (e.g. for paid-only deployments). Negative or invalid values
    fall back to the default with a warning.
    """
    raw = os.getenv("KLEMMA_INITIAL_TOKEN_GRANT")
    if raw is None:
        return _DEFAULT_INITIAL_TOKEN_GRANT
    try:
        amount = int(raw)
    except ValueError:
        logger.warning(
            "KLEMMA_INITIAL_TOKEN_GRANT=%r is not an integer; using default %d",
            raw, _DEFAULT_INITIAL_TOKEN_GRANT,
        )
        return _DEFAULT_INITIAL_TOKEN_GRANT
    if amount < 0:
        logger.warning(
            "KLEMMA_INITIAL_TOKEN_GRANT=%d is negative; using default %d",
            amount, _DEFAULT_INITIAL_TOKEN_GRANT,
        )
        return _DEFAULT_INITIAL_TOKEN_GRANT
    return amount


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def register(
    body: UserCreate,
    _rate=Depends(rate_limit(3, 60)),
) -> TokenResponse:
    """Create a new user account and return token pair."""
    store = get_user_store()
    pw_hash = hash_password(body.password)
    try:
        user = store.create_user(email=body.email, password_hash=pw_hash, name=body.name)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email already registered",
        )

    initial_grant = _initial_token_grant()
    if initial_grant > 0:
        try:
            store.grant_tokens(user.user_id, initial_grant)
        except Exception:
            logger.exception(
                "Failed to grant initial %d tokens to new user %s; account created without balance",
                initial_grant, user.user_id,
            )

    access = create_access_token(user.user_id, user.email)
    refresh = create_refresh_token(user.user_id)
    store.store_refresh_token(user.user_id, hash_token(refresh), refresh_token_expires_at())

    return TokenResponse(
        user_id=user.user_id, username=user.username,
        access_token=access, refresh_token=refresh,
    )


@router.post("/login", response_model=TokenResponse)
async def login(
    body: UserLogin,
    _rate=Depends(rate_limit(5, 60)),
) -> TokenResponse:
    """Authenticate with email/password and return token pair."""
    store = get_user_store()
    user = store.get_user_by_email(body.email)
    if user is None or not verify_password(body.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )

    access = create_access_token(user.user_id, user.email)
    refresh = create_refresh_token(user.user_id)
    store.store_refresh_token(user.user_id, hash_token(refresh), refresh_token_expires_at())

    return TokenResponse(
        user_id=user.user_id, username=user.username,
        access_token=access, refresh_token=refresh,
    )


@router.post("/refresh", response_model=TokenResponse)
async def refresh(
    body: RefreshRequest,
    _rate=Depends(rate_limit(10, 60)),
) -> TokenResponse:
    """Exchange a valid refresh token for a new token pair (rotation)."""
    payload = decode_token(body.refresh_token)
    if payload is None or payload.get("type") != "refresh":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired refresh token",
        )

    user_id: str = payload["sub"]
    store = get_user_store()
    old_hash = hash_token(body.refresh_token)

    if not store.verify_refresh_token(user_id, old_hash):
        # Possible token reuse — revoke all tokens for safety
        store.revoke_refresh_tokens(user_id)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token revoked or already used",
        )

    user = store.get_user_by_id(user_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found"
        )

    # Rotate: revoke old, issue new
    store.revoke_refresh_tokens(user_id)
    access = create_access_token(user.user_id, user.email)
    new_refresh = create_refresh_token(user.user_id)
    store.store_refresh_token(
        user.user_id, hash_token(new_refresh), refresh_token_expires_at()
    )

    return TokenResponse(
        user_id=user.user_id, username=user.username,
        access_token=access, refresh_token=new_refresh,
    )


@router.get("/me", response_model=UserResponse)
async def me(user: UserRecord = Depends(get_current_user)) -> UserResponse:
    """Return the current authenticated user's info."""
    return UserResponse(
        user_id=user.user_id,
        email=user.email,
        name=user.name,
        username=user.username,
        email_verified=user.email_verified,
    )
