"""Auth configuration — loaded from environment variables."""

from __future__ import annotations

import os


class AuthConfig:
    """Auth settings from environment. Defaults are dev-friendly."""

    def __init__(self) -> None:
        self.secret_key: str = os.environ.get(
            "KLEMMA_JWT_SECRET", "dev-secret-change-in-production"
        )
        self.algorithm: str = "HS256"
        self.access_token_expire_minutes: int = int(
            os.environ.get("KLEMMA_ACCESS_TOKEN_EXPIRE_MINUTES", "15")
        )
        self.refresh_token_expire_days: int = int(
            os.environ.get("KLEMMA_REFRESH_TOKEN_EXPIRE_DAYS", "30")
        )


auth_config = AuthConfig()
