"""FastAPI application factory (ADR-009).

Creates the Klemma API app with all routers mounted.
Entry point: `uvicorn klemma.api.app:create_app --factory`
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI

from klemma import __version__

from .routes import health


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan: startup and shutdown hooks."""
    # Startup: initialize DB connections, check config
    yield
    # Shutdown: close connections, flush caches


def create_app() -> FastAPI:
    """Application factory — creates and configures the FastAPI app."""
    app = FastAPI(
        title="Klemma API",
        description="AI-powered academic research assistant — SaaS backend",
        version=__version__,
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
    )

    # Mount routers
    app.include_router(health.router)

    # Future routers (Phase 1 tasks):
    # app.include_router(auth.router, prefix="/auth", tags=["auth"])
    # app.include_router(library.router, prefix="/library", tags=["library"])
    # app.include_router(projects.router, prefix="/projects", tags=["projects"])
    # app.include_router(process.router, prefix="/process", tags=["process"])
    # app.include_router(analyze.router, prefix="/analyze", tags=["analyze"])
    # app.include_router(write.router, prefix="/write", tags=["write"])

    return app
