# API Routes

FastAPI route modules for the Klemma SaaS backend (ADR-009).

## Modules

### health.py (20 lines)
System health check endpoint — no auth required.
- `GET /health` → `{"status": "ok", "version": "<semver>", "service": "klemma-api"}`
- Router mounted with `prefix="/health"` in `app.py`; route decorator is `@router.get("")`

### auth.py
Auth endpoints — mounted with `prefix="/auth"`. `TokenResponse` includes `user_id` in all endpoints.
- `POST /auth/register` → `TokenResponse` (201). Auto-grants initial token balance via `LocalUserStore.grant_tokens()` — amount controlled by `KLEMMA_INITIAL_TOKEN_GRANT` env var (default `1_000_000`; set to `0` to disable). Grant failures log + don't break registration.
- `POST /auth/login` → `TokenResponse` (200)
- `POST /auth/refresh` → `TokenResponse` (200) — rotates refresh token
- `GET /auth/me` → `UserResponse` (requires Bearer token)

### Admin: create users via CLI
Registration disabled on frontend. Use `scripts/create_user.sh`:
```bash
./scripts/create_user.sh <email> <password> [name] [token_amount]
# Set Klemma_ADMIN_EMAIL + Klemma_ADMIN_PASSWORD to auto-grant tokens
```

