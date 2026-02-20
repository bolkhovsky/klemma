# Tests

## Current test suite
- `test_ai.py` (112 lines) — `extract_json()`, `AIProviderBase`, `create_ai()` factory, `ClaudeClient` detection
- `test_ai_openai.py` (118 lines) — `OpenAIClient` with mocked openai SDK

## Patterns
- pytest + `unittest.mock` (`patch`, `MagicMock`)
- AI backends tested with mocked subprocess (Claude) or mocked SDK (OpenAI)
- Config fixtures use `AIConfig` Pydantic models with test values
- No integration tests (all mocked)

## Running
```bash
pip install -e ".[dev]"
pytest tests/
```

## Adding tests
- Mock external dependencies (AI CLIs, SDKs, Zotero API, MCP servers)
- Mirror source structure: `test_<module>.py` for `src/klemma/<module>.py`

See: [Core infrastructure](../src/klemma/CLAUDE.md) for AI provider architecture
