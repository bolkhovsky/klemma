# klemma-cli

Lightweight API-native sync client for Klemma SaaS. Separate package (`pip install klemma-cli`).

## Architecture

- All sync via **REST API only** (no server-side git)
- Local git stays intact — power users use `git` directly; `k status` shows local uncommitted changes
- No AI, no heavy deps — just click, requests, pydantic, pyyaml + git subprocess (local only)

## Modules

### main.py (~260 lines)
Click CLI entry point. 5 commands: `link`, `push`, `pull`, `status`, `login`.

### auth.py (~80 lines)
Login to Klemma API, token storage at `~/.klemma-cli/auth.json`.
- `login(api_url, email, password)` — POST /auth/login → save tokens
- `refresh_access_token()` — rotate tokens on 401

### client.py (~60 lines)
`KlemmaClient` — HTTP client with auto-refresh on 401.
- `get(path)`, `post(path, json)` — wrappers with auth headers

### gitops.py (~110 lines)
Local git subprocess wrappers (no server transport functions).
- `is_git_repo`, `init`, `add_files`, `has_changes`, `commit`, `log`
- `status` — filtered to klemma-synced paths (`KLEMMA.md`, `draft/`, `notes/research/`, `.gitignore`)
- `get_head_hash`, `write_gitignore`

### sync.py (~265 lines)
Library and draft sync — read local SQLite DB and push/pull via API.
- `read_local_sources(project_root)` — read from library.db user_sources + papers
- `read_local_fragments(project_root)` — read from library.db fragments
- `read_local_embeddings(project_root)` — base64-encode vectors from library.db
- `push_library(client, project_root)` — push sources + fragments + embeddings
- `pull_library(client, project_root)` — pull and write to local library.db
- `push_drafts(client, project_root, dashboard_project_id)` — PUT each `draft/*.md` to server
- `pull_drafts(client, project_root, dashboard_project_id)` — GET draft file list + contents, write only if changed; `_SAFE_DRAFT_FILENAME` regex guards against path traversal

### project.py (~50 lines)
Project discovery — find `.klemma/` directory, parse KLEMMA.md.

### models.py (~55 lines)
Pydantic schemas: `SourcePayload`, `FragmentPayload`, `EmbeddingPayload`, `DecisionPayload`, `SyncConfig`.
`SyncConfig` fields: `api_url`, `dashboard_project_id`, `last_push`, `last_pull` (no `git_url`, no `access_token`).

### state.py (~30 lines)
Sync state persistence — `.klemma/sync_config.json` CRUD.

## Data flow: push

```
klemma-cli push
  1. Read local library.db → POST /sync/push/library (sources + fragments)
  2. Read local embeddings → POST /sync/push/embeddings (base64 chunks)
  3. PUT draft/*.md → POST /projects/{id}/drafts/{name} (each file)
  4. Update .klemma/sync_config.json last_push timestamp
```

## Data flow: pull

```
klemma-cli pull
  1. GET /sync/pull/library → write sources/fragments to local library.db
  2. GET /projects/{id}/drafts → list; GET /projects/{id}/drafts/{name} per file
     → write to draft/{name} only if content differs (ADR-016)
  3. Update .klemma/sync_config.json last_pull timestamp
```

## Data flow: link

```
klemma-cli link
  1. Login → GET /projects (find by name) or POST /projects (create)
  2. write_gitignore(project_root)
  3. Save SyncConfig {api_url, dashboard_project_id} to .klemma/sync_config.json
```

## Local git story

- `k status` shows local uncommitted changes via `git status --short` (filtered to synced paths)
- Power users: use `git add / commit / log` directly in the dissertation directory
- `k link` writes `.gitignore` with klemma-specific exclusions
- Server has no bare repos — all file sync via `/projects/{id}/drafts` REST API

## Tests

- `cli/tests/test_gitops.py` — git subprocess wrapper tests (local ops only)
- `cli/tests/test_project.py` — project discovery tests
- `cli/tests/test_sync.py` — library DB read tests + pull_drafts tests (5 cases)
- `tests/test_api_sync.py` — backend sync API endpoint tests (library sync only)

See: [API Routes](../src/klemma/api/routes/CLAUDE.md) | [Root](../CLAUDE.md)
