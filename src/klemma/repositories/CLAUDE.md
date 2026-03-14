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

### sources.py (~440 lines)
Source lifecycle, sections, Zotero key management, vault sync.
- `register_sources()`, `mark_completed()`, `get_source()`, `get_stats()`, `update_source_info()` (persist title/authors/year/abstract/doi)
- `get_all_sources()` — includes `year` column for recency filtering
- `get_all_sources_metadata()` — full metadata (title, authors, year, doi) for dedup checks
- `set_source_sections()` — replaces old `_set_sections_inline`
- `get_by_section(section, section_type?)` — filter by numeric section or semantic type (#67)
- `get_existing_source_ids()` — replaces old direct `_conn()` usage in cli.py
- `get_sources_without_embeddings()` — replaces old direct `_conn()` usage in cli.py
- `sync_source_sections()` — vault frontmatter bidirectional sync
- `rename_source()`, `delete_source()` — cascade operations

### fragments.py (~330 lines)
Fragment CRUD, citation intent coverage, fragment-level embeddings.
- `save_fragments()` — `INSERT OR IGNORE` (UNIQUE constraint on source_id+fragment_text prevents duplicates); updates `fragment_count` via `COUNT(*)` subquery (not inserted count) so repeated calls never reset to 0
- `get_fragments(section_type?)`, `get_fragment_stats()`
- `get_intent_coverage()` — section x intent matrix
- `get_embedded_fragment_metadata(model?)` — id, source_id, section, chapter, text_preview for fragments with embeddings
- `save_fragment_embedding()` — store vector BLOB (struct.pack float32)
- `get_fragment_embeddings(model?)` — return `{fragment_id: vector}`
- `get_fragment_embedding_stats()` — coverage stats (total, embedded, by model)
- `get_unembedded_fragments()` — fragments missing embeddings
- `retrieve_similar_fragments(query_embedding, top_k, model?)` — top-K cosine retrieval
- `save_reassign_skip()`, `save_reassign_skips_batch()`, `get_reassign_skips()`, `clear_reassign_skips()` — legacy skip persistence (unused since batch --apply removed)

### embeddings_store.py (~180 lines)
Vector BLOB storage with model versioning.
- `save_embedding()`, `get_embedding()`, `get_all_embeddings()`
- `get_embedding_stats()` — coverage by model
- `save_section_embedding(section, embedding, model, source_count)` — UPSERT section centroid BLOB
- `get_section_embedding(section, model?)` — returns `(vector, model, source_count)` or None
- `get_all_section_embeddings(model?)` — returns `{section: vector}` dict
- `get_section_embedding_stats()` — `{total_sections, embedded_sections, models}`

### gaps.py (~350 lines)
Reference gaps, coverage analysis, intent-weighted scoring.
- `get_reference_gaps(section?, limit?, section_weights?)` — aggregated with intent-weighted scoring formula; `section_weights` dict maps section IDs to `w_s ∈ (0,1]` (unlisted→0.5, None→uniform 1.0)
- `get_section_sources(section, section_type?)` — filter by numeric section or semantic type (#67)
- `rerank_gaps_semantic()` — centroid-based semantic reranking (cross-repo via callables)
- `resolve_gaps()` — auto-resolve against library entries
- `get_coverage_stats()` — includes `section_types` dict with per-type source counts (#67)
- `get_gaps()`, `reset_non_completed()`

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
