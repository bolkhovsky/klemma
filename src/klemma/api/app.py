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
from fastapi.middleware.cors import CORSMiddleware

from klemma import __version__
from klemma.stores.paper_store import LocalPaperStore
from klemma.stores.project_store import LocalProjectStore
from klemma.stores.user_library import LocalUserLibrary
from klemma.stores.user_store import LocalUserStore

from .auth.deps import set_user_store
from .deps import set_paper_store, set_project_store, set_user_library
from .routes import analyze, auth, health, library, process, projects


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan: startup and shutdown hooks."""
    # Startup: initialize stores
    # SaaS Phase 1: SQLite backends via Protocol interfaces.
    # Phase 2 swaps to PostgreSQL — same Protocols, different implementations.
    # project.db lives in KLEMMA_DATA_DIR (not per-directory) because the SaaS
    # API has no concept of filesystem project directories — one project per user.
    data_dir = Path(os.environ.get("KLEMMA_DATA_DIR", str(Path.home() / ".klemma")))
    user_store = LocalUserStore(data_dir / "users.db")
    set_user_store(user_store)
    library_db = data_dir / "library.db"
    set_paper_store(LocalPaperStore(library_db))
    set_user_library(LocalUserLibrary(library_db))
    set_project_store(LocalProjectStore(data_dir / "project.db"))
    yield
    # Shutdown: close connections, flush caches


def create_app() -> FastAPI:
    """Application factory — creates and configures the FastAPI app."""
    is_production = os.getenv("KLEMMA_ENV") == "production"
    app = FastAPI(
        title="Klemma API",
        description="AI-powered academic research assistant — SaaS backend",
        version=__version__,
        lifespan=lifespan,
        docs_url=None if is_production else "/docs",
        redoc_url=None if is_production else "/redoc",
        openapi_url=None if is_production else "/openapi.json",
    )

    # CORS — explicit origins from env, deny-all default in production
    cors_origins_raw = os.getenv("KLEMMA_CORS_ORIGINS", "")
    is_production = os.getenv("KLEMMA_ENV") == "production"
    if cors_origins_raw:
        allowed_origins = [o.strip() for o in cors_origins_raw.split(",") if o.strip()]
    elif is_production:
        allowed_origins = []
    else:
        allowed_origins = ["http://localhost:3000", "http://localhost:5173"]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Mount routers
    app.include_router(health.router, prefix="/health")
    app.include_router(auth.router, prefix="/auth", tags=["auth"])
    app.include_router(library.router, prefix="/library", tags=["library"])
    app.include_router(projects.router, prefix="/projects", tags=["projects"])
    app.include_router(analyze.router, prefix="/analyze", tags=["analyze"])
    app.include_router(process.router, prefix="/process", tags=["process"])
    # app.include_router(write.router, prefix="/write", tags=["write"])

    return app
