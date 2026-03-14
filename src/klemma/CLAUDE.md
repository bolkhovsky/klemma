# Core Infrastructure

Foundation layer: config, state, AI providers, vault, library abstraction, CLI entry point, and context.

## Modules

### cli.py (~5950 lines)
Click CLI entry point. Defines 18 commands + hidden aliases.
- `_init_components(config_path)` — creates `KlemmaContext` via Git-style project discovery; attaches parent DB for inheritance when `inherit_db=True` and project chain > 1
- `_resolve_parent_db(parent_root)` — reads parent's `.klemma/config.yaml` to locate its DB path
- `_get_context(ctx)` — returns cached `KlemmaContext` from `ctx.obj` or initializes fresh
- `_init_ai()` — creates AI client (separated for commands that don't need API key)
- `_sync_sections()` — auto-sync vault frontmatter → DB on every `research`/`library`/`status` command
- Commands: `init`, `plan`, `status`, `process`, `embed`, `similar`, `acquire`, `research`, `library`, `library prune`, `library duplicates`, `suggest`, `reassign`, `outline`, `ask`, `info`, `tree`, `benchmark`, `migrate`, `migrate-content` (hidden — moves config.yaml content fields to KLEMMA.md frontmatter), `migrate-library` (dry-run by default; `--run` copies monolithic klemma.db → library.db + project.db via three-tier stores)
- Hidden aliases: `gaps suggest` → `suggest`, `coverage` → `status --verbose`
- Deprecation warnings: bare `klemma gaps` → use `klemma status --verbose`; `klemma library -s` → use `klemma research -s`
- `init --outline` generates an outline after project setup (requires AI backend)
- `init` non-interactive mode: `--name`, `--description`, `--keywords`, `--language` flags auto-skip wizard; `--non-interactive` is alias for `--no-input`
- `embed` group: `embed sources [CITEKEYS]` (default), `embed fragments`, `embed sections` (centroid from source vectors), `embed all` (sources→fragments→sections)
- `_auto_embed_after_process(citekey, state, embeddings, quiet)` — embeds fragments + recomputes section centroids for a just-processed source; called automatically from `_process_single()` unless `--no-embed` is set
- Citekey fast-path dedup (Phase 1C, ADR-014): at start of PDF path in `_process_single()`, checks `user_library.resolve_paper_id(citekey)` before finding/reading PDF — if citekey already in library with fragments, reuses them immediately without PDF read; faster than pdf_hash check for cross-project sharing
- `--no-embed` flag on `process`, `acquire`, and `add` — skips all auto-embedding (source, fragment, section centroid) after processing
- `add` command: unified source ingestion — auto-detects input type (URL/citekey/PDF path) and runs full pipeline: register → section assign → process → embed. Flags: `--section`, `--no-process`, `--no-embed`, `--model`, `--title`/`--authors`/`--year` (URL mode)
- `_detect_input_type(value) -> "url" | "citekey" | "path"` — input detection helper for `add` command
- `--model` override available on: `research`, `ask`, `library`, `process`, `draft -s` — overrides `cfg.ai.model` per invocation
- `draft` group: `draft introduction` (ГОСТ intro), `draft -s X.X` (standalone section draft → `notes/drafts/Draft_{section}.md`), `--no-rag` flag skips per-block RAG retrieval (ablation/debugging)
- `coach` command: methodology-driven research advisor (zero AI calls). Default: project-wide health check. `-s X.X`: section focus. `--json`: structured output. Calls `_sync_sections()` before reading (ADR-010)
- `_coach_section_hint(state, section, project_root)` — generates 1-line hint for inline use; called from `add`, `draft -s`, `research -s`
- `reassign` command: suggest fragment-to-section reassignments via embedding similarity. Optional `CITEKEY` arg filters to one source. `-s SECTION` + `--apply` directly reassigns. Dry-run shows per-suggestion runnable commands. No batch apply — each reassignment is an explicit user decision.

### context.py (47 lines)
`KlemmaContext` dataclass — single object per CLI command invocation.
Holds: `config`, `state`, `vault`, `ai` (optional), `embeddings` (optional), `library` (optional), `project` (optional), `klemma_home`, `dissertation_context`, `available_tags`, `project_root`, `project_chain`, `system_home`, `paper_store` (optional — `LocalPaperStore` at `~/.klemma/library.db`, Phase 1B), `user_library` (optional — `LocalUserLibrary` at same `library.db`, Phase 1C), `project_store` (optional — `LocalProjectStore` at `project/.klemma/data/project.db`, Phase 1C).

### config.py (~800 lines)
Pydantic config models + Git-style project discovery + klemmarc loading.
Key models: `KlemmaConfig`, `ZoteroConfig`, `ObsidianConfig`, `AIConfig` (with `_resolved_api_keys` PrivateAttr), `EmbeddingsConfig`, `SearchConfig` (`backend`, `throttle`), `SuggestConfig` (`max_age_years=10`, `classic_min_score=15.0`), `StateConfig` (`db_path`, `inherit_db`), `DissertationConfig`, `SystemConfig`, `ProjectConfig` (`auto_register: "mapped"|"all"` — filter new sources by chapter_mapping match).
- `KlemmaConfig.library_db_path: Optional[Path]` — override shared library.db location (default: `~/.klemma/library.db`); set in klemmarc.yaml as `library_db_path: /custom/path`; respected by `_init_components()` and `migrate-library`.
- `parse_klemma_md(path)` — split YAML frontmatter from KLEMMA.md body; returns `({}, full_text)` if no frontmatter. Strict: only matches `---` at file start (not mid-file horizontal rules). Integer chapter keys preserved.
- `save_klemma_md(path, frontmatter, body)` — write `---\n{yaml}\n---\n{body}` to KLEMMA.md
- `generate_chapter_mapping(chapters, sections?)` — auto-generate `ChapterMapping` regex patterns from chapter titles (keyword extraction, stopword filtering)
- `_load_klemmarc()` — load `~/.klemmarc.yaml` (or `.yml` / `.klemmarc`) global config
- `_derive_provider(backend, model)` — extract provider name for api_keys lookup (e.g. `litellm` + `anthropic/claude-sonnet` → `"anthropic"`)
- `_check_klemmarc_permissions()` — fix permissions on `~/.klemmarc*` if world-readable
- `discover_project_root(start)` — traverse up from cwd to find nearest `.klemma/`
- `discover_project_chain(start)` — find all project roots child-first, max depth 3
- `resolve_effective_config(project_chain, config_override)` — merge: klemmarc < system < parent < child < CLI override; injects `api_keys` into `AIConfig._resolved_api_keys`; runs `_warn_config_issues()` on each layer. **ADR-013**: reads KLEMMA.md frontmatter for ProjectConfig (priority over config.yaml `project:` section). Emits DeprecationWarning when content fields found in config.yaml without frontmatter.
- `_warn_config_issues(raw, source)` — warns about misplaced keys, unknown keys, bare Claude model shorthands with litellm backend; uses `warnings.warn()` for stderr visibility
- `load_project_context(project_chain, config)` — aggregate KLEMMA.md files parent-first. Strips YAML frontmatter — AI commands see prose body only (not structured config).
- `ensure_system_home()` — auto-create `~/.klemma/` via `init_system()` on first run; checks klemmarc permissions
- `get_system_home()` / `get_klemma_home()` — returns `Path(KLEMMA_HOME)` or `~/.klemma`
- `load_available_tags(klemma_home, config, project_chain?)` — reads `tags.yaml` with parent fallback
- `resolve_prompt(name, klemma_home, project_chain?)` — 4-level: project → parent → system → shipped
- `scan_project_files(project_root, max_chars?)` — scan .md/.tex/.bib/.txt files, returns [{name, path, size, content_preview}]
- `update_project_config(project_root, updates)` — delegates to `_update_via_klemma_md()` if frontmatter exists, else `_update_via_config_yaml()`
- Selective inheritance: only `_INHERITED_KEYS = {"obsidian", "zotero", "ai", "embeddings"}` from parent projects
- Default AI backend: `litellm` (was `claude`)

### setup.py (338 lines)
`klemma init` logic — creates per-directory `.klemma/` projects, `~/.klemma/` system config, and `~/.klemmarc.yaml` global config. Interactive wizard with auto-discovery; non-interactive mode via CLI flags.
- `init_project(project_dir, project_type, values?)` — creates `.klemma/`, `KLEMMA.md` (with YAML frontmatter for content fields), updates `.gitignore`; `config.yaml` has infrastructure only (no `project:` section)
- `migrate_content_to_klemma_md(project_root)` — reads content fields from `config.yaml project:`/`dissertation:`, writes to KLEMMA.md frontmatter, strips content from `config.yaml`; returns `{"migrated_fields": [...], "warnings": [...]}`
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
- `reassign_skips` — persisted skip decisions (legacy, unused since batch --apply removed)

Key methods: `register_sources()`, `update_source_info()`, `get_by_section(section, section_type?)` (JOIN on `source_sections`), `get_coverage_stats()` (includes `section_types` dict), `get_gap_summary()`, `save_plan()`, `save_citation_links()`, `get_citation_graph()`, `save_embedding()`, `get_embeddings()`, `save_section_embedding()`, `get_section_embedding()`, `get_all_section_embeddings()`, `get_section_embedding_stats()`, `save_prune_verdicts()`, `get_prune_verdicts()`, `save_benchmark_run()`, `get_benchmark_runs()`, `compare_benchmark_runs()`, `sync_section_types(config)`.

### hashing.py (25 lines)
Content-addressable hashing utilities for the three-tier library (ADR-014). Pure stdlib, no internal dependencies.
- `compute_pdf_hash(pdf_path)` — SHA256 of PDF bytes for content-addressable paper dedup
- `compute_content_hash(paper_id, text, page)` — SHA256 fragment ID (deterministic: same PDF + extraction = same IDs)
- `compute_prompt_hash(prompt_text)` — first 16 hex chars of SHA256 for extraction prompt versioning

### protocols.py (100 lines)
Protocol interfaces for the three-tier library split (ADR-014). Defines the boundary between tiers.
- `PaperStore` — content-addressable paper storage (Global Corpus): find/register papers, save/get fragments and embeddings
- `UserLibrary` — user's personal collection: citekey→paper_id mapping, source status
- `ProjectStore` — per-project data: section assignments, coverage stats, reference gaps
All three are `@runtime_checkable`. Skills do NOT import this module.

### models.py (60 lines)
Data classes for the three-tier storage layer (ADR-014). Distinct from `literature/models.py` (AI extraction output).
- `PaperRecord` — global corpus paper (paper_id, pdf_hash, doi, title, authors, year, abstract)
- `FragmentRecord` — stored fragment with content-addressable ID (fragment_id = content_hash)
- `UserSource` — user's source entry mapping citekey → global paper_id

### stores/ (ADR-014 Phase 1B–1C)
SQLite backends implementing the three-tier library protocols.

#### stores/paper_store.py (~350 lines)
`LocalPaperStore` — SQLite-backed `PaperStore` at `~/.klemma/library.db`. Content-addressable: same PDF → same paper_id → same fragments (global dedup).
- `__init__(db_path)` — creates parent dirs, runs `_migrate_schema()` (schema version 1)
- `find_paper(*, pdf_hash?, doi?) -> PaperRecord | None` — look up by hash or DOI
- `register_paper(*, title, pdf_hash, ...) -> str` — idempotent: same pdf_hash → same paper_id (UUID)
- `get_fragments(paper_id) -> list[FragmentRecord]` — all stored fragments for a paper
- `save_fragments(paper_id, fragments, prompt_hash, ai_model) -> int` — insert with `INSERT OR IGNORE`, creates `extractions` record
- `get/save_paper_embedding(paper_id, vector, model)` — blob-packed float32 roundtrip
- `get/save_fragment_embedding(fragment_id, vector, model)` — same pattern
- Tables: `papers`, `extractions`, `fragments`, `paper_embeddings`, `fragment_embeddings`, `citation_graph`
- Used by: `_init_components()` in `cli.py` (always created); `_process_single()` in `cli.py` (dedup check + dual-write)

#### stores/user_library.py (~210 lines)
`LocalUserLibrary` — SQLite-backed `UserLibrary` at `~/.klemma/library.db` (same file as `LocalPaperStore`, schema version 2). Maps citekey → paper_id for the User Library tier.
- `add_source(paper_id, citekey, *, status, pdf_path, ...) -> None` — upsert on citekey conflict; replaces chapters/sections
- `get_source_by_citekey(citekey) -> UserSource | None`
- `resolve_paper_id(citekey) -> str | None`
- `get_existing_citekeys() -> set[str]`
- `update_status(citekey, status)`, `get_all_sources()`, `count()`
- Tables: `user_sources`, `user_source_chapters`, `user_source_sections`
- Called from `_process_single()` in `cli.py` to register citekey after successful extraction

#### stores/project_store.py (~310 lines)
`LocalProjectStore` — SQLite-backed `ProjectStore` at `project/.klemma/data/project.db`. Per-project section assignments, coverage stats, and prune verdicts (schema v2).
- `set_source_sections(citekey, paper_id, sections, chapters) -> None` — upsert + replace section assignments
- `get_coverage_stats() -> dict` — `{total_sources, by_section: {section: count}}`
- `get_reference_gaps(**kwargs) -> list[dict]` — returns `[]` (Phase 1D stub)
- `get_source_sections(citekey) -> list[str]`, `get_sources_by_section(section) -> list[str]`
- `register_fragment(fragment_id, *, citekey, section, ...) -> None` — INSERT OR IGNORE
- `count_sources() -> int`
- `save_prune_verdicts(drop, maybe) -> None` — replace all verdicts; skips blank citekeys
- `get_prune_verdicts(verdict?, chapter?, section_type?) -> list[dict]` — filtered; expires after 14 days
- `get_prune_drop_ids(max_age_days?) -> set[str]` — citekeys with 'drop' verdict
- `get_prune_summary() -> dict` — `{drop, maybe, total}` counts
- `clear_prune_verdict(source_id) -> None` — remove single verdict
- Tables: `project_sources`, `project_source_sections`, `project_fragments`, `prune_verdicts` (v2)

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
