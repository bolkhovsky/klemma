# ADR-014: Three-tier library split

**Status**: Accepted
**Date**: 2026-03-10
**Epic**: #82
**Supersedes**: ADR-005 (three-tier spec, previously unimplemented)

## Context

Klemma uses a monolithic SQLite database per project. When a user works on multiple projects (dissertation + papers) with overlapping PDFs, extraction and embedding are repeated for every project — wasting API costs (Claude ~$0.03/paper, OpenAI ~$0.001/paper) and wall-clock time (~30s/paper). The `inherit_db` mechanism (ADR-002) attempted to solve this by attaching a parent DB read-only, but it's fragile, creates merge complexity in 9 read methods, and doesn't scale to SaaS (no concept of shared corpus).

Current state: 966 tests, cli.py ~5950 lines, state.py ~965 lines, 8 domain repositories, DB v11.

## Decision

Split the monolithic `klemma.db` into two databases:

```
~/.klemma/library.db              ← shared: papers, fragments, embeddings, citations
project/.klemma/data/project.db   ← per-project: assignments, gaps, plans, benchmarks
```

### Key design choices

**1. Protocol interfaces as the boundary**

Three runtime-checkable Protocols: `PaperStore` (global corpus), `UserLibrary` (user's collection), `ProjectStore` (per-project data). Each has a local SQLite implementation (Phase 1) and will have a PostgreSQL implementation (SaaS, Phase 2). StateManager becomes a facade over these three stores.

**2. Content-addressable fragment IDs**

Fragment IDs use `content_hash` = SHA256(paper_id + fragment_text + page_number). This is deterministic, globally unique, and supports dedup: same PDF + same extraction = same fragment IDs. No UUIDs for immutable content.

**3. No ATTACH DATABASE**

Each Protocol implementation queries its own DB independently. The StateManager facade merges results in Python. This ensures the same code works with PostgreSQL (SaaS) — ATTACH is SQLite-specific.

**4. Dual-write migration**

During transition, `_process_single()` writes to BOTH monolithic DB and library.db. This validates the split without breaking existing flows. The monolithic write path is removed only after the project DB split is complete and `klemma migrate` has been run.

**5. Deferred to SaaS sprint**

- Privacy model (visibility levels, consent flows, `auto_share_published`)
- `user_preferences` table
- `user_tags` table
- Multi-user `user_id` columns

These add complexity without delivering value for CLI single-user mode.

### What lives where

| Data | Store | Rationale |
|------|-------|-----------|
| Paper metadata (title, authors, DOI, abstract) | PaperStore (library.db) | Same paper for everyone |
| PDF hash (SHA256) | PaperStore | Content-addressable dedup |
| Extracted fragments (text, type, page, intent) | PaperStore | Deterministic: same PDF + prompt = same fragments |
| Paper + fragment embeddings | PaperStore | Deterministic: same text + model = same vector |
| Citation graph | PaperStore | Objective bibliographic relationship |
| Extraction versioning (prompt_hash, ai_model) | PaperStore | Enables re-extraction on prompt upgrade |
| User's citekey mapping | UserLibrary (library.db) | Different users may use different BibTeX keys |
| Source status (pending/completed/failed) | UserLibrary | User's processing progress |
| Zotero integration (pdf_path, note_path) | UserLibrary | User's local Zotero instance |
| Quality score | UserLibrary | Per-user assessment |
| Chapter/section assignment | ProjectStore (project.db) | Same paper → different sections in different projects |
| Fragment relevance score, usage_hint | ProjectStore | Per-project relevance |
| Reference gaps | ProjectStore | Gaps depend on project's section structure |
| Section type map | ProjectStore | Project-specific vocabulary |
| Plans, reading queue, prune verdicts, benchmarks | ProjectStore | Per-project workflow |

## Execution plan

**4 independently shippable PRs, each maintaining backward compat:**

| PR | Name | Depends on | Deliverable |
|----|------|------------|-------------|
| A | Protocol interfaces + hashing | — | Types, data classes, pdf_hash + content_hash. Zero behavior change. |
| B | LocalPaperStore + library.db | A | Dedup on process/embed. Dual-write to library.db + monolithic. |
| C | Project DB split + migration | B | LocalProjectStore, `klemma migrate`, remove `inherit_db`. |
| D | cli.py split into command groups | — (parallel) | Reduce cli.py from ~5950 to <1000 lines. |

PR D can proceed in parallel with A/B/C.

## Consequences

- **Removes** `inherit_db` / `set_parent()` / `_merge_by_id()` — the most fragile hack in the codebase
- **Removes** 9 merge methods in StateManager that handle parent-child data merging
- **Enables** SaaS: same Protocol interfaces, PostgreSQL+pgvector backend
- **Enables** dedup: `klemma process` in project B instantly finds fragments from project A
- **Requires** `klemma migrate` for existing users (with dry-run, backup, rollback)
- **Changes** StateManager from facade-over-8-repos to facade-over-3-stores

## Risks

| Risk | Impact | Mitigation |
|------|--------|------------|
| Migration corrupts data | HIGH | Backup before migration, dry-run mode, rollback |
| Fragment ID collision during migration | MEDIUM | Content-hash is deterministic — same content = same hash |
| Performance: Python-level merge vs SQL JOIN | LOW | Profile after PR B; optimize if >100ms |
| cli.py changes across 18 commands | HIGH | PR D (cli.py split) reduces blast radius |

## Alternatives considered

1. **ATTACH DATABASE for cross-DB queries** — rejected: SQLite-specific, won't port to PostgreSQL
2. **UUID fragment IDs** — rejected: content-addressable is better for immutable content
3. **Big-bang rewrite in single PR** — rejected: too risky, 3000-5000 LOC change with no intermediate checkpoints
4. **Keep inherit_db, add dedup layer on top** — rejected: adds complexity to already fragile mechanism
