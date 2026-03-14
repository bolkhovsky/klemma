"""In-memory per-IP rate limiting for the Klemma API (#170).

Simple token-bucket rate limiter implemented as FastAPI dependencies.
Suitable for single-process deployment (FirstVDS VPS, ADR-009).
"""

from __future__ import annotations

import time
from collections import defaultdict
from typing import Callable

from fastapi import HTTPException, Request, status


class _RateLimiter:
    """Token-bucket rate limiter keyed by client IP."""

    def __init__(self) -> None:
        # {ip: [(timestamp, ...), ...]} — sliding window
        self._requests: dict[str, list[float]] = defaultdict(list)

    def check(self, ip: str, max_requests: int, window_seconds: int) -> None:
        """Raise 429 if the IP has exceeded max_requests in the last window_seconds."""
        now = time.monotonic()
        cutoff = now - window_seconds
        # Prune old entries
        self._requests[ip] = [t for t in self._requests[ip] if t > cutoff]
        if len(self._requests[ip]) >= max_requests:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many requests. Please try again later.",
                headers={"Retry-After": str(window_seconds)},
            )
        self._requests[ip].append(now)


_limiter = _RateLimiter()


def reset_rate_limiter() -> None:
    """Clear all rate limit state. Used in tests."""
    _limiter._requests.clear()


def _get_client_ip(request: Request) -> str:
    """Extract client IP from request (respects X-Forwarded-For behind proxy)."""
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def rate_limit(max_requests: int, window_seconds: int = 60) -> Callable:
    """Create a FastAPI dependency that enforces rate limiting.

    Usage::

        @router.post("/login")
        async def login(
            body: UserLogin,
            _rate=Depends(rate_limit(5, 60)),  # 5 req/min
        ):
    """

    async def dependency(request: Request) -> None:
        _limiter.check(_get_client_ip(request), max_requests, window_seconds)

    return dependency
