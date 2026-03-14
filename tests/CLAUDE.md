# Tests

## Current test suite (1240 tests)
- `test_paper_store.py` (~200 lines) — 22 tests: `LocalPaperStore` schema (version=1, all 6 tables, migration idempotency, new-dir creation), register/find CRUD (UUID return, idempotency by pdf_hash, find by DOI, metadata roundtrip), fragments (save/get, INSERT OR IGNORE dedup, types), paper-level embeddings (roundtrip, upsert, missing=None), fragment-level embeddings (roundtrip, empty), dual-write cache scenario (second project hits library cache)
- `test_user_library.py` (~185 lines) — 23 tests: `LocalUserLibrary` schema (version=2, 3 tables, PaperStore coexistence on same db_path, idempotency), add_source (basic, metadata, upsert), get_source missing=None, resolve_paper_id, get_existing_citekeys (empty, multiple), update_status, count, chapters/sections replaced on upsert
- `test_project_store.py` (~240 lines) — 29 tests: `LocalProjectStore` schema (version=2, 4 tables including prune_verdicts, v1→v2 migration idempotency, parent-dir creation), set_source_sections (basic, upserts paper_id, replaces sections, empty), get_source_sections (data, missing), get_sources_by_section (multi-source, empty), get_coverage_stats (empty, with data), get_reference_gaps (stub returns []), register_fragment (basic, INSERT OR IGNORE dedup), count_sources (empty, multiple), prune_verdicts (save/get basic, @ prefix strip, replaces all, get_drop_ids, summary counts, summary empty, filter by verdict, clear single, skip empty citekeys)
- `test_errors.py` (33 lines) — `KlemmaAIError` hierarchy, `retryable` classification, cause chaining
- `test_hashing.py` (~80 lines) — 15 tests: `compute_pdf_hash` (determinism, different content, hex format, missing file), `compute_content_hash` (determinism, uniqueness by paper/text/page, None page, empty text), `compute_prompt_hash` (determinism, uniqueness, 16-char truncation, empty)
- `test_protocols.py` (~80 lines) — 10 tests: `PaperRecord`/`FragmentRecord`/`UserSource` dataclass defaults + all-fields, runtime-checkable Protocol verification, non-implementing class rejection
- `test_ai.py` (170 lines) — `extract_json()`, `AIProviderBase`, `AICallResult` dataclass, `call_with_meta()` base + Claude, `create_ai()` factory
- `test_ai_litellm.py` (265 lines) — `LiteLLMClient` with mocked litellm: call, json_mode, base_url, api_key, reasoning model detection, retry, `call_with_meta()` with structured error mapping + token extraction
- `test_ai_openai.py` (127 lines) — deprecated `OpenAIClient`: DeprecationWarning, delegation to LiteLLMClient, model prefixing
- `test_ai_contract.py` (203 lines) — parametrized contract tests: all 3 backends (Claude/LiteLLM/OpenAI) satisfy same behavioral contract (call → str|None, call_json → dict|None, call_with_meta → AICallResult, protocol conformance)
- `test_klemmarc.py` (244 lines) — `_load_klemmarc()`, `_derive_provider()`, `AIConfig.api_key` resolution, `resolve_effective_config()` with klemmarc, chmod 600 enforcement
- `test_project_discovery.py` (645 lines) — 42 tests for Git-style project discovery, config merging, context aggregation, prompt resolution, tags inheritance, setup, and deep merge
- `test_embeddings.py` (354 lines) — `EmbeddingProvider` protocol, `cosine_similarity`, `SemanticScholarEmbeddings`/`LocalSPECTEREmbeddings`/`OpenAIEmbeddings` with mocked backends, `create_embeddings()` factory
- `test_citation_graph.py` (245 lines) — citation graph storage (`save_citation_links`, `get_citation_graph`), intent scoring for reference gaps, `_migrate_schema()` v1→v4
- `test_mcp.py` (404 lines) — `ToolRegistry` CRUD + routing, `ToolInfo`, SPECTER MCP server creation (4 tools), embed/similar/batch tool logic via helpers, `compare_intents()` (LLM vs S2 accuracy, confusion matrix)
- `test_intent_scoring.py` (~490 lines) — intent-weighted reference gap scoring, configurable section weights (`TestSectionWeights`), intent coverage matrix, fragment citation intent classification
- `test_interactive_init.py` (347 lines) — interactive `klemma init` wizard, auto-discovery, config generation
- `test_cli_init_outline.py` (37 lines) — `klemma init --outline` CLI behavior, AI-missing skip, no-outline no AI call
- `test_cli_init_non_interactive.py` (~100 lines) — 6 tests: `klemma init` non-interactive mode (#54) — --name creates project, CLI flags populate config.yaml, KLEMMA.md content, --non-interactive alias, minimal init, no wizard prompts
- `test_cli_embed.py` (~190 lines) — `klemma embed` multi-citekey CLI behavior, missing-key warnings, `--fragments` flag (embed + dry-run), `--sections` flag (section centroid computation, dry-run, roundtrip CRUD)
- `test_cli_add.py` (~270 lines) — `klemma add` unified ingestion: `_detect_input_type` (6 tests), citekey mode (4 tests: not found, auto-register from library, section assign, multi-section), URL mode (2 tests), flags (3 tests: no-process, no-embed, help)
- `test_coach.py` (~300 lines) — coach skill heuristics (parametrized adequacy/intent/readiness/saturation), project health check, section hint generator, CLI integration (help, health check, section focus, JSON output, inline hint in add)
- `test_cli_model_override.py` (~96 lines) — `--model` CLI override: verifies override reaches `create_ai()`, default model preserved without flag
- `test_db_inheritance.py` (~195 lines) — 15 tests: DB inheritance (#55) — parent sources/fragments/embeddings/gaps visible through child, child wins on duplicate, write isolation, coverage stats aggregation, RAG across both DBs, disabled/absent parent
- `test_repositories.py` (~240 lines) — repository composition, facade delegation, per-repo CRUD roundtrips, new public methods (`get_existing_source_ids`, `get_sources_without_embeddings`), fragment embedding save/retrieve roundtrip, embedding stats, unembedded fragments, top-K cosine retrieval
- `test_agent_rag.py` (~80 lines) — 4 tests: agent context with fragments (RAG), without embeddings, no fragment embeddings, embed failure graceful degradation
- `test_researcher_budget.py` (~280 lines) — 12 tests: `_fit_prompt_budget()` progressive reduction (7 tests), RAG-first fragment retrieval (5 tests: RAG used, fallback on few results, fallback without embeddings, embed error graceful degradation, dedup with fallback), `@pytest.mark.benchmark` section vs RAG quality comparison
- `test_three_tier_backward_compat.py` (~300 lines) — 18 tests (Phase 1G): legacy StateManager reads (sources, individual source, fragments, coverage stats, get_by_section, fragment_stats), library miss on legacy papers (pdf_hash, doi, citekey, resolve_paper_id, existing_citekeys), paper_store=None graceful degradation (CRUD works, fragments unaffected), three-tier coexistence (library.db alongside klemma.db, paper→library no klemma.db change, project.db alongside, coverage stats unaffected), legacy embeddings survive library.db creation
- `test_evaluation.py` (~320 lines) — 38 tests: pure metrics (intent_metrics, precision@K, recall@K, nDCG@K), dataset load/validate/roundtrip, runner integration (intent/gap/embedding benchmarks with seeded DB), export template, `test_run_gap_benchmark_with_reranked_gaps` (verifies reranked list bypasses DB)
- `test_security_hardening.py` — path traversal and download safety tests
- `test_benchmark_history.py` (179 lines) — 17 tests: benchmark run save/get roundtrip, ordering, paper filtering, latest_run, compare deltas, migration idempotency, schema version, build_results_summary, compute_dataset_hash
- `test_candidates.py` (116 lines) — 10 tests: candidate ranking by citation coverage, benchmarked penalization, limit, has_pdf flag, format_candidate_hint output
- `test_prepare.py` (251 lines) — 19 tests: _titles_match (exact/case/partial/no match/empty), resolve_arxiv (success/no match/error), resolve_crossref_doi, resolve_unpaywall, resolve_pdf_url (arxiv first/fallback/no resolution), prepare_benchmark (dry_run/no links/unfetchable/actual fetch)
- `test_metadata.py` (~350 lines) — 18 tests: extract_pdf_metadata (title+author/empty/first-page fallback), lookup_s2 (success/no match/API error), resolve_metadata (CLI wins/PDF+S2/no sources), citekey from real metadata, DB update_source_info (persist/partial), migration v6 column check, Zotero API (is_running true/false, create_item success, parse_authors, get_bbt_citekey)
- `test_auto_pipeline.py` (199 lines) — 8 tests: run_analyst_from_source (success/missing source/no pdf), run_auto_benchmark (explicit paper/analyst failure/auto-select/no candidates/comparison)
- `test_process_dedup.py` (~100 lines) — 7 tests: citekey fast-path dedup in `_process_single()` (#162) — reuse without PDF read, fragments saved to project state, fall-through when no frags/not in library, force bypasses, no user_library falls through, multiple fragments
- `test_auto_migrate.py` (~200 lines) — 10 tests: `_auto_migrate_to_three_tier()` — migrates source to library.db, registers citekey in user library, migrates fragments, migrates sections to project.db, creates .db.bak backup, returns (0,0,0) when no mono DB or empty DB, idempotent second run (INSERT OR IGNORE), multiple sources, missing source_sections table handled gracefully
- `test_suggester.py` (~190 lines) — 16 tests: `suggest_acquisitions` (empty, basic resolution, search None, DOI-only, limit, sort by score, search error), recency filter (on/off/no-year), `_parse_sections` (empty, single, group_concat, dedup, plain CSV fallback)
- `test_relevance_gate.py` (~130 lines) — 14 tests: auto_classify `matched` field (pattern match/no match/project config/empty mapping/abstract), `generate_chapter_mapping` (basic/sections/defaults/empty/stopwords/short words), `ProjectConfig.auto_register` default + `from_dissertation` backward compat
- `test_librarian_recency.py` (~100 lines) — 7 tests: library recency filter (old filtered/classic kept/no year kept/disabled/defaults/boundary)
- `test_cli_restructuring.py` (~55 lines) — 5 tests: suggest top-level (visible in help, help accessible), gaps suggest backward compat (help, hidden), bare gaps deprecation, library recommend deprecation
- `test_context_loader.py` (~50 lines) — `load_research_report()`: notes/research/ path, legacy fallback, missing, empty, preference
- `test_research_library_fallback.py` (~220 lines) — 11 tests: `supplement_fragments_from_library()` (adds frags, dedup by id, skips unknown citekeys, multiple sources, citekey-field fallback, empty sources), `research_section()` library guard (supplement when local empty, no-op when paper_store=None, no-op when ≥10 local), `build_agent_context()` library guard (supplement when RAG empty, no-op when ≥5 RAG frags)
- `test_context_aware_rag.py` (~280 lines) — 27 tests: `parse_argument_blocks` (10 tests: 3-block parse, order, titles, descriptions, citations, empty, no section, single, no citations, section boundary), `retrieve_rag_fragments_per_block` (7 tests: returns blocks, required fields, dedup across blocks, empty blocks, no embeddings, embed failure, no description), `fit_prompt_budget` with RAG (4 tests: passthrough, section trimmed first, RAG trimmed last, 4-tuple without RAG), `section_draft.md` template with RAG (6 tests: RAG section renders, both RAG+fallback, flat without RAG, empty, multiple blocks, He et al. reference)
- `test_drafter.py` (~190 lines) — `DraftResult` defaults, `_extract_citations` regex, `_filter_hallucinated_citations` (valid/invalid/empty/dedup), `generate_draft` pipeline (with/without research, filtering, empty response), `section_draft.md` template rendering
- `test_duplicate_checker.py` (~170 lines) — 23 tests: `_normalize`, DOI dedup (same/different/empty/3-way), author+year+title (match/different year/author/missing/and-separator), title prefix (match/short skipped/case insensitive), `find_duplicates` (empty/single/none/dedup across strategies/sorted/dataclass/None fields)
- `test_reassign.py` (~240 lines) — 12 tests: cosine similarity (identical/different), best section match, suggestion filtering (different section/same section/below threshold), CLI argument handling (help, section-without-citekey, citekey-not-found), fragment metadata repo (returns embedded/empty/model filter)
- `test_prompts.py` (~280 lines) — 25 tests: all 12 prompt templates render without Jinja2 errors, coverage check (all shipped templates have test contexts), reconstruct.md ablation variants (default/max_recs/fewshot/combined), prompt hash determinism + uniqueness, AblationParams defaults + to_snapshot + with_fewshot factory
- `test_notes_subdirs.py` (~190 lines) — 10 tests: `notes/` subdirectory layout (#34) — researcher save/load (new path, legacy fallback, preference), librarian save to `notes/library/`, agent scanner finds `notes/{research,library,agents}/`, backward compat for flat reports, `update_agents_index` (generation, no-dir, empty-dir, reverse sort)
- `test_task_model_routing.py` (~166 lines) — 13 tests: `resolve_task_model()` routing (no classes, not in classes, claude returns class, class_model_map priority, litellm with/without map, openai with map), model_override forwarding (ClaudeClient subprocess args, LiteLLMClient completion kwargs), AIConfig task_classes/class_model_map parsing
- `test_config_validation.py` (~200 lines) — 21 tests: `_warn_config_issues()` — misplaced keys at root (task_classes, model, backend, vault_path), unknown keys (top-level, inside sections, api_keys/mcp exemptions), bare Claude shorthands with litellm backend (model, task_classes without map, correct backend no warning), edge cases (empty/None/non-dict, source label, multiple issues)
- `test_section_types.py` (~300 lines) — 50 tests: `SectionType` enum (values, str comparison, keyword coverage), `infer_section_type` (18 ru/en names, unknown, empty, case insensitive), `resolve_section_identifier` (numeric, semantic, keyword, config map, empty), config `section_type_map` (auto-infer from dissertation, explicit, weights), DB migration v7 (version, columns, table), `sync_section_types` (backfill, explicit map, idempotent), repository queries with `section_type` (sources, fragments, coverage stats, section sources)
- `test_integration_three_tier.py` (~400 lines) — 10 tests: cross-project library.db sharing (ADR-014 Phase 1G) — process dedup (cache hit skips AI, fragments saved to state, source marked completed, cache miss calls AI), dual-write (paper+fragments in library.db after novel extraction, citekey in user_library), embed dedup (library cache skips API, no cache calls API), E2E (project A processes → library written → project B deduplicates)

## Patterns
- pytest + `unittest.mock` (`patch`, `MagicMock`)
- AI backends tested with mocked subprocess (Claude) or mocked SDK (OpenAI)
- Config fixtures use `AIConfig` Pydantic models with test values
- MCP tests: `_MCP_AVAILABLE` flag + `@_skip_no_mcp` decorator for CI environments without mcp package
- Tool logic tested via helper functions that replicate tool behavior (avoids running MCP server)
- No integration tests (all mocked)

## Running
```bash
pip install -e ".[dev]"
pytest tests/
```

## Adding tests
- Mock external dependencies (AI CLIs, SDKs, Zotero API, MCP servers, S2 API)
- Mirror source structure: `test_<module>.py` for `src/klemma/<module>.py`
- For MCP-dependent tests: wrap with `@_skip_no_mcp` (requires `klemma[mcp]`)

## Maintaining this file
Update when: adding new test files (add to "Current test suite"), changing testing patterns, or adding integration test infrastructure.

See: [Core infrastructure](../src/klemma/CLAUDE.md) for AI provider architecture
