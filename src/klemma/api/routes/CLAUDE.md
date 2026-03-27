# API Routes

FastAPI route modules for the Klemma SaaS backend (ADR-009).

## Modules

### health.py (20 lines)
System health check endpoint — no auth required.
- `GET /health` → `{"status": "ok", "version": "<semver>", "service": "klemma-api"}`
- Router mounted with `prefix="/health"` in `app.py`; route decorator is `@router.get("")`

### auth.py
Auth endpoints — mounted with `prefix="/auth"`. `TokenResponse` includes `user_id` in all endpoints.
- `POST /auth/register` → `TokenResponse` (201)
- `POST /auth/login` → `TokenResponse` (200)
- `POST /auth/refresh` → `TokenResponse` (200) — rotates refresh token
- `GET /auth/me` → `UserResponse` (requires Bearer token)

### Admin: create users via CLI
Registration disabled on frontend. Use `scripts/create_user.sh`:
```bash
./scripts/create_user.sh <email> <password> [name] [token_amount]
# Set Klemma_ADMIN_EMAIL + Klemma_ADMIN_PASSWORD to auto-grant tokens
```

### library.py (~220 lines)
Library CRUD endpoints — mounted with `prefix="/library"`. All require Bearer auth.
- `GET /library/sources` → `SourceListResponse` — list all user sources with paper metadata
- `GET /library/sources/{citekey}` → `SourceDetailResponse` — source details + fragments
- `POST /library/sources` → `SourceResponse` (201) — add source by metadata (DOI dedup); uses caller-supplied `citekey` as primary key (no UUID generated)
- `DELETE /library/sources/{citekey}` → 204 — remove from user library (keeps global corpus)
- `POST /library/upload` → `UploadResponse` (201) — upload PDF with content-addressable dedup (`pdf_hash`); citekey derived from filename; **if same user re-uploads the same PDF, returns existing citekey unchanged** (citekey stability guarantee — issue #268)
- Schemas: `SourceResponse`, `SourceListResponse`, `SourceCreateRequest`, `FragmentResponse`, `SourceDetailResponse`, `UploadResponse`

**Citekey stability guarantee** (issue #268): citekeys must remain stable across push/pull round-trips because `draft/*.md` files embed `[@citekey]` references and section assignments are keyed by citekey.
- `POST /library/sources` — uses caller-provided `citekey` verbatim. ✅
- `POST /library/upload` — derives citekey from filename on first upload; subsequent uploads of the same PDF by the same user return the **original citekey** (not a new one). ✅
- `GET /sync/pull/library` — returns the exact citekey stored on push. ✅

### projects.py (~175 lines)
Project CRUD + coverage + section assignment endpoints — mounted with `prefix="/projects"`. All require Bearer auth.
- `GET /projects` → `ProjectListResponse` — list user's projects
- `POST /projects` → `ProjectResponse` (201) — create project
- `PATCH /projects/{project_id}` → `ProjectResponse` — rename project
- `DELETE /projects/{project_id}` → 204 — delete project + draft files + sync repo
- `PATCH /projects/{project_id}/outline` → `ProjectResponse` — update section outline
- `POST /projects/{project_id}/outline/generate` → `{job_id, status}` — enqueue AI outline generation (requires Redis/rq)
- `GET /projects/coverage` → `CoverageStatsResponse` — total sources, per-section/chapter counts
- `GET /projects/sections/{section}/sources` → `SectionSourcesResponse` — citekeys assigned to a section
- `POST /projects/sections/assign` → assign source to sections (validates source exists in library)
- `GET /projects/sources/{citekey}/sections` → sections assigned to a source
- `GET /projects/{project_id}/research` → list research reports for a project
- `GET /projects/{project_id}/research/{section:path}` → get research report for a section
- Schemas: `ProjectResponse`, `ProjectListResponse`, `ProjectCreateRequest`, `ProjectRenameRequest`, `OutlineSection`, `OutlineUpdateRequest`, `OutlineGenerateRequest`, `CoverageStatsResponse`, `SectionSourcesResponse`, `AssignSectionRequest`
- Block draft endpoints were in `blocks.py` (removed in #260 item 2 — BlockView migrated to `drafts.py`)

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

### sync.py (~340 lines)
API-only sync endpoints — mounted with `prefix="/sync"`. All require Bearer auth.
Server-side for `klemma-cli` sync client. No server-side git — all file sync via `/projects/{id}/drafts`.

**Library bulk sync:**
- `POST /sync/push/library` — batch upsert sources + fragments (JSON)
- `POST /sync/push/embeddings` — batch upsert vectors (base64-encoded float32)
- `POST /sync/push/decisions` — batch upsert decisions (stub — acknowledged, storage deferred)
- `GET /sync/pull/library?since=` — incremental: sources + fragments
- `GET /sync/pull/decisions?since=` — decisions (stub)

**Status:**
- `GET /sync/status/{project_id}` → `SyncStatusResponse` — library counts (`source_count`, `fragment_count`)

## Adding a new router

1. Create `<domain>.py` with `router = APIRouter(tags=["<domain>"])` and route handlers.
2. Mount it in `../app.py`: `app.include_router(<domain>.router, prefix="/<domain>")`.
3. Add an entry to this file under **Modules**.

## Maintaining this file
Update when adding, removing, or renaming route modules in this directory.

See: [API package](../CLAUDE.md) | [Core infrastructure](../../CLAUDE.md)
