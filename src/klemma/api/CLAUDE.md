# API Package

FastAPI application for the Klemma SaaS backend (ADR-009).

Install extra: `pip install "klemma[api]"` (adds `fastapi` + `uvicorn`).

Entry point: `uvicorn klemma.api.app:create_app --factory`

## Modules

### app.py (50 lines)
Application factory — creates and configures the FastAPI app.
- `create_app() -> FastAPI` — mounts all routers, sets title/version/lifespan
- `lifespan(app)` — async context manager: startup (DB init, config checks) + shutdown (close connections)

### deps.py (~65 lines)
Shared FastAPI dependencies for data store access.
- `set/get_paper_store()` — `PaperStore` singleton
- `set/get_user_library()` — `UserLibrary` singleton
- `set/get_project_store()` — `ProjectStore` singleton
- `set/get_file_store()` — `FileStore` singleton
- All set in `app.py` lifespan, used by route handlers via `Depends()`

### tasks.py (~790 lines)
Async task definitions for rq worker. Tasks receive primitive args (worker runs in separate process).
- `_create_ai_provider()` — AI provider from env vars (ANTHROPIC_API_KEY, OPENAI_API_KEY, KLEMMA_AI_MODEL)
- `_create_embeddings_provider()` — embedding provider from env vars (KLEMMA_EMBEDDINGS_BACKEND/MODEL/BASE_URL); returns None when disabled
- `process_source(paper_id, citekey, data_dir)` — full pipeline: PDF extract → AI fragments → auto-embed → section assign → citation links → auto-suggest
- `generate_outline_saas(project_id, context_text, ...)` — outline generation from plan-prospekt
- `generate_research(section, project_id, ...)` — research briefing via researcher.py in headless mode
- `generate_draft(section, data_dir, ...)` — section draft via drafter.py in headless mode

### worker.py (~25 lines)
RQ worker entry point: `python -m klemma.api.worker`

### routes/
Route modules. See [routes/CLAUDE.md](routes/CLAUDE.md).

## Adding a new feature

1. Create `routes/<domain>.py` with the FastAPI router.
2. Mount it in `app.py` under the commented-out future routers block.
3. Document it in `routes/CLAUDE.md`.

## Deployment

Docker Compose stack in `saas/deploy/`:
- `Dockerfile` — Python 3.12, installs `[api,recommended]`, uvicorn 2 workers
- `docker-compose.yml` — 6 services: api, worker, redis, ollama, caddy, (certbot removed)
- `Caddyfile` — reverse proxy with auto-TLS
- `.env.example` — required secrets (JWT secret) + optional embeddings config

```bash
cp saas/deploy/.env.example saas/deploy/.env  # edit with real secret
cd saas/deploy && docker compose up -d
```

API binds to Docker network only (not host) — nginx is the sole public entry point.

## Maintaining this file
Update when adding new modules to `src/klemma/api/` or changing the entry point / install extra.

See: [Routes](routes/CLAUDE.md) | [Core infrastructure](../CLAUDE.md) | [ADR-009](../../../docs/adr/ADR-009-saas-architecture.md)
