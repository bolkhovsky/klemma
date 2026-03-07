# Core Infrastructure

Foundation layer: config, state, AI providers, vault, library abstraction, CLI entry point, and context.

## Modules

### cli.py (3860 lines)
Click CLI entry point. Defines 17 commands + hidden aliases.
- `_init_components(config_path)` — creates `KlemmaContext` via Git-style project discovery; attaches parent DB for inheritance when `inherit_db=True` and project chain > 1
- `_resolve_parent_db(parent_root)` — reads parent's `.klemma/config.yaml` to locate its DB path
- `_get_context(ctx)` — returns cached `KlemmaContext` from `ctx.obj` or initializes fresh
- `_init_ai()` — creates AI client (separated for commands that don't need API key)
- `_sync_sections()` — auto-sync vault frontmatter → DB on every `research`/`library`/`status` command
- Commands: `init`, `plan`, `status`, `process`, `embed`, `similar`, `acquire`, `research`, `library`, `library prune`, `library duplicates`, `suggest`, `reassign`, `outline`, `ask`, `info`, `tree`, `benchmark`, `migrate`
- Hidden aliases: `gaps suggest` → `suggest`, `coverage` → `status --verbose`
- Deprecation warnings: bare `klemma gaps` → use `klemma status --verbose`; `klemma library -s` → use `klemma research -s`
- `init --outline` generates an outline after project setup (requires AI backend)
- `init` non-interactive mode: `--name`, `--description`, `--keywords`, `--language` flags auto-skip wizard; `--non-interactive` is alias for `--no-input`
- `embed --sections` computes section centroid embeddings from source vectors (mean of assigned source embeddings per section)
- `--model` override available on: `research`, `ask`, `library`, `process`, `draft -s` — overrides `cfg.ai.model` per invocation
- `draft` group: `draft introduction` (ГОСТ intro), `draft -s X.X` (standalone section draft → `notes/drafts/Draft_{section}.md`), `--no-rag` flag skips per-block RAG retrieval (ablation/debugging)

### context.py (41 lines)
`KlemmaContext` dataclass — single object per CLI command invocation.
Holds: `config`, `state`, `vault`, `ai` (optional), `embeddings` (optional), `library` (optional), `project` (optional), `klemma_home`, `dissertation_context`, `available_tags`, `project_root`, `project_chain`, `system_home`.

### config.py (~800 lines)
Pydantic config models + Git-style project discovery + klemmarc loading.
Key models: `KlemmaConfig`, `ZoteroConfig`, `ObsidianConfig`, `AIConfig` (with `_resolved_api_keys` PrivateAttr), `EmbeddingsConfig`, `SearchConfig` (`backend`, `throttle`), `SuggestConfig` (`max_age_years=10`, `classic_min_score=15.0`), `StateConfig` (`db_path`, `inherit_db`), `DissertationConfig`, `SystemConfig`, `ProjectConfig` (`auto_register: "mapped"|"all"` — filter new sources by chapter_mapping match).
- `generate_chapter_mapping(chapters, sections?)` — auto-generate `ChapterMapping` regex patterns from chapter titles (keyword extraction, stopword filtering)
- `_load_klemmarc()` — load `~/.klemmarc.yaml` (or `.yml` / `.klemmarc`) global config
- `_derive_provider(backend, model)` — extract provider name for api_keys lookup (e.g. `litellm` + `anthropic/claude-sonnet` → `"anthropic"`)
- `_check_klemmarc_permissions()` — fix permissions on `~/.klemmarc*` if world-readable
- `discover_project_root(start)` — traverse up from cwd to find nearest `.klemma/`
- `discover_project_chain(start)` — find all project roots child-first, max depth 3
- `resolve_effective_config(project_chain, config_override)` — merge: klemmarc < system < parent < child < CLI override; injects `api_keys` into `AIConfig._resolved_api_keys`; runs `_warn_config_issues()` on each layer
- `_warn_config_issues(raw, source)` — warns about misplaced keys, unknown keys, bare Claude model shorthands with litellm backend; uses `warnings.warn()` for stderr visibility
- `load_project_context(project_chain, config)` — aggregate KLEMMA.md files parent-first
- `ensure_system_home()` — auto-create `~/.klemma/` via `init_system()` on first run; checks klemmarc permissions
- `get_system_home()` / `get_klemma_home()` — returns `Path(KLEMMA_HOME)` or `~/.klemma`
- `load_available_tags(klemma_home, config, project_chain?)` — reads `tags.yaml` with parent fallback
- `resolve_prompt(name, klemma_home, project_chain?)` — 4-level: project → parent → system → shipped
- `scan_project_files(project_root, max_chars?)` — scan .md/.tex/.bib/.txt files, returns [{name, path, size, content_preview}]
- `update_project_config(project_root, updates)` — merge updates into .klemma/config.yaml project section
- Selective inheritance: only `_INHERITED_KEYS = {"obsidian", "zotero", "ai", "embeddings"}` from parent projects
- Default AI backend: `litellm` (was `claude`)

### setup.py (338 lines)
`klemma init` logic — creates per-directory `.klemma/` projects, `~/.klemma/` system config, and `~/.klemmarc.yaml` global config. Interactive wizard with auto-discovery; non-interactive mode via CLI flags.
- `init_project(project_dir, project_type, values?)` — creates `.klemma/`, `KLEMMA.md`, updates `.gitignore`; accepts `InitValues` from wizard or CLI flags
- `init_system(system_home)` — creates `~/.klemmarc.yaml` (0600, with api_keys template) + `~/.klemma/config.yaml` (legacy fallback)
- `init_klemma_home()` — legacy alias for `init_system()`
- Interactive mode: auto-discovers Obsidian vaults, Zotero exports via `discovery.py`

### section_types.py (~240 lines)
Semantic section vocabulary — cross-project labels for dissertation/paper sections.
- `SectionType(str, Enum)` — 12 values: introduction, background, literature_review, theoretical_framework, methodology, data_description, experiments, results, discussion, conclusion, appendix, custom
- `SECTION_TYPE_KEYWORDS` — ru/en keyword lists per type for heuristic matching
- `infer_section_type(chapter_name)` — keyword matching → `SectionType | None`
- `resolve_section_identifier(input, config?)` — parse CLI input: numeric `"2.3"` → `(section, None)`, semantic `"methodology"` → `(section?, SectionType)`
- `WRITING_ORDER_PRIORITY` — dict mapping SectionType → priority (1=write first, 6=write last), based on Kallestinova 2011 results-first order
- `WritingOrderItem` — dataclass: section_id, title, section_type, priority, has_draft
- `get_writing_order(sections, type_map, drafts_dir?)` — compute results-first writing order, detect existing drafts

### state.py (~965 lines)
SQLite state manager — **facade** over 8 domain repositories in `repositories/`. Schema versioned via `PRAGMA user_version` (currently v10), auto-migrates via `_migrate_schema()`. All 70+ public methods delegate to repos; repos accessible via `state.sources`, `state.fragments`, `state.benchmarks`, etc. See [Repositories](repositories/CLAUDE.md).

**DB inheritance (#55):** `set_parent(db_path)` attaches a read-only parent `StateManager`. Nine read methods merge parent data: `get_all_sources`, `get_by_chapter`, `get_by_section`, `get_fragments`, `get_coverage_stats`, `retrieve_similar_fragments`, `get_fragment_embeddings`, `get_all_embeddings`, `get_reference_gaps`. Child wins on key collision. Writes go only to child DB. Controlled by `StateConfig.inherit_db` (default `True`).

**Section type sync (#67):** `sync_section_types(config)` populates `section_type_map` table from config + chapter name inference, then backfills `section_type` columns on `source_sections`, `fragments`, and `reference_gaps`. Called from `_sync_sections()` in CLI.

Tables:
- `sources` — Zotero entries (citekey, title, authors, year, abstract, doi, status, chapter, quality, pdf_path, `embedding` BLOB float32, `embedding_model` TEXT)
- `source_sections` — junction table: source_id × section × `section_type` (multi-section support)
- `fragments` — extracted citation fragments (text, type, chapter, section, `section_type`, relevance, page, `citation_intent`: background/method/result_comparison/extends/contrasts/uses_data, `embedding` BLOB float32, `embedding_model` TEXT, `UNIQUE(source_id, fragment_text)`)
- `reference_gaps` — missing references from bibliographies (status: open/resolved, score, `citation_intent`, `section_type`, intent-weighted scoring)
- `section_type_map` — lookup table: numeric section → semantic type + chapter (populated from config)
- `citation_links` — citation graph: source_id → target (title_hash MD5 for dedup, citation_intent, in_library flag)
- `daily_plans` — generated daily plans
- `reading_queue` — prioritized reading list
- `prune_verdicts` — librarian audit results (drop/maybe with reason)
- `benchmark_runs` — benchmark run history (run_id, timestamp, metrics JSON, paper_citekey, git_commit, klemma_version, config_snapshot, duration)
- `section_embeddings` — section centroid embeddings (section × embedding_model composite PK, BLOB float32, source_count, updated_at)
- `reassign_skips` — persisted skip decisions for `reassign` command (PK: source_id, from_section, to_section; `--fresh` clears)

Key methods: `register_sources()`, `update_source_info()`, `get_by_section(section, section_type?)` (JOIN on `source_sections`), `get_coverage_stats()` (includes `section_types` dict), `get_gap_summary()`, `save_plan()`, `save_citation_links()`, `get_citation_graph()`, `save_embedding()`, `get_embeddings()`, `save_section_embedding()`, `get_section_embedding()`, `get_all_section_embeddings()`, `get_section_embedding_stats()`, `save_prune_verdicts()`, `get_prune_verdicts()`, `save_benchmark_run()`, `get_benchmark_runs()`, `compare_benchmark_runs()`, `sync_section_types(config)`.

### errors.py (32 lines)
Klemma error taxonomy for AI backends.
- `KlemmaAIError` — base class with `retryable` flag and optional `cause` chaining
- `AITimeoutError` (retryable), `AIRateLimitError` (retryable), `AIAuthError` (fatal), `AIResponseError` (fatal)

### ai.py (351 lines)
`AIProvider` protocol → `AIProviderBase` → `ClaudeClient` + `create_ai()` factory.
- `AICallResult` — dataclass with `text`, `duration_ms`, `input_tokens`, `output_tokens`, `retries_used`, `model`, `error`; truthy when `text is not None`
- `AIProvider.call()` / `call_json()` / `call_with_meta()` — main interface for AI calls
- `call_with_meta()` — returns `AICallResult` with timing/tokens/error metadata; base wraps `call()`, backends override with structured error mapping
- `AIProviderBase.render_prompt()` — Jinja2 template rendering
- `extract_json()` — parses JSON from markdown code blocks and unstructured text
- `ClaudeClient` — subprocess wrapper for `claude -p --model <model>` with structured error tracking (timeout, CLI error, FileNotFoundError)

### ai_openai.py (71 lines)
**DEPRECATED** — thin delegation wrapper around `LiteLLMClient`. Emits `DeprecationWarning`, prefixes bare model names with `openai/`, delegates all calls to LiteLLM.

### ai_litellm.py (230 lines)
`LiteLLMClient` — recommended AI backend via `litellm.completion()`. Model format: `provider/model`.
- `_build_kwargs()` — single helper for all completion kwargs (model, tokens, temperature, base_url, api_key, response_format)
- `_is_reasoning_model` — detects o-series/gpt-5 models, switches to `max_completion_tokens`
- `call_json()` — supports structured JSON mode (`response_format`) when `json_mode=True`
- `call_with_meta()` — structured error mapping: `AuthenticationError` (fatal, no retry), `Timeout`/`RateLimitError` (retryable), token extraction from `response.usage`
- `base_url` passthrough for custom endpoints (Ollama, vLLM, etc.)

### vault.py (263 lines)
`VaultAdapter` — Obsidian vault file I/O.
- `read_note()` / `write_note()` — file read/write
- `update_section()` — replace content between markdown heading markers
- `get_properties()` — parse YAML frontmatter
- `list_notes()` — enumerate vault notes

### library_provider.py (86 lines)
`LibraryProvider` protocol with LocalLibrary backend:
- `LocalLibrary` — wraps `PDFExtractor.load_entry_lookup()` (BBT JSON)
- `create_library(config)` — factory, creates LocalLibrary from config

### search.py (266 lines)
Provider-agnostic paper search — resolve reference gaps to acquisition targets.
- `SearchResult` — dataclass: title, authors, year, abstract, doi, pdf_url, source_api
- `SearchProvider` — runtime-checkable protocol: `resolve()`, `resolve_pdf_url()`, `backend_name`
- `S2SearchProvider` — Semantic Scholar (wraps `literature.metadata.lookup_s2()`, rate-limited ~1 req/3s)
- `CrossRefSearchProvider` — CrossRef API (free, no auth, generous rate limits, broader coverage)
- `ChainSearchProvider` — try providers in sequence, first hit wins. Default chain: CrossRef → S2
- `create_search(config)` — factory: `"s2"`, `"crossref"`, `"auto"` (chain), `""` (disabled)

### embeddings.py (268 lines)
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

### tools/ (567 lines)
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
When `auto_register: "mapped"` (default), entries that don't match any `chapter_mapping` pattern are skipped with a visible CLI warning.

### Multi-section sources
Frontmatter `sections: [1.1, 1.4.1, 3.2.2]` → `source_sections` table → `get_by_section()` uses JOIN.

## Maintaining this file
Update when: adding/removing/renaming root-level modules in `src/klemma/`, changing key class/function signatures, adding SQLite tables to `state.py`, modifying `KlemmaContext` fields, or adding new CLI commands to `cli.py`. Line counts should be refreshed after significant changes.

See: [Repositories](repositories/CLAUDE.md) | [Evaluation](evaluation/CLAUDE.md) | [AI Skills](skills/CLAUDE.md) | [Literature](literature/CLAUDE.md) | [TUI](tui/CLAUDE.md) | [Prompts](../../prompts/CLAUDE.md) | [Tests](../../tests/CLAUDE.md) | [Root](../../CLAUDE.md)
