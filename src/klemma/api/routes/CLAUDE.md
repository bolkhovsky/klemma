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

### library.py (~210 lines)
Library CRUD endpoints — mounted with `prefix="/library"`. All require Bearer auth.
- `GET /library/sources` → `SourceListResponse` — list all user sources with paper metadata
- `GET /library/sources/{citekey}` → `SourceDetailResponse` — source details + fragments
- `POST /library/sources` → `SourceResponse` (201) — add source by metadata (DOI dedup)
- `DELETE /library/sources/{citekey}` → 204 — remove from user library (keeps global corpus)
- `POST /library/upload` → `UploadResponse` (201) — upload PDF file with content-addressable dedup (pdf_hash)
- Schemas: `SourceResponse`, `SourceListResponse`, `SourceCreateRequest`, `FragmentResponse`, `SourceDetailResponse`, `UploadResponse`

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

### blocks.py (~200 lines)
Block draft endpoints — mounted with `prefix="/projects"`. All require Bearer auth.
File layout: `KLEMMA_DATA_DIR/drafts/{project_id}/{section_id}/{block_id}.md`
Each project directory is a git repo; every save creates a commit with `"block {section}/{block}: {N}w"`.
- `GET /projects/{project_id}/blocks/{section_id}/{block_id}` → `BlockResponse` — read draft (empty text if not yet saved)
- `PUT /projects/{project_id}/blocks/{section_id}/{block_id}` → `BlockResponse` — write file + git commit; commit failure is non-fatal
- `GET /projects/{project_id}/blocks/status` → `BlockStatusResponse` — all saved blocks with `{section_id}/{block_id}` keys, word counts, `has_draft` flags

### write.py (~110 lines)
Write endpoints — mounted with `prefix="/write"`. All require Bearer auth.
- `POST /write/research` → `WriteJobResponse` (202) — enqueue research briefing job
- `POST /write/draft` → `WriteJobResponse` (202) — enqueue section draft job
- Jobs polled via shared `GET /process/jobs/{job_id}`
- Task stubs in `api/tasks.py` (AI pipeline not yet wired for headless SaaS)

### sync.py (~550 lines)
Git-native sync endpoints — mounted with `prefix="/sync"`. All require Bearer auth (except verify-git-token).
Server-side for `klemma-cli` sync client. Git repos are bare repos at `KLEMMA_DATA_DIR/repos/{project_id}/`.

**Git repo management:**
- `POST /sync/init-repo` → `InitRepoResponse` (201) — create bare repo + access token
- `GET /sync/file/{project_id}/{file_path}` → `FileContentResponse` — `git show HEAD:{path}`
- `GET /sync/history/{project_id}` → `[HistoryEntry]` — `git log`
- `POST /sync/commit/{project_id}` → `CommitResponse` — commit file from browser edit (bare repo plumbing)
- `POST /sync/rollback/{project_id}` → rollback N commits via `git update-ref`
- `GET /sync/status/{project_id}` → `SyncStatusResponse` — file hashes, library counts, last commit

**Library bulk sync:**
- `POST /sync/push/library` — batch upsert sources + fragments (JSON)
- `POST /sync/push/embeddings` — batch upsert vectors (base64-encoded float32)
- `POST /sync/push/decisions` — batch upsert decisions (stub — acknowledged, storage deferred)
- `GET /sync/pull/library?since=` — incremental: sources + fragments
- `GET /sync/pull/decisions?since=` — decisions (stub)

**Token verification:**
- `GET /sync/verify-git-token?token=&project_id=` — for reverse proxy auth (no Bearer required)

## Adding a new router

1. Create `<domain>.py` with `router = APIRouter(tags=["<domain>"])` and route handlers.
2. Mount it in `../app.py`: `app.include_router(<domain>.router, prefix="/<domain>")`.
3. Add an entry to this file under **Modules**.

## Maintaining this file
Update when adding, removing, or renaming route modules in this directory.

See: [API package](../CLAUDE.md) | [Core infrastructure](../../CLAUDE.md)
