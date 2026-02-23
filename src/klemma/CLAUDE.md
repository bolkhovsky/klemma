# Core Infrastructure

Foundation layer: config, state, AI providers, vault, library abstraction, CLI entry point, and context.

## Modules

### cli.py (2274 lines)
Click CLI entry point. Defines 14 commands + hidden aliases.
- `_init_components(config_path)` — creates `KlemmaContext` via Git-style project discovery
- `_get_context(ctx)` — returns cached `KlemmaContext` from `ctx.obj` or initializes fresh
- `_init_ai()` — creates AI client (separated for commands that don't need API key)
- `_sync_sections()` — auto-sync vault frontmatter → DB on every `research`/`library`/`status` command
- Commands: `init`, `plan`, `status`, `process`, `embed`, `similar`, `acquire`, `research`, `library`, `outline`, `ask`, `info`, `tree`, `migrate`

### context.py (41 lines)
`KlemmaContext` dataclass — single object per CLI command invocation.
Holds: `config`, `state`, `vault`, `ai` (optional), `embeddings` (optional), `library` (optional), `project` (optional), `klemma_home`, `dissertation_context`, `available_tags`, `project_root`, `project_chain`, `system_home`.

### config.py (635 lines)
Pydantic config models + Git-style project discovery.
Key models: `KlemmaConfig`, `ZoteroConfig`, `ObsidianConfig`, `AIConfig`, `EmbeddingsConfig`, `DissertationConfig`, `SystemConfig`, `ProjectConfig`.
- `discover_project_root(start)` — traverse up from cwd to find nearest `.klemma/`
- `discover_project_chain(start)` — find all project roots child-first, max depth 3
- `resolve_effective_config(project_chain, config_override)` — merge: system < parent < child < CLI override
- `load_project_context(project_chain, config)` — aggregate KLEMMA.md files parent-first
- `ensure_system_home()` — auto-create `~/.klemma/` via `init_system()` on first run
- `get_system_home()` / `get_klemma_home()` — returns `Path(KLEMMA_HOME)` or `~/.klemma`
- `load_available_tags(klemma_home, config, project_chain?)` — reads `tags.yaml` with parent fallback
- `resolve_prompt(name, klemma_home, project_chain?)` — 4-level: project → parent → system → shipped
- `scan_project_files(project_root, max_chars?)` — scan .md/.tex/.bib/.txt files, returns [{name, path, size, content_preview}]
- `update_project_config(project_root, updates)` — merge updates into .klemma/config.yaml project section
- Selective inheritance: only `_INHERITED_KEYS = {"obsidian", "zotero", "ai", "embeddings"}` from parent projects

### setup.py (263 lines)
`klemma init` logic — creates per-directory `.klemma/` projects and `~/.klemma/` system config. Interactive wizard with auto-discovery.
- `init_project(project_dir, project_type)` — creates `.klemma/`, `KLEMMA.md`, updates `.gitignore`
- `init_system(system_home)` — creates `~/.klemma/config.yaml` (AI defaults only)
- `init_klemma_home()` — legacy alias for `init_system()`
- Interactive mode: auto-discovers Obsidian vaults, Zotero exports via `discovery.py`

### state.py (420 lines)
SQLite state manager — **facade** over 7 domain repositories in `repositories/`. Schema versioned via `PRAGMA user_version` (currently v3), auto-migrates via `_migrate_schema()`. All 58 public methods delegate to repos; repos accessible via `state.sources`, `state.fragments`, etc. See [Repositories](repositories/CLAUDE.md).

Tables:
- `sources` — Zotero entries (citekey, title, status, chapter, quality, pdf_path, `embedding` BLOB float32, `embedding_model` TEXT)
- `source_sections` — junction table: source_id × section (multi-section support)
- `fragments` — extracted citation fragments (text, type, chapter, section, relevance, page, `citation_intent`: background/method/result_comparison)
- `reference_gaps` — missing references from bibliographies (status: open/resolved, score, `citation_intent`, intent-weighted scoring)
- `citation_links` — citation graph: source_id → target (title_hash MD5 for dedup, citation_intent, in_library flag)
- `daily_plans` — generated daily plans
- `reading_queue` — prioritized reading list
- `prune_verdicts` — librarian audit results (drop/maybe with reason)

Key methods: `register_sources()`, `get_by_section()` (JOIN on `source_sections`), `get_coverage_stats()`, `get_gap_summary()`, `save_plan()`, `save_citation_links()`, `get_citation_graph()`, `save_embedding()`, `get_embeddings()`, `save_prune_verdicts()`, `get_prune_verdicts()`.

### ai.py (249 lines)
`AIProvider` protocol → `AIProviderBase` → `ClaudeClient` + `create_ai()` factory.
- `AIProvider.call()` / `call_json()` — main interface for AI calls
- `AIProviderBase.render_prompt()` — Jinja2 template rendering
- `extract_json()` — parses JSON from markdown code blocks and unstructured text
- `ClaudeClient` — subprocess wrapper for `claude -p --model <model>`
- Retry logic with configurable timeout

### ai_openai.py (105 lines)
`OpenAIClient` — wraps OpenAI Python SDK. Supports structured JSON mode (`response_format`).
Works with: OpenAI, Ollama, vLLM, LM Studio (via `base_url`).

### ai_litellm.py (70 lines)
`LiteLLMClient` — thin wrapper around `litellm.completion()`. Model format: `provider/model`.

### vault.py (249 lines)
`VaultAdapter` — Obsidian vault file I/O.
- `read_note()` / `write_note()` — file read/write
- `update_section()` — replace content between markdown heading markers
- `get_properties()` — parse YAML frontmatter
- `list_notes()` — enumerate vault notes

### library_provider.py (86 lines)
`LibraryProvider` protocol with LocalLibrary backend:
- `LocalLibrary` — wraps `PDFExtractor.load_entry_lookup()` (BBT JSON)
- `create_library(config)` — factory, creates LocalLibrary from config

### embeddings.py (257 lines)
`EmbeddingProvider` runtime-checkable protocol + 3 backends + utilities.
- `EmbeddingProvider` protocol — `dim`, `model_name`, `embed(title, abstract) → list[float] | None`
- `SemanticScholarEmbeddings` — free S2 API (768-dim SPECTER), rate-limited (throttle param)
- `LocalSPECTEREmbeddings` — offline sentence-transformers SPECTER2 model
- `OpenAIEmbeddings` — text-embedding-3-small (1536-dim)
- `create_embeddings(config)` — factory, returns provider or None if disabled
- `cosine_similarity(a, b)` — dot-product cosine similarity for float vectors

### discovery.py (260 lines)
Auto-discovery for `klemma init` interactive wizard.
- `discover_obsidian_vaults()` — find Obsidian vaults on disk
- `discover_zotero_exports()` — find BetterBibTeX JSON exports
- `discover_bbt_json()` — locate BBT auto-export JSON files
- Used by `setup.py` for interactive project initialization

### tools/ (577 lines)
MCP tool infrastructure for embedding and citation analysis.
- `client.py` (129) — `MCPClient` for stdio transport connection to MCP servers
- `registry.py` (99) — `ToolRegistry` for multi-server tool routing + `ToolInfo` dataclass
- `specter_server.py` (339) — FastMCP server with 4 tools: `embed_paper`, `find_similar`, `batch_embed`, `get_citation_intents`; also `fetch_citation_intents()` S2 API wrapper and `compare_intents()` for LLM vs S2 intent validation
- `__main__.py` (5) — CLI entry: `python -m klemma.tools [--local]`

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

See: [AI Skills](skills/CLAUDE.md) | [Literature](literature/CLAUDE.md) | [TUI](tui/CLAUDE.md) | [Prompts](../../prompts/CLAUDE.md) | [Tests](../../tests/CLAUDE.md) | [Root](../../CLAUDE.md)
