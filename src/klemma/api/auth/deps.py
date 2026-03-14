"""FastAPI dependencies for authentication."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .tokens import decode_token

if TYPE_CHECKING:
    from klemma.models import UserRecord
    from klemma.protocols import UserStore

_bearer_scheme = HTTPBearer()

# Module-level store reference, set during app startup via set_user_store().
_user_store: UserStore | None = None


def set_user_store(store: UserStore) -> None:
    """Set the UserStore instance used by auth dependencies."""
    global _user_store  # noqa: PLW0603
    _user_store = store


def get_user_store() -> UserStore:
    """Return the configured UserStore, or raise if not set."""
    if _user_store is None:
        raise RuntimeError("UserStore not configured — call set_user_store() at startup")
    return _user_store


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
) -> UserRecord:
    """Extract and validate the current user from the Authorization header."""
    payload = decode_token(credentials.credentials)
    if payload is None or payload.get("type") != "access":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired access token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user_id: str | None = payload.get("sub")
    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token payload"
        )

    store = get_user_store()
    user = store.get_user_by_id(user_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found"
        )

    return user
