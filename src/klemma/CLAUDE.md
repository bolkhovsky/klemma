# Core Infrastructure

Foundation layer: config, state, AI providers, vault, library abstraction, CLI entry point, and context.

## Modules

### cli.py (1480 lines)
Click CLI entry point. Defines all 11 commands + hidden aliases.
- `_init_components()` — creates `KlemmaContext` from config
- `_init_ai()` — creates AI client (separated for commands that don't need API key)
- `_sync_sections()` — auto-sync vault frontmatter → DB on every `research`/`library`/`status` command

### context.py (30 lines)
`KlemmaContext` dataclass — single object per CLI command invocation.
Holds: `config`, `state`, `vault`, `ai` (optional), `library` (optional), `tools` (optional).

### config.py (164 lines)
Pydantic config models loaded from `config.yaml`.
Key models: `KlemmaConfig`, `ZoteroConfig`, `ObsidianConfig`, `AIConfig`, `DissertationConfig`, `MCPConfig`, `MCPServerConfig`.

### state.py (1207 lines)
SQLite state manager. Tables:
- `sources` — Zotero entries (citekey, title, status, chapter, quality, pdf_path)
- `source_sections` — junction table: source_id × section (multi-section support)
- `fragments` — extracted citation fragments (text, type, chapter, section, relevance, page)
- `reference_gaps` — missing references from bibliographies (status: open/resolved, score)
- `discoveries` — papers from discovery pipeline (status: pending/accepted/rejected)
- `daily_plans` — generated daily plans
- `reading_queue` — prioritized reading list
- `prune_verdicts` — librarian audit results

Key methods: `register_sources()`, `get_by_section()` (JOIN on `source_sections`), `get_coverage_stats()`, `get_gap_summary()`, `save_discovery()`, `save_plan()`.

### ai.py (243 lines)
`AIProvider` protocol → `AIProviderBase` → `ClaudeClient` + `create_ai()` factory.
- `AIProvider.call()` / `call_json()` — main interface for AI calls
- `AIProviderBase.render_prompt()` — Jinja2 template rendering
- `extract_json()` — parses JSON from markdown code blocks and unstructured text
- `ClaudeClient` — subprocess wrapper for `claude -p --model <model>`
- Retry logic with configurable timeout

### ai_openai.py (103 lines)
`OpenAIClient` — wraps OpenAI Python SDK. Supports structured JSON mode (`response_format`).
Works with: OpenAI, Ollama, vLLM, LM Studio (via `base_url`).

### ai_litellm.py (67 lines)
`LiteLLMClient` — thin wrapper around `litellm.completion()`. Model format: `provider/model`.

### vault.py (250 lines)
`VaultAdapter` — Obsidian vault file I/O.
- `read_note()` / `write_note()` — file read/write
- `update_section()` — replace content between markdown heading markers
- `get_properties()` — parse YAML frontmatter
- `list_notes()` — enumerate vault notes

### library_provider.py (195 lines)
`LibraryProvider` protocol with two backends:
- `LocalLibrary` — wraps `PDFExtractor.load_entry_lookup()` (BBT JSON)
- `MCPLibrary` — uses zotero-mcp server via MCP protocol
- `create_library(config)` — factory, selects based on `config.zotero.backend`

### app.py (124 lines)
`KlemmaApp` — Textual TUI application. Mounts screens from `tui/` package.

## Data flows

### Auto-sync sections
Triggered on every `research`, `library`, `status` command from `cli.py._sync_sections()`.
Reads all vault `@citekey.md` frontmatter (~60ms), compares with DB, updates section assignments.
Also discovers new Zotero entries not in DB (auto-classified via config regex patterns, registered as `pending`).

### Multi-section sources
Frontmatter `sections: [1.1, 1.4.1, 3.2.2]` → `source_sections` table → `get_by_section()` uses JOIN.

## Maintaining this file
Update when: adding/removing/renaming root-level modules in `src/klemma/`, changing key class/function signatures, adding SQLite tables to `state.py`, modifying `KlemmaContext` fields, or adding new CLI commands to `cli.py`. Line counts should be refreshed after significant changes.

See: [AI Skills](skills/CLAUDE.md) | [MCP Tools](tools/CLAUDE.md) | [Literature](literature/CLAUDE.md) | [TUI](tui/CLAUDE.md) | [Prompts](../../prompts/CLAUDE.md) | [Tests](../../tests/CLAUDE.md)
