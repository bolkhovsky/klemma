"""JWT token creation and verification (ADR-009).

Access tokens (short-lived, 15 min) and refresh tokens (long-lived, 30 days).
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timedelta, timezone

import jwt
from jwt.exceptions import PyJWTError

from .config import auth_config


def create_access_token(user_id: str, email: str) -> str:
    """Create a short-lived access token."""
    expire = datetime.now(timezone.utc) + timedelta(
        minutes=auth_config.access_token_expire_minutes
    )
    payload = {
        "sub": user_id,
        "email": email,
        "exp": expire,
        "type": "access",
    }
    return jwt.encode(payload, auth_config.secret_key, algorithm=auth_config.algorithm)


def create_refresh_token(user_id: str) -> str:
    """Create a long-lived refresh token."""
    expire = datetime.now(timezone.utc) + timedelta(
        days=auth_config.refresh_token_expire_days
    )
    payload = {
        "sub": user_id,
        "exp": expire,
        "type": "refresh",
        "jti": uuid.uuid4().hex,
    }
    return jwt.encode(payload, auth_config.secret_key, algorithm=auth_config.algorithm)


def decode_token(token: str) -> dict | None:
    """Decode and validate a JWT token. Returns payload or None if invalid."""
    try:
        payload = jwt.decode(
            token, auth_config.secret_key, algorithms=[auth_config.algorithm]
        )
        return payload
    except PyJWTError:
        return None


def hash_token(token: str) -> str:
    """Hash a token for storage (refresh tokens are stored hashed)."""
    return hashlib.sha256(token.encode()).hexdigest()


def refresh_token_expires_at() -> str:
    """ISO 8601 expiry timestamp for a new refresh token."""
    expire = datetime.now(timezone.utc) + timedelta(
        days=auth_config.refresh_token_expire_days
    )
    return expire.isoformat()
