"""FastAPI application factory (ADR-009).

Creates the Klemma API app with all routers mounted.
Entry point: `uvicorn klemma.api.app:create_app --factory`
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncGenerator

from fastapi import FastAPI

from klemma import __version__
from klemma.stores.user_store import LocalUserStore

from .auth.deps import set_user_store
from .routes import auth, health


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan: startup and shutdown hooks."""
    # Startup: initialize UserStore
    data_dir = Path(os.environ.get("KLEMMA_DATA_DIR", str(Path.home() / ".klemma")))
    user_store = LocalUserStore(data_dir / "users.db")
    set_user_store(user_store)
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
    app.include_router(auth.router, prefix="/auth", tags=["auth"])

    # Future routers (Phase 1 tasks):
    # app.include_router(library.router, prefix="/library", tags=["library"])
    # app.include_router(projects.router, prefix="/projects", tags=["projects"])
    # app.include_router(process.router, prefix="/process", tags=["process"])
    # app.include_router(analyze.router, prefix="/analyze", tags=["analyze"])
    # app.include_router(write.router, prefix="/write", tags=["write"])

    return app
