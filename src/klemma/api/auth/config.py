"""Auth configuration — loaded from environment variables."""

from __future__ import annotations

import os
import secrets


class AuthConfig:
    """Auth settings from environment.

    In production (KLEMMA_ENV=production), KLEMMA_JWT_SECRET must be set
    explicitly — the app will refuse to start otherwise.

    In development/test, an ephemeral random secret is generated per process
    so tokens never leak a predictable value.
    """

    def __init__(self) -> None:
        raw_secret = os.environ.get("KLEMMA_JWT_SECRET", "")
        if not raw_secret:
            env = os.environ.get("KLEMMA_ENV", "development")
            if env == "production":
                raise ValueError(
                    "KLEMMA_JWT_SECRET must be set in production. "
                    "Generate one with: openssl rand -hex 32"
                )
            # Development/test: ephemeral secret — unique per process, never leaked.
            raw_secret = secrets.token_hex(32)
        self.secret_key: str = raw_secret
        self.algorithm: str = "HS256"
        self.access_token_expire_minutes: int = int(
            os.environ.get("KLEMMA_ACCESS_TOKEN_EXPIRE_MINUTES", "15")
        )
        self.refresh_token_expire_days: int = int(
            os.environ.get("KLEMMA_REFRESH_TOKEN_EXPIRE_DAYS", "30")
        )


auth_config = AuthConfig()
