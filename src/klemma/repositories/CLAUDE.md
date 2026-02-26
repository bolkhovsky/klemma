# Repositories

Domain repositories decomposed from `StateManager` (1599 lines -> 8 focused modules). StateManager remains as backward-compatible facade.

## Architecture

```
StateManager (facade)
├── _conn()          — shared SQLite connection factory (WAL + FK)
├── _init_db()       — schema creation + migrations
├── _migrate_schema()— idempotent PRAGMA user_version
└── delegates to:
    ├── SourceRepository      — source CRUD, status, sections, Zotero keys, vault sync
    ├── FragmentRepository    — fragment CRUD, intent coverage
    ├── EmbeddingsStoreRepository — vector BLOB storage, coverage stats
    ├── GapsRepository        — reference gaps, coverage, scoring, semantic rerank
    ├── CitationsRepository   — citation links, graph stats, co-citation, author groups
    ├── PlansRepository       — daily plans, reading queue, writing streak
    ├── PruneRepository       — prune verdicts, protection logic
    └── BenchmarkRepository   — benchmark run history, comparison
```

All repos receive `StateManager._conn` as their connection factory via `BaseRepository.__init__`.

## Modules

### base.py (8 lines)
Base class providing shared `_conn` factory.
- `BaseRepository` — all repos inherit from this

### sources.py (~400 lines)
Source lifecycle, sections, Zotero key management, vault sync.
- `register_sources()`, `mark_completed()`, `get_source()`, `get_stats()`
- `set_source_sections()` — replaces old `_set_sections_inline`
- `get_existing_source_ids()` — replaces old direct `_conn()` usage in cli.py
- `get_sources_without_embeddings()` — replaces old direct `_conn()` usage in cli.py
- `sync_source_sections()` — vault frontmatter bidirectional sync
- `rename_source()`, `delete_source()` — cascade operations

### fragments.py (~120 lines)
Fragment CRUD and citation intent coverage.
- `save_fragments()`, `get_fragments()`, `get_fragment_stats()`
- `get_intent_coverage()` — section x intent matrix

### embeddings_store.py (~100 lines)
Vector BLOB storage with model versioning.
- `save_embedding()`, `get_embedding()`, `get_all_embeddings()`
- `get_embedding_stats()` — coverage by model

### gaps.py (~280 lines)
Reference gaps, coverage analysis, intent-weighted scoring.
- `get_reference_gaps()` — aggregated with intent-weighted scoring formula
- `rerank_gaps_semantic()` — centroid-based semantic reranking (cross-repo via callables)
- `resolve_gaps()` — auto-resolve against library entries
- `get_coverage_stats()`, `get_gaps()`, `reset_non_completed()`

### citations.py (~200 lines)
Citation graph: links, stats, co-citation, author network.
- `save_citation_links()` — MD5 title hash for dedup
- `get_citation_graph_stats()`, `get_co_cited()`, `get_key_author_groups()`

### plans.py (~120 lines)
Daily plans and reading queue.
- `save_plan()`, `get_plan()`, `get_writing_streak()`
- `add_to_reading_queue()`, `get_next_reading()`, `complete_reading()`

### prune.py (~120 lines)
Library audit recommendations.
- `save_prune_verdicts()` — hard-protects valuable sources
- `get_prune_drop_ids()`, `get_prune_summary()`, `get_prune_verdicts()`

### benchmarks.py (~148 lines)
Benchmark run history persistence and comparison.
- `save_run()` — persist benchmark run with metrics, config snapshot, git commit
- `get_runs(limit, paper_citekey?)` — list runs newest first, optional paper filter
- `get_run(run_id)` — fetch single run by ID
- `get_latest_run(paper_citekey?)` — most recent run
- `compare_runs(id_a, id_b)` — delta for shared summary metric keys
- `get_benchmarked_citekeys()` — set of all benchmarked paper citekeys
- `compute_dataset_hash(path)` — SHA256 of dataset file for reproducibility

## Cross-repo dependencies

- `gaps.rerank_gaps_semantic()` needs embedding data — resolved via callable injection from StateManager
- `sources.get_by_chapter/section` and `plans.get_next_reading` filter by prune drops — use SQL subquery (no repo import)

## Maintaining this file
Update when repos are added/renamed, or when methods move between repos.

See: [Core infrastructure](../CLAUDE.md), [Tests](../../../tests/CLAUDE.md)
