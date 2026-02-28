# Tests

## Current test suite (472 tests)
- `test_errors.py` (33 lines) — `KlemmaAIError` hierarchy, `retryable` classification, cause chaining
- `test_ai.py` (170 lines) — `extract_json()`, `AIProviderBase`, `AICallResult` dataclass, `call_with_meta()` base + Claude, `create_ai()` factory
- `test_ai_litellm.py` (265 lines) — `LiteLLMClient` with mocked litellm: call, json_mode, base_url, api_key, reasoning model detection, retry, `call_with_meta()` with structured error mapping + token extraction
- `test_ai_openai.py` (127 lines) — deprecated `OpenAIClient`: DeprecationWarning, delegation to LiteLLMClient, model prefixing
- `test_ai_contract.py` (203 lines) — parametrized contract tests: all 3 backends (Claude/LiteLLM/OpenAI) satisfy same behavioral contract (call → str|None, call_json → dict|None, call_with_meta → AICallResult, protocol conformance)
- `test_klemmarc.py` (244 lines) — `_load_klemmarc()`, `_derive_provider()`, `AIConfig.api_key` resolution, `resolve_effective_config()` with klemmarc, chmod 600 enforcement
- `test_project_discovery.py` (645 lines) — 42 tests for Git-style project discovery, config merging, context aggregation, prompt resolution, tags inheritance, setup, and deep merge
- `test_embeddings.py` (354 lines) — `EmbeddingProvider` protocol, `cosine_similarity`, `SemanticScholarEmbeddings`/`LocalSPECTEREmbeddings`/`OpenAIEmbeddings` with mocked backends, `create_embeddings()` factory
- `test_citation_graph.py` (245 lines) — citation graph storage (`save_citation_links`, `get_citation_graph`), intent scoring for reference gaps, `_migrate_schema()` v1→v4
- `test_mcp.py` (404 lines) — `ToolRegistry` CRUD + routing, `ToolInfo`, SPECTER MCP server creation (4 tools), embed/similar/batch tool logic via helpers, `compare_intents()` (LLM vs S2 accuracy, confusion matrix)
- `test_intent_scoring.py` (422 lines) — intent-weighted reference gap scoring, intent coverage matrix, fragment citation intent classification
- `test_interactive_init.py` (347 lines) — interactive `klemma init` wizard, auto-discovery, config generation
- `test_cli_init_outline.py` (37 lines) — `klemma init --outline` CLI behavior, AI-missing skip, no-outline no AI call
- `test_cli_embed.py` (26 lines) — `klemma embed` multi-citekey CLI behavior and missing-key warnings
- `test_repositories.py` (~130 lines) — repository composition, facade delegation, per-repo CRUD roundtrips, new public methods (`get_existing_source_ids`, `get_sources_without_embeddings`)
- `test_evaluation.py` (~320 lines) — 38 tests: pure metrics (intent_metrics, precision@K, recall@K, nDCG@K), dataset load/validate/roundtrip, runner integration (intent/gap/embedding benchmarks with seeded DB), export template, `test_run_gap_benchmark_with_reranked_gaps` (verifies reranked list bypasses DB)
- `test_security_hardening.py` — path traversal and download safety tests
- `test_benchmark_history.py` (179 lines) — 17 tests: benchmark run save/get roundtrip, ordering, paper filtering, latest_run, compare deltas, migration idempotency, schema version, build_results_summary, compute_dataset_hash
- `test_candidates.py` (116 lines) — 10 tests: candidate ranking by citation coverage, benchmarked penalization, limit, has_pdf flag, format_candidate_hint output
- `test_prepare.py` (251 lines) — 19 tests: _titles_match (exact/case/partial/no match/empty), resolve_arxiv (success/no match/error), resolve_crossref_doi, resolve_unpaywall, resolve_pdf_url (arxiv first/fallback/no resolution), prepare_benchmark (dry_run/no links/unfetchable/actual fetch)
- `test_auto_pipeline.py` (199 lines) — 8 tests: run_analyst_from_source (success/missing source/no pdf), run_auto_benchmark (explicit paper/analyst failure/auto-select/no candidates/comparison)
- `test_prompts.py` (~270 lines) — 25 tests: all 12 prompt templates render without Jinja2 errors, coverage check (all shipped templates have test contexts), reconstruct.md ablation variants (default/max_recs/fewshot/combined), prompt hash determinism + uniqueness, AblationParams defaults + to_snapshot + with_fewshot factory

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
