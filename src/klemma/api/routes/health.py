"""Health check endpoint."""

from __future__ import annotations

from fastapi import APIRouter

from klemma import __version__

router = APIRouter(tags=["system"])


@router.get("")
async def health_check() -> dict:
    """Basic health check — returns version and status."""
    return {
        "status": "ok",
        "version": __version__,
        "service": "klemma-api",
    }
