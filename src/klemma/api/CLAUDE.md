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
- `_validate_embeddings_config()` — fail-fast guard: SaaS requires `KLEMMA_EMBEDDINGS_BACKEND=litellm` + `MODEL` starting with `ollama/` + non-empty `BASE_URL`. Called at FastAPI startup and as backstop in `_create_embeddings_provider()`. Bypass with `KLEMMA_EMBEDDINGS_ALLOW_REMOTE=1` (CI/test only — **never in prod**).
- `_create_ai_provider()` — AI provider from env vars (ANTHROPIC_API_KEY, OPENAI_API_KEY, KLEMMA_AI_MODEL)
- `_create_embeddings_provider()` — embedding provider from env vars (KLEMMA_EMBEDDINGS_BACKEND/MODEL/BASE_URL); calls `_validate_embeddings_config()` as backstop
- `process_source(paper_id, citekey, data_dir)` — full pipeline: PDF extract → abstract extraction from text → AI fragments → verbatim validation (full text for <100K PDFs, 150K cap for large) → auto-embed → section assign → citation links → async auto-suggest post-hook
- `_run_auto_suggest(...)` — writes curation suggestions for all fragments; idempotent (INSERT OR REPLACE); runs as async rq job, errors are logged but never re-raised
- `_enqueue_auto_suggest(...)` — enqueues `_run_auto_suggest`; falls back to synchronous execution if Redis unavailable
- `re_embed_source_task(paper_id, citekey, data_dir)` — re-computes source embedding after metadata enrichment; called by `enrich-metadata` route
- `generate_outline_saas(project_id, context_text, ...)` — outline generation from plan-prospekt
- `generate_research(section, project_id, ...)` — research briefing via researcher.py in headless mode
- `generate_draft(section, data_dir, ...)` — section draft via drafter.py in headless mode

### constants.py
Shared numeric constants for the API layer.
- `VERBATIM_VALIDATION_CAP_SMALL = 100_000` — PDFs below this threshold → validate against full text
- `VERBATIM_VALIDATION_CAP_LARGE = 150_000` — cap applied for large PDFs (≥ SMALL threshold)
- `EMBEDDINGS_REQUIRED_BACKEND = "litellm"`, `EMBEDDINGS_REQUIRED_MODEL_PREFIX = "ollama/"` — enforcement values for `_validate_embeddings_config()`

### worker.py (~25 lines)
RQ worker entry point: `python -m klemma.api.worker`

### scoring.py (~170 lines)
Pure gap scoring function — no DB/network dependencies.
- `score_gaps(raw_gaps, citing_paper_ids_by_gap, citing_embeddings, section_centroids, sections_by_citing_paper) -> list[dict]`
  Formula: `count × avg_quality × intent_weight × semantic_factor`
  `intent_weight` = AVG of per-intent weights (Teufel 2006 taxonomy): method=3.0, extends=2.5, result_comparison=2.0, contrasts=2.0, uses_data=1.5, background/None=1.0
  `semantic_factor` = 0.5 + 0.5 × max_section_cosine — range [0.5, 1.0]. **Noise penalty, not boost**: cosine=0 → 0.5 (halves score), cosine=1 → 1.0 (neutral). <2 citing papers with embeddings → 1.0.
- `_compute_semantic_factor(citing_paper_ids, paper_embeddings, section_centroids)` — cosine similarity of citing embeddings vs section centroids; neutral if insufficient data
- `_parse_intents(intents_str)` — parse GROUP_CONCAT string, validate whitelist
- `_compute_intent_weight(intents)` — AVG weights, empty → 1.0
- `_compute_top_intent(intents)` — most frequent, weight-tiebreaker

### routes/
Route modules. See [routes/CLAUDE.md](routes/CLAUDE.md).

## Backfill operations

### backfill_citation_intents (tasks.py)
Cursor-based task to retroactively extract `citation_intent` for papers processed before intent detection was added.

```python
backfill_citation_intents(
    user_id: str,
    data_dir: str,
    batch_size: int = 20,
    cursor: str | None = None,
) -> dict  # {processed, skipped_no_raw_text, failed, next_cursor, remaining}
```

- Uses full `raw_text` (50K truncated) — NOT bibliography text (hallucination risk)
- Only updates citation_graph entries where intent IS NULL or 'background' (legacy default)
- Papers without raw_text: counted in `skipped_no_raw_text` (not an error)
- Cursor: `paper_id` ASC ordering — pass `next_cursor` to resume; repeat until `remaining == 0`

### Admin script

`scripts/backfill_gap_intents.sh <user_id>` — cursor-loop around the admin endpoint.
- `--dry-run` flag: first batch without UPDATE (report only)
- Logs progress and estimated token usage before starting
- Requires `ADMIN_TOKEN` env var

### Admin endpoint

`POST /admin/backfill/citation-intents` — see [routes/CLAUDE.md](routes/CLAUDE.md).

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
