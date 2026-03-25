# ADR-009: SaaS Architecture — High-Level Decisions

- **Status**: Draft (Tier 2 — do not implement until Tier 1 complete: #82, #87, #84)
- **Date**: 2026-03-06
- **Depends on**: ADR-001 (SQLite/WAL), ADR-005 (Three-tier library #82)
- **Supersedes**: None

## Context

Klemma is a CLI tool (Python, SQLite, ~17K LOC) for academic writing assistance. The product vision includes a SaaS version for users without CLI skills. Before designing any UI, we need to lock down infrastructure decisions that shape the entire backend.

### Constraints

- **Budget**: Self-hosted VPS, not managed cloud (AWS/GCP). Hosting: [FirstVDS](https://firstvds.ru/products/vds_vps_hosting)
- **Audience**: Russian-speaking PhD students and researchers (initial market)
- **Team size**: Solo developer — minimize operational complexity
- **Existing architecture**: Protocol-based backends (AI, Embeddings, Storage), repository pattern, skills isolated from storage (see `architecture-decisions.md`)
- **Three-tier library** (#82) must be implemented first — it defines the data model SaaS builds on

## Decisions

### 1. Hosting Model: Self-Hosted VPS (FirstVDS)

**Decision**: Deploy on FirstVDS VPS (Linux). No managed cloud services.

**Rationale**:
- Cost-effective for early stage (VPS ~500-2000 RUB/mo vs AWS ~$50-200/mo)
- Data residency in Russia — relevant for academic users and potential institutional partnerships
- Full control over stack, no vendor lock-in
- Sufficient for initial user base (tens to low hundreds of users)

**Implications**:
- Must self-manage: TLS certs (Let's Encrypt), backups, monitoring, updates
- No managed DB — run PostgreSQL on VPS or separate DB VPS
- No auto-scaling — plan capacity manually, vertical scaling first
- Container deployment (Docker Compose) for reproducibility

**Migration path**: If load outgrows a single VPS, move to multiple VDS instances behind a load balancer, or migrate to managed hosting. Protocol interfaces make DB backend swappable.

### 2. Architecture: API-First (not monolith)

**Decision**: Separate API server + static frontend. API serves JSON, frontend is a SPA or SSR app that consumes it.

**Rationale**:
- CLI already exists as a "client" — API becomes the shared backend for CLI and web
- Skills layer is already decoupled from storage and CLI — it maps directly to API endpoints
- Enables future mobile client or third-party integrations without rebuilding
- Easier to test: API contract tests independent of UI

**Stack**:
- **API framework**: FastAPI (Python, async, OpenAPI docs out of the box, same language as klemma core)
- **Task queue**: Redis + rq (or Celery) for long-running jobs (PDF extraction, embedding)
- **Frontend**: TBD — not part of this ADR. Could be Next.js, plain HTMX, or Svelte. Decision deferred until API is stable.

**API boundary**:
```
FastAPI app
  ├── /auth/*          — registration, login, token refresh
  ├── /library/*       — CRUD on user's paper collection
  ├── /projects/*      — project management, section assignments
  ├── /process/*       — trigger extraction, embedding (async jobs)
  ├── /analyze/*       — coverage, gaps, suggestions
  ├── /write/*         — research, draft generation
  └── /admin/*         — system stats, corpus management
```

Skills become service-layer functions called by API handlers — same code path as CLI, different I/O boundary.

### 3. Database: PostgreSQL + pgvector

**Decision**: PostgreSQL with pgvector extension for production. SQLite remains for CLI (local) mode.

**Rationale**:
- ADR-001 already identified SQLite as non-scalable for SaaS (single-writer, file-based)
- pgvector provides native vector similarity search — replaces manual cosine distance on BLOBs
- PostgreSQL is battle-tested for multi-user concurrent access
- Three-tier schema (#82) maps directly: `papers`/`fragments`/`embeddings` tables gain `user_id`/`project_id`
- Can run on same VPS initially, move to dedicated DB VPS later

**Protocol layer** (from #82):
```
PaperStore    → LocalPaperStore (SQLite)    | PostgresPaperStore
UserLibrary   → LocalUserLibrary (SQLite)   | PostgresUserLibrary
ProjectStore  → LocalProjectStore (SQLite)  | PostgresProjectStore
```

Same Protocol interfaces, different backends. CLI uses SQLite, SaaS uses PostgreSQL. No code duplication in skills or business logic.

**Backup strategy**: `pg_dump` daily to a separate volume or remote storage. Tested restore procedure.

### 4. Authentication: Email/Password + OAuth2 (Yandex ID)

**Decision**: Own auth with JWT tokens. Email/password as primary, Yandex ID as social login.

**Rationale**:
- Target audience is Russian academic community — Yandex ID has higher penetration than Google among this demographic
- Email/password is essential — many institutional users have restrictions on social auth
- JWT (access + refresh tokens) — stateless API auth, standard for SPA frontends
- No need for enterprise SSO (SAML/LDAP) at this stage

**Implementation**:
- Password hashing: argon2 (via `argon2-cffi`)
- JWT: `python-jose` or `authlib`
- Refresh token rotation, short-lived access tokens (15 min)
- Rate limiting on auth endpoints (bruteforce protection)
- Email verification required before full access

**Not now**: Google OAuth, GitHub OAuth, institutional SSO — add when user demand warrants it.

### 5. PDF/File Storage: Local Filesystem (VPS)

**Decision**: Store uploaded PDFs on VPS filesystem. No S3 or object storage initially.

**Rationale**:
- Single VPS — no need for distributed storage yet
- Simpler ops: no MinIO/S3 setup, just a directory with proper permissions
- PDF files are write-once, read-rarely (extracted on upload, then fragments are used)
- Expected volume: 1000 users x 200 papers x 2MB avg = ~400GB — fits on a VPS disk

**Structure**:
```
/data/klemma/
  pdfs/{paper_id_prefix}/{paper_id}.pdf    — content-addressed by paper_id
  exports/                                  — generated BibTeX, drafts (temp)
  backups/                                  — pg_dump output
```

**Migration path**: When storage exceeds VPS capacity or multi-node is needed, swap to S3-compatible storage (MinIO self-hosted or Yandex Object Storage). Upload/download through a `FileStore` protocol — same pattern as DB backends.

### 6. Background Jobs: Redis + rq

**Decision**: Redis as job queue backend, `rq` (Redis Queue) for async task processing.

**Rationale**:
- PDF extraction takes 30-60s per paper (Claude API call) — cannot block HTTP request
- Embedding computation is batch-friendly — queue enables prioritization
- `rq` is minimal (~500 LOC wrapper), Python-native, trivial to set up on a single VPS
- Redis also serves as cache layer (session store, rate limiting counters)

**Job types**:
| Job | Trigger | Duration | Priority |
|-----|---------|----------|----------|
| PDF extraction | Upload / `POST /process` | 30-60s | Normal |
| Fragment embedding | After extraction | 5-10s | Normal |
| Gap analysis | On demand | 10-30s | Low |
| Draft generation | On demand | 30-120s | Low |
| Corpus dedup check | After extraction | 1-5s | High |

**Not now**: Celery (overkill for single-node), distributed workers. Scale to Celery only if job throughput demands it.

### 7. Deployment: Docker Compose

**Decision**: Single-node Docker Compose deployment on FirstVDS VPS.

**Rationale**:
- Reproducible environment: same setup locally and in production
- Simple orchestration for 4-5 services (API, worker, PostgreSQL, Redis, nginx)
- No Kubernetes overhead for a solo developer with one node
- Easy rollback: `docker compose up -d --build` with tagged images

**Compose services**:
```yaml
services:
  api:        # FastAPI (uvicorn)
  worker:     # rq worker(s)
  db:         # PostgreSQL + pgvector
  redis:      # Queue + cache
  nginx:      # Reverse proxy, TLS termination, static files
```

**TLS**: Let's Encrypt via certbot (nginx plugin) or Caddy as alternative.

**CI/CD**: GitHub Actions builds Docker image, pushes to registry, SSH deploy to VPS. Simple `docker compose pull && docker compose up -d`.

### 8. Offline-First Sync (Future — Phase 2+)

**Decision**: Design for eventual local-cloud sync, but do not implement in Phase 1.

**Rationale**:
- CLI users should be able to work offline and sync when connected
- Three-tier library (#82) Protocol interfaces enable this: local SQLite and cloud PostgreSQL behind same Protocol
- Sync is complex (conflict resolution, partial sync, merge strategies) — premature to build now

**Phase 1**: SaaS is online-only. CLI remains local-only. No sync.
**Phase 2**: Read-only sync — CLI user can pull their cloud library locally.
**Phase 3**: Bidirectional sync with conflict resolution.

## Architecture Diagram

```
                    ┌─────────────────────────────────┐
                    │         FirstVDS VPS             │
                    │                                  │
  Users ──HTTPS──▶  │  nginx (TLS, static, proxy)     │
                    │    │                             │
                    │    ▼                             │
                    │  FastAPI (uvicorn, N workers)    │
                    │    │         │                   │
                    │    │    enqueue jobs              │
                    │    │         │                   │
                    │    ▼         ▼                   │
                    │  PostgreSQL  Redis               │
                    │  + pgvector    │                 │
                    │    ▲         ▼                   │
                    │    │    rq worker(s)             │
                    │    │      │                      │
                    │    └──────┘                      │
                    │         │                        │
                    │    /data/klemma/pdfs/            │
                    └─────────────────────────────────┘

  CLI users ──▶ local SQLite (same Protocol interfaces)
```

## What This ADR Does NOT Decide

- **Frontend framework** — deferred until API is stable
- **UI/UX design** — no dashboard specs, no wireframes
- **Pricing model** — free tier vs paid, limits, quotas
- **Multi-tenancy isolation** — row-level security vs separate schemas (decide with #82)
- **Monitoring stack** — Prometheus/Grafana vs simpler alternatives (decide at deploy time)
- **Domain/branding** — Klemma (#76) vs klemma.ai (pending decision)

## Risks

| Risk | Impact | Mitigation |
|------|--------|------------|
| Single VPS = single point of failure | HIGH | Daily backups to remote storage, documented restore procedure |
| PostgreSQL on same VPS as app | MEDIUM | Monitor resources, move DB to separate VPS if contention appears |
| Self-managed TLS/security | MEDIUM | Automated certbot renewal, `unattended-upgrades`, fail2ban |
| FirstVDS outage | LOW-MEDIUM | Stateless API + pg_dump = can redeploy to any VPS provider in hours |
| Redis data loss on restart | LOW | Redis is cache/queue only, no critical state. Jobs are idempotent. |

## Success Criteria

- [ ] API serves the same core loop (acquire/process/map/analyze/write) as CLI
- [ ] Skills code is shared between CLI and API (zero duplication)
- [ ] Can deploy full stack with `docker compose up` on a fresh VPS in <30 min
- [ ] 10 concurrent users, 50 papers each, no degradation
- [ ] Daily automated backups with tested restore

## References

- ADR-001: SQLite with WAL mode (migration path to PostgreSQL)
- ADR-005: Three-tier library split (#82) — data model foundation
- Product vision: `product-vision.md` — SaaS audience, metrics, constraints
- Architecture decisions: `architecture-decisions.md` — red lines, dependency flow
