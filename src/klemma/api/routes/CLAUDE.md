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

### process.py (~120 lines)
Process endpoints — mounted with `prefix="/process"`. All require Bearer auth.
- `POST /process/sources/{citekey}` → `JobSubmitResponse` (202) — enqueue async extraction job
- `GET /process/jobs/{job_id}` → `JobStatusResponse` — poll job status (queued/started/finished/failed)
- Requires Redis + rq; returns 503 if unavailable
- Worker entry point: `python -m klemma.api.worker`

### analyze.py (~90 lines)
Analyze endpoints — mounted with `prefix="/analyze"`. All require Bearer auth.
- `GET /analyze/status` → `StatusResponse` — source counts (total/completed/pending/failed), coverage by section, total fragment count. SaaS equivalent of `klemma status`.

### write.py (~110 lines)
Write endpoints — mounted with `prefix="/write"`. All require Bearer auth.
- `POST /write/research` → `WriteJobResponse` (202) — enqueue research briefing job
- `POST /write/draft` → `WriteJobResponse` (202) — enqueue section draft job
- Jobs polled via shared `GET /process/jobs/{job_id}`
- Task stubs in `api/tasks.py` (AI pipeline not yet wired for headless SaaS)

## Adding a new router

1. Create `<domain>.py` with `router = APIRouter(tags=["<domain>"])` and route handlers.
2. Mount it in `../app.py`: `app.include_router(<domain>.router, prefix="/<domain>")`.
3. Add an entry to this file under **Modules**.

## Maintaining this file
Update when adding, removing, or renaming route modules in this directory.

See: [API package](../CLAUDE.md) | [Core infrastructure](../../CLAUDE.md)
