# API Package

FastAPI application for the Klemma SaaS backend (ADR-009).

Install extra: `pip install "klemma[api]"` (adds `fastapi` + `uvicorn`).

Entry point: `uvicorn klemma.api.app:create_app --factory`

## Modules

### app.py (50 lines)
Application factory — creates and configures the FastAPI app.
- `create_app() -> FastAPI` — mounts all routers, sets title/version/lifespan
- `lifespan(app)` — async context manager: startup (DB init, config checks) + shutdown (close connections)

### deps.py (38 lines)
Shared FastAPI dependencies for data store access.
- `set_paper_store(store)` / `get_paper_store()` — module-level `PaperStore` singleton
- `set_user_library(lib)` / `get_user_library()` — module-level `UserLibrary` singleton
- Both set in `app.py` lifespan, used by route handlers via `Depends()`

### routes/
Route modules. See [routes/CLAUDE.md](routes/CLAUDE.md).

## Adding a new feature

1. Create `routes/<domain>.py` with the FastAPI router.
2. Mount it in `app.py` under the commented-out future routers block.
3. Document it in `routes/CLAUDE.md`.

## Maintaining this file
Update when adding new modules to `src/klemma/api/` or changing the entry point / install extra.

See: [Routes](routes/CLAUDE.md) | [Core infrastructure](../CLAUDE.md) | [ADR-009](../../../docs/adr/ADR-009-saas-architecture.md)
