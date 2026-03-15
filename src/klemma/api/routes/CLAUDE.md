# API Routes

FastAPI route modules for the Klemma SaaS backend (ADR-009).

## Modules

### health.py (20 lines)
System health check endpoint — no auth required.
- `GET /health` → `{"status": "ok", "version": "<semver>", "service": "klemma-api"}`
- Router mounted with `prefix="/health"` in `app.py`; route decorator is `@router.get("")`

### auth.py
Auth endpoints — mounted with `prefix="/auth"`.
- `POST /auth/register` → `TokenResponse` (201)
- `POST /auth/login` → `TokenResponse` (200)
- `POST /auth/refresh` → `TokenResponse` (200) — rotates refresh token
- `GET /auth/me` → `UserResponse` (requires Bearer token)

### library.py (~210 lines)
Library CRUD endpoints — mounted with `prefix="/library"`. All require Bearer auth.
- `GET /library/sources` → `SourceListResponse` — list all user sources with paper metadata
- `GET /library/sources/{citekey}` → `SourceDetailResponse` — source details + fragments
- `POST /library/sources` → `SourceResponse` (201) — add source by metadata (DOI dedup)
- `DELETE /library/sources/{citekey}` → 204 — remove from user library (keeps global corpus)
- Schemas: `SourceResponse`, `SourceListResponse`, `SourceCreateRequest`, `FragmentResponse`, `SourceDetailResponse`

### projects.py (~110 lines)
Project endpoints — mounted with `prefix="/projects"`. All require Bearer auth.
- `GET /projects/coverage` → `CoverageStatsResponse` — total sources, per-section/chapter counts
- `GET /projects/sections/{section}/sources` → `SectionSourcesResponse` — citekeys assigned to a section
- `POST /projects/sections/assign` → assign source to sections (validates source exists in library)
- `GET /projects/sources/{citekey}/sections` → sections assigned to a source

## Adding a new router

1. Create `<domain>.py` with `router = APIRouter(tags=["<domain>"])` and route handlers.
2. Mount it in `../app.py`: `app.include_router(<domain>.router, prefix="/<domain>")`.
3. Add an entry to this file under **Modules**.

## Maintaining this file
Update when adding, removing, or renaming route modules in this directory.

See: [API package](../CLAUDE.md) | [Core infrastructure](../../CLAUDE.md)
