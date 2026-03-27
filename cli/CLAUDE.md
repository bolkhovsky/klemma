# klemma-cli

Lightweight git-native sync client for Klemma SaaS. Separate package (`pip install klemma-cli`).

## Architecture

- Files sync via **git** (push/pull to bare repos on server)
- Library data syncs via **REST API** (batch JSON for sources/fragments/embeddings)
- No AI, no heavy deps — just click, requests, pydantic, pyyaml + git subprocess

## Modules

### main.py (~280 lines)
Click CLI entry point. 6 commands: `link`, `push`, `pull`, `status`, `rollback`, `login`.

### auth.py (~80 lines)
Login to Klemma API, token storage at `~/.klemma-cli/auth.json`.
- `login(api_url, email, password)` — POST /auth/login → save tokens
- `refresh_access_token()` — rotate tokens on 401

### client.py (~60 lines)
`KlemmaClient` — HTTP client with auto-refresh on 401.
- `get(path)`, `post(path, json)` — wrappers with auth headers

### gitops.py (~180 lines)
Git subprocess wrappers.
- `init`, `add_remote`, `add_files`, `commit`, `push`, `pull`, `fetch`
- `log`, `status`, `remote_log`, `revert_last_n`, `force_push`
- `write_gitignore` — auto-generate .gitignore for klemma projects

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

### models.py (~60 lines)
Pydantic schemas: `SourcePayload`, `FragmentPayload`, `EmbeddingPayload`, `DecisionPayload`, `SyncConfig`.

### state.py (~30 lines)
Sync state persistence — `.klemma/sync_config.json` CRUD.

## Data flow: push

```
klemma-cli push
  1. git add KLEMMA.md notes/drafts/ notes/research/ .gitignore
  2. git commit "sync: push from CLI — {date}"
  3. git push klemma main
  4. Read local library.db → POST /sync/push/library (sources + fragments)
  5. Read local embeddings → POST /sync/push/embeddings (base64 chunks)
  6. Update .klemma/sync_config.json last_push timestamp
```

## Data flow: pull

```
klemma-cli pull
  1. git pull klemma main (conflicts → print instructions)
  2. GET /sync/pull/library → write sources/fragments to local library.db
  3. GET /projects/{id}/drafts → list; GET /projects/{id}/drafts/{name} per file
     → write to draft/{name} only if content differs (ADR-016)
  4. Update .klemma/sync_config.json last_pull timestamp
```

## Tests

- `cli/tests/test_gitops.py` — git subprocess wrapper tests
- `cli/tests/test_project.py` — project discovery tests
- `cli/tests/test_sync.py` — library DB read tests + pull_drafts tests (5 cases)
- `tests/test_api_sync.py` — backend sync API endpoint tests (15 tests)

See: [API Routes](../src/klemma/api/routes/CLAUDE.md) | [Root](../CLAUDE.md)