### library.py (~280 lines)
Library CRUD endpoints — mounted with `prefix="/library"`. All require Bearer auth.
- `GET /library/sources` → `SourceListResponse` — list all user sources with paper metadata; accepts `?q=` for full-text filter on title, authors, citekey
- `GET /library/sources/{citekey}` → `SourceDetailResponse` — source details + fragments
- `GET /library/fragments/search?q={text}&limit=N` → `FragmentSearchResponse` — text search over fragment_text for user's library; requires `q` ≥ 2 chars; returns up to `limit` (default 10, max 50) results ordered by length; uses `LocalPaperStore.search_fragments_for_user()` (JOIN fragments + papers + user_sources in library.db)
- `POST /library/sources` → `SourceResponse` (201) — add source by metadata (DOI dedup); uses caller-supplied `citekey` as primary key (no UUID generated)
- `DELETE /library/sources/{citekey}` → 204 — remove from user library (keeps global corpus)
- `POST /library/upload` → `UploadResponse` (201) — upload PDF with content-addressable dedup (`pdf_hash`); citekey derived from filename; **if same user re-uploads the same PDF, returns existing citekey unchanged** (citekey stability guarantee — issue #268). CrossRef is NOT called during upload — metadata enrichment is a separate user-triggered action.
- `GET /library/sources/{citekey}/metadata-preview` → `MetadataPreviewResponse` — returns current metadata fields + DOI extracted from PDF text via regex (`suggested_doi: str | null`). Used to pre-fill the MetadataEnrichDialog. Requires source ownership.
- `POST /library/sources/{citekey}/enrich-metadata` → `EnrichResponse` — call CrossRef by DOI (exact) or title (fuzzy, 5s timeout), update paper metadata, enqueue re-embed job. Rate-limited 10 req/min per user. Accepts `abstract_override` for scan PDFs. Returns `{matched, source: "doi"|"title"|"timeout"|"none", fields, embedding_status: "pending"|"skipped"}`.
- Schemas: `SourceResponse`, `SourceListResponse`, `SourceCreateRequest`, `FragmentResponse`, `SourceDetailResponse`, `UploadResponse`, `FragmentSearchResult`, `FragmentSearchResponse`, `MetadataCurrentFields`, `MetadataPreviewResponse`, `EnrichRequest`, `EnrichResponse`

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
- `DELETE /projects/sections/{section}/sources/{citekey}` → 204 — remove a specific section assignment; returns 404 if assignment not found; scoped by user_id
- `GET /projects/sources/{citekey}/sections` → sections assigned to a source
- `GET /projects/{project_id}/research` → list research reports for a project
- `GET /projects/{project_id}/research/{section:path}` → get research report for a section
- Schemas: `ProjectResponse`, `ProjectListResponse`, `ProjectCreateRequest`, `ProjectRenameRequest`, `OutlineSection`, `OutlineUpdateRequest`, `OutlineGenerateRequest`, `CoverageStatsResponse`, `SectionSourcesResponse`, `AssignSectionRequest`
- Block draft endpoints were in `blocks.py` (removed in #260 item 2 — BlockView migrated to `drafts.py`)

### drafts.py (~280 lines)
Draft file management — mounted under `prefix="/projects"`. All require Bearer auth.
Files stored at `KLEMMA_DATA_DIR/drafts/{project_id}/draft/` (ADR-016).
- `GET /projects/{id}/drafts` → `FileListResponse` — list `.md` files with parsed headings + word count
- `GET /projects/{id}/drafts/{filename}` → `FileContentResponse` — full content + headings
- `PUT /projects/{id}/drafts/{filename}` → `FileContentResponse` — save full file (git commit)
- `POST /projects/{id}/drafts/init` → `FileContentResponse` (201) — create file from project outline; idempotent
- `POST /projects/{id}/drafts/scaffold` → `ScaffoldResponse` (201) — create ADR-016 multi-file structure from outline; dissertation/thesis: intro.md + chapter_N.md + conclusion.md; paper: single paper.md; idempotent (existing files not overwritten); returns 422 if no outline
- `DELETE /projects/{id}/drafts/{filename}` → 204 — git rm + commit
- `PUT /projects/{id}/drafts/{filename}/sections/{section_id}` → `SectionUpsertResponse` — upsert one section body; used by klemma-cli push
- `POST /projects/{id}/drafts/migrate` → `MigrateResponse` — split monolithic `dissertation.md` into ADR-016 chapter files (`intro.md`, `chapter_N.md`, `conclusion.md`); idempotent (skips existing files); deletes source when ≥1 chapter written; accepts optional `?source_filename=` query param
- Schemas: `FileInfo`, `FileListResponse`, `FileContentResponse`, `FileSaveRequest`, `InitDraftRequest`, `ScaffoldResponse`, `SectionUpsertRequest`, `SectionUpsertResponse`, `MigrateChapterResult`, `MigrateResponse`

### process.py (~130 lines)
Process endpoints — mounted with `prefix="/process"`. All require Bearer auth.
- `POST /process/sources/{citekey}` → `JobSubmitResponse` (202) — enqueue async extraction job; validates `project_id` ownership before enqueueing (project_id is a write path for auto-suggestion)
- `GET /process/jobs/{job_id}` → `JobStatusResponse` — poll job status (queued/started/finished/failed)
- Requires Redis + rq; returns 503 if unavailable
- Worker entry point: `python -m klemma.api.worker`

### analyze.py (~190 lines)
Analyze endpoints — mounted with `prefix="/analyze"`. All require Bearer auth.
- `GET /analyze/status` → `StatusResponse` — source counts (total/completed/pending/failed), coverage by section, total fragment count. SaaS equivalent of `klemma status`.
- `GET /analyze/briefing/{project_id}` → `BriefingResponse` — per-section readiness assessment + coach findings. Zero AI cost. Readiness counts accepted+suggested (excludes rejected). Coach findings from `klemma.skills.coach.analyze_section()`.
- Schemas: `SectionBriefing`, `CoachFindingResponse`, `BriefingResponse`

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

### curation.py (~300 lines)
Citation curation endpoints — mounted with `prefix="/projects"`. All require Bearer auth.
Library-first pivot: users accept/reject fragments, assign them to outline sections, and curate a bank of citations per chapter.
- `GET /projects/{id}/fragments/pending?citekey=X` → `PendingFragmentsResponse` — uncurated fragments for a source; excludes already-curated fragment IDs
- `POST /projects/{id}/fragments/curate` → `{curated, accepted, rejected}` — batch accept/reject with optional section assignment + note; auto-assigns section via `INTENT_TO_SECTION_TYPES` mapping if not provided
- `GET /projects/{id}/fragments/curated?verdict=&section=&citekey=` → `CuratedBankResponse` — curated fragments with full text, grouped stats by section
- `PATCH /projects/{id}/fragments/curate/{fragment_id}` → partial update (verdict, section, note)
- `GET /projects/{id}/fragments/suggest?section=X` → `SuggestFragmentsResponse` — smart suggestions: intent match + gap alerts for missing intents
- `POST /projects/{id}/fragments/auto-suggest` → `{suggested: N}` — backfill `verdict='suggested'` entries for all uncurated fragments in a project; idempotent; uses `auto_assign_section()` from `klemma.section_types`
- Uses `INTENT_TO_SECTION_TYPES` and `auto_assign_section()` from `klemma.section_types` for auto-assignment
- Depends on: `user_store`, `paper_store`, `user_library` from `deps.py`
- Schemas: `PendingFragmentsResponse`, `CurateRequest`, `CuratedBankResponse`, `CuratedFragmentResponse`, `SuggestFragmentsResponse`

## Adding a new router

1. Create `<domain>.py` with `router = APIRouter(tags=["<domain>"])` and route handlers.
2. Mount it in `../app.py`: `app.include_router(<domain>.router, prefix="/<domain>")`.
3. Add an entry to this file under **Modules**.

## Maintaining this file
Update when adding, removing, or renaming route modules in this directory.

See: [API package](../CLAUDE.md) | [Core infrastructure](../../CLAUDE.md)
