# Tests

## Current test suite (226 tests)
- `test_ai.py` (111 lines) — `extract_json()`, `AIProviderBase`, `create_ai()` factory, `ClaudeClient` detection
- `test_ai_openai.py` (117 lines) — `OpenAIClient` with mocked openai SDK
- `test_project_discovery.py` (645 lines) — 42 tests for Git-style project discovery, config merging, context aggregation, prompt resolution, tags inheritance, setup, and deep merge
- `test_embeddings.py` (354 lines) — `EmbeddingProvider` protocol, `cosine_similarity`, `SemanticScholarEmbeddings`/`LocalSPECTEREmbeddings`/`OpenAIEmbeddings` with mocked backends, `create_embeddings()` factory
- `test_citation_graph.py` (245 lines) — citation graph storage (`save_citation_links`, `get_citation_graph`), intent scoring for reference gaps, `_migrate_schema()` v1→v3
- `test_mcp.py` (404 lines) — `ToolRegistry` CRUD + routing, `ToolInfo`, SPECTER MCP server creation (4 tools), embed/similar/batch tool logic via helpers, `compare_intents()` (LLM vs S2 accuracy, confusion matrix)
- `test_intent_scoring.py` (422 lines) — intent-weighted reference gap scoring, intent coverage matrix, fragment citation intent classification
- `test_interactive_init.py` (347 lines) — interactive `klemma init` wizard, auto-discovery, config generation
- `test_repositories.py` (~130 lines) — repository composition, facade delegation, per-repo CRUD roundtrips, new public methods (`get_existing_source_ids`, `get_sources_without_embeddings`)
- `test_security_hardening.py` — path traversal and download safety tests

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
