# API Routes

FastAPI route modules for the Klemma SaaS backend (ADR-009).

## Modules

### health.py (20 lines)
System health check endpoint — no auth required.
- `GET /health` → `{"status": "ok", "version": "<semver>", "service": "klemma-api"}`

## Adding a new router

1. Create `<domain>.py` with `router = APIRouter(tags=["<domain>"])` and route handlers.
2. Mount it in `../app.py`: `app.include_router(<domain>.router, prefix="/<domain>")`.
3. Add an entry to this file under **Modules**.

## Maintaining this file
Update when adding, removing, or renaming route modules in this directory.

See: [API package](../CLAUDE.md) | [Core infrastructure](../../CLAUDE.md)
