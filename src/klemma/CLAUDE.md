# Core Infrastructure

Foundation layer: config, state, AI providers, vault, library abstraction, CLI entry point, and context.

## Modules

### cli.py (~5950 lines)
Click CLI entry point. Defines 18 commands + hidden aliases.
- `_auto_migrate_to_three_tier(klemma_home, lib_db)` — auto-migration from `_init_components()` when `project.db` is empty but `klemma.db` has sources; backs up `klemma.db` once, migrates sources/fragments/sections; infers chapter from section string (`"1.1"` → `1`)
- `_init_components(config_path)` — creates `KlemmaContext` via Git-style project discovery; triggers `_auto_migrate_to_three_tier()` when needed
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
- `research --require CITEKEY` — pin citekeys into RAG context regardless of similarity rank (repeatable; comma-separated values also accepted, e.g. `--require key1,key2`); pinned fragments prepended before RAG results, never dropped by the 40-fragment cap
- `draft` group: `draft introduction` (ГОСТ intro), `draft -s X.X` (standalone section draft → `notes/drafts/Draft_{section}.md`), `--no-rag` flag skips per-block RAG retrieval (ablation/debugging)
- `coach` command: methodology-driven research advisor (zero AI calls). Default: project-wide health check. `-s X.X`: section focus. `--json`: structured output. Calls `_sync_sections()` before reading (ADR-010)
- `_coach_section_hint(state, section, project_root)` — generates 1-line hint for inline use; called from `add`, `draft -s`, `research -s`
- `reassign` command: suggest fragment-to-section reassignments via embedding similarity. Optional `CITEKEY` arg filters to one source. `-s SECTION` + `--apply` directly reassigns. Dry-run shows per-suggestion runnable commands. No batch apply — each reassignment is an explicit user decision.
- `insights` command: 3-stage pipeline (generate → suppress → curate) for blind spots and hidden clusters. Default: LLM-curated top 3-5. `--raw`: unfiltered (no AI). `--model`: override AI model. Blocking: refuses to generate new if pending insight decisions exist.
- `decisions` group: `list` (default), `show <ID>`, `trail`, `note <ID> "text"` (research note), `like <ID>` (useful feedback), `dislike <ID>` (not useful feedback). `decide <ID> A|B|C` (top-level command).
- `briefing` command: AI briefing for a source. `--pending`: process top unbriefed sources. `--model`: override.

### context.py (47 lines)
`KlemmaContext` dataclass — single object per CLI command invocation.
Holds: `config`, `state`, `vault`, `ai` (optional), `embeddings` (optional), `library` (optional), `project` (optional), `klemma_home`, `dissertation_context`, `available_tags`, `project_root`, `project_chain`, `system_home`, `paper_store` (optional — `LocalPaperStore` at `~/.klemma/library.db`, Phase 1B), `user_library` (optional — `LocalUserLibrary` at same `library.db`, Phase 1C), `project_store` (optional — `LocalProjectStore` at `project/.klemma/data/project.db`, Phase 1C).

### config.py (~800 lines)
Pydantic config models + Git-style project discovery + klemmarc loading.
Key models: `KlemmaConfig`, `ZoteroConfig`, `ObsidianConfig`, `AIConfig` (with `_resolved_api_keys` PrivateAttr), `EmbeddingsConfig`, `SearchConfig` (`backend`, `throttle`), `SuggestConfig` (`max_age_years=10`, `classic_min_score=15.0`), `StateConfig` (`db_path`), `DissertationConfig`, `SystemConfig`, `ProjectConfig` (`auto_register: "mapped"|"all"` — filter new sources by chapter_mapping match).
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
- `init_project(project_dir, project_type, values?)` — creates `.klemma/`, including pre-created `.klemma/notes/` and `.klemma/pdfs/` directories (ADR-016 default-local layout), `KLEMMA.md` (with YAML frontmatter for content fields), updates `.gitignore`; `config.yaml` has infrastructure only (no `project:` section)
- `migrate_content_to_klemma_md(project_root)` — reads content fields from `config.yaml project:`/`dissertation:`, writes to KLEMMA.md frontmatter, strips content from `config.yaml`; returns `{"migrated_fields": [...], "warnings": [...]}`
- `init_system(system_home)` — creates `~/.klemmarc.yaml` (0600, with api_keys template) + `~/.klemma/config.yaml` (legacy fallback)
- `init_klemma_home()` — legacy alias for `init_system()`
- Interactive mode: auto-discovers Zotero exports via `discovery.py`. The wizard **no longer prompts for Obsidian** — it's an opt-in override added to `config.yaml` by hand (ADR-016). `discover_obsidian_vault()` still ships for programmatic callers.
- `_build_project_config()` skips the `obsidian:` section unless `values.vault_path` is explicitly set — fresh local-mode projects have a clean `config.yaml`

### section_types.py (~290 lines)
Semantic section vocabulary — cross-project labels for dissertation/paper sections.
- `SectionType(str, Enum)` — 12 values: introduction, background, literature_review, theoretical_framework, methodology, data_description, experiments, results, discussion, conclusion, appendix, custom
- `SECTION_TYPE_KEYWORDS` — ru/en keyword lists per type for heuristic matching
- `infer_section_type(chapter_name)` — keyword matching → `SectionType | None`
- `resolve_section_identifier(input, config?)` — parse CLI input: numeric `"2.3"` → `(section, None)`, semantic `"methodology"` → `(section?, SectionType)`
- `WRITING_ORDER_PRIORITY` — dict mapping SectionType → priority (1=write first, 6=write last), based on Kallestinova 2011 results-first order
- `WritingOrderItem` — dataclass: section_id, title, section_type, priority, has_draft
- `get_writing_order(sections, type_map, drafts_dir?)` — compute results-first writing order, detect existing drafts
- `INTENT_TO_SECTION_TYPES` — dict mapping citation_intent → list[SectionType] for auto-assignment (shared between curation.py and tasks.py)
- `auto_assign_section(intent, outline, ai_predicted_section?)` — AI-prediction-first section assignment: uses AI's per-fragment section prediction first, falls back to intent→SectionType mapping

### state.py (~965 lines)
SQLite state manager — **facade** over 8 domain repositories in `repositories/`. Schema versioned via `PRAGMA user_version` (currently v14), auto-migrates via `_migrate_schema()`. All 70+ public methods delegate to repos; repos accessible via `state.sources`, `state.fragments`, `state.benchmarks`, etc. See [Repositories](repositories/CLAUDE.md).

**Three-tier library (ADR-014):** Shared `library.db` replaces the old parent-child DB inheritance (#55). Papers and fragments are stored globally in `~/.klemma/library.db` via `LocalPaperStore` + `LocalUserLibrary`; per-project data (sections, gaps, plans) lives in `project/.klemma/data/project.db` via `LocalProjectStore`. StateManager is a pure facade over domain repositories with no cross-DB merging.

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
- `decisions` — Guided Serendipity branching points (trigger_type, trigger_source, context_json, options_json, chosen_option, rationale, sections, influenced_by, `note` TEXT for research notes, `feedback` TEXT for like/dislike retrospective feedback)

Key methods: `register_sources()`, `update_source_info()`, `get_by_section(section, section_type?)` (JOIN on `source_sections`), `get_coverage_stats()` (includes `section_types` dict), `get_gap_summary()`, `save_plan()`, `save_citation_links()`, `get_citation_graph()`, `save_embedding()`, `get_embeddings()`, `save_section_embedding()`, `get_section_embedding()`, `get_all_section_embeddings()`, `get_section_embedding_stats()`, `save_prune_verdicts()`, `get_prune_verdicts()`, `save_benchmark_run()`, `get_benchmark_runs()`, `compare_benchmark_runs()`, `sync_section_types(config)`.

### hashing.py (25 lines)
Content-addressable hashing utilities for the three-tier library (ADR-014). Pure stdlib, no internal dependencies.
- `compute_pdf_hash(pdf_path)` — SHA256 of PDF bytes for content-addressable paper dedup
- `compute_content_hash(paper_id, text, page)` — SHA256 fragment ID (deterministic: same PDF + extraction = same IDs)
- `compute_prompt_hash(prompt_text)` — first 16 hex chars of SHA256 for extraction prompt versioning

### protocols.py (141 lines)
Protocol interfaces for the three-tier library split (ADR-014) and file storage (ADR-009). Defines the boundary between tiers.
- `PaperStore` — content-addressable paper storage (Global Corpus): find/register papers, save/get fragments and embeddings
- `UserLibrary` — user's personal collection: citekey→paper_id mapping, source status
- `ProjectStore` — per-project data: section assignments, coverage stats, reference gaps
- `FileStore` — pluggable file storage: `save(paper_id, data, filename) -> str`, `read`, `exists`, `delete`, `get_path`. Local impl uses filesystem; SaaS can swap to S3-compatible storage.
All four are `@runtime_checkable`. Skills do NOT import this module.

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
- `get_paper_by_id(paper_id) -> PaperRecord | None` — look up by paper_id
- `register_paper(*, title, pdf_hash, ...) -> str` — idempotent: same pdf_hash → same paper_id (UUID)
- `get_fragments(paper_id) -> list[FragmentRecord]` — all stored fragments for a paper
- `save_fragments(paper_id, fragments, prompt_hash, ai_model) -> int` — insert with `INSERT OR IGNORE`, creates `extractions` record
- `get/save_paper_embedding(paper_id, vector, model)` — blob-packed float32 roundtrip
- `get/save_fragment_embedding(fragment_id, vector, model)` — same pattern
- Tables: `papers`, `extractions`, `fragments`, `paper_embeddings`, `fragment_embeddings`, `citation_graph`
- Used by: `_init_components()` in `cli.py` (always created); `_process_single()` in `cli.py` (dedup check + dual-write)

#### stores/user_library.py (~460 lines)
`LocalUserLibrary` — SQLite-backed `UserLibrary` at `~/.klemma/library.db` (same file as `LocalPaperStore`, schema version 5). Maps citekey → paper_id for the User Library tier. Composite PK `(user_id, citekey)` for multi-user SaaS isolation (v5).
- `add_source(paper_id, citekey, *, status, pdf_path, ...) -> None` — upsert on citekey conflict; replaces chapters/sections
- `get_source_by_citekey(citekey) -> UserSource | None`
- `resolve_paper_id(citekey) -> str | None`
- `get_existing_citekeys() -> set[str]`
- `remove_source(citekey) -> bool` — delete from user library (keeps global corpus)
- `get_project_citekeys(project_id, user_id?) -> set[str]` — strictly project-attached citekeys (excludes unassigned)
- `update_status(citekey, status)`, `get_all_sources()`, `count()`
- Tables: `user_sources`, `user_source_chapters`, `user_source_sections`
- Called from `_process_single()` in `cli.py` to register citekey after successful extraction

#### stores/file_store.py (~80 lines)
`LocalFileStore` — filesystem-backed `FileStore` (ADR-009). Default location: `~/.klemma/files/`.
- `__init__(base_dir)` — creates directory, stores resolved `base_dir`
- `_file_path(paper_id, filename) -> Path` — content-addressed path `{base_dir}/{prefix}/{paper_id}/{filename}`; validates both args (rejects `..`, `/`, `\\`, empty) and confirms resolved path stays inside `base_dir`
- `save/read/exists/delete/get_path` — CRUD operations; `delete` cleans up empty parent dir
- `get_paper_dir(paper_id)` / `delete_paper_files(paper_id)` — bulk paper-level operations

#### stores/project_store.py (~310 lines)
`LocalProjectStore` — SQLite-backed `ProjectStore` at `project/.klemma/data/project.db`. Per-project section assignments, coverage stats, and prune verdicts (schema v5). Composite PK `(user_id, citekey)` for multi-user SaaS isolation.
- `_uid(user_id) -> str` — normalizes `None` → `""` for composite PK compatibility
- `set_source_sections(citekey, paper_id, sections, chapters, user_id=None) -> None` — upsert + replace section assignments; user-scoped
- `get_coverage_stats(user_id=None) -> dict` — `{total_sources, by_section: {section: count}}`; user-scoped
- `get_reference_gaps(**kwargs) -> list[dict]` — returns `[]` (Phase 1D stub)
- `get_source_sections(citekey, user_id=None) -> list[str]`, `get_sources_by_section(section, user_id=None) -> list[str]` — user-scoped
- `register_fragment(fragment_id, *, citekey, section, ...) -> None` — INSERT OR IGNORE
- `count_sources(user_id=None) -> int` — user-scoped
- `save_prune_verdicts(drop, maybe, user_id=None) -> None` — replace user's verdicts; skips blank citekeys
- `get_prune_verdicts(verdict?, chapter?, section_type?, user_id=None) -> list[dict]` — filtered; expires after 14 days; user-scoped
- `get_prune_drop_ids(max_age_days?, user_id=None) -> set[str]` — citekeys with 'drop' verdict; user-scoped
- `get_prune_summary(user_id=None) -> dict` — `{drop, maybe, total}` counts; user-scoped
- `clear_prune_verdict(source_id, user_id=None) -> None` — remove single verdict; user-scoped
- Tables: `project_sources` (PK: user_id, citekey), `project_source_sections` (PK: user_id, citekey, section), `project_fragments`, `prune_verdicts` (PK: user_id, source_id) (v5)

#### stores/user_store.py (~370 lines)
`LocalUserStore` — SQLite-backed `UserStore` at `~/.klemma/users.db` (separate from library.db). User accounts, projects, and fragment curation for the SaaS auth layer (ADR-009).
- `__init__(db_path)` — creates dirs, runs `_migrate_schema()` (schema version 12)
- `create_user(email, password_hash, name?) -> UserRecord` — normalizes email to lowercase; raises `ValueError` on duplicate
- `get_user_by_email(email) -> UserRecord | None` — normalizes email to lowercase before lookup
- `get_user_by_id(user_id) -> UserRecord | None`
- `update_email_verified(user_id) -> None`
- `save_refresh_token(user_id, token_hash, expires_at) -> None`
- `get_refresh_token(token_hash) -> dict | None` — returns `{user_id, expires_at}` or None
- `delete_refresh_token(token_hash) -> None` — token rotation on use
- `delete_all_refresh_tokens(user_id) -> None` — logout-all
- `curate_fragments(project_id, decisions) -> int` — batch INSERT OR REPLACE curation decisions
- `get_curated(project_id, *, verdict?, section?, citekey?) -> list[dict]` — filtered curation query
- `get_curation_stats(project_id, citekey) -> dict` — {curated, accepted, rejected, suggested}
- `get_curated_fragment_ids(project_id) -> set[str]` — user-decided IDs only (accepted/rejected); excludes suggested
- `update_curation(project_id, fragment_id, *, verdict?, assigned_section?, note?) -> bool` — partial update
- Tables: `users`, `refresh_tokens`, `projects`, `fragment_curation` (project_id FK, fragment_id, citekey, verdict CHECK('accepted','rejected','suggested'), assigned_section, note)

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
`VaultAdapter` — file I/O for annotated `@<citekey>.md` notes. Since ADR-016 the adapter is re-rooted at the *resolved notes directory*, not the Obsidian vault root — `resolve_notes_root()` is the single source of truth for where notes land.
- `resolve_notes_root(config, project_root) -> Path` — module-level helper: returns `Path(vault_path).expanduser() / notes_folder` when `config.obsidian.vault_path` is set (non-whitespace), else `project_root / ".klemma" / "notes"`. `notes_folder=""` joins to the vault root (flat-vault edge case).
- `read_note()` / `write_note()` — file read/write
- `update_section()` — replace content between markdown heading markers
- `get_properties()` — parse YAML frontmatter
- `list_notes()` — enumerate notes in the adapter root
- **Scope narrowing for existing Obsidian users**: `get_tags()`, `search()`, `_find_daily_dir()` used to scan the whole vault. They now scan only the notes directory (the adapter root). Matches how klemma actually uses these helpers (citekey-note operations + tag inventory of `@*.md`), but downstream scripts relying on whole-vault scans must move files into the notes folder or use a separate tool.

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

### embeddings.py (393 lines)
`EmbeddingProvider` runtime-checkable protocol + 4 backends + utilities.
- `EmbeddingProvider` protocol — `dim`, `model_name`, `embed(title, abstract) → list[float] | None`
- `embed_batch(texts) → list[list[float] | None]` — optional method on backends (LiteLLM implements it as a single batched HTTP call); fallback helper `_default_embed_batch(provider, texts)` loops `embed()` for backends without native batching
- `SemanticScholarEmbeddings` — free S2 API (768-dim SPECTER), rate-limited (throttle param)
- `LocalSPECTEREmbeddings` — offline sentence-transformers SPECTER2 model
- `OpenAIEmbeddings` — text-embedding-3-small (1536-dim)
- `LiteLLMEmbeddings` — any `provider/model` supported by LiteLLM (Ollama/BGE-M3 recommended default, also Voyage, Cohere, Mistral, OpenAI). Auto-detects `dim` on first call; `model_name` stored as `"model-provider"` for uniqueness across providers. `api_base` passed through to `litellm.embedding()` for Ollama endpoints
- `create_embeddings(config)` — factory, returns provider or None if disabled; supports `backend: "litellm"` with `model`, `base_url`, `api_key_env`, `timeout`, `dim`
- `_derive_embedding_provider(model)` — local helper mirroring `config._derive_provider` to avoid circular import
- `cosine_similarity(a, b)` — dot-product cosine similarity for float vectors

The `embed sources|fragments|all` commands accept `--remodel` to re-embed rows whose `embedding_model` no longer matches the active provider (migration path for changing backends).

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

### api/ (SaaS backend — ADR-009)
FastAPI application. Install extra: `pip install "klemma[api]"`. Entry point: `uvicorn klemma.api.app:create_app --factory`.
- `api/app.py` — `create_app()` factory, lifespan hooks, router mounting
- `api/routes/health.py` — `GET /health` (status/version/service — no auth)
- `api/routes/auth.py` — `POST /auth/register`, `POST /auth/login`, `POST /auth/refresh`, `GET /auth/me`
- `api/auth/` — JWT (python-jose), argon2 passwords, Pydantic schemas, FastAPI deps
See [api/CLAUDE.md](api/CLAUDE.md) and [api/auth/CLAUDE.md](api/auth/CLAUDE.md) for full detail.

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

See: [Repositories](repositories/CLAUDE.md) | [Evaluation](evaluation/CLAUDE.md) | [AI Skills](skills/CLAUDE.md) | [Literature](literature/CLAUDE.md) | [TUI](tui/CLAUDE.md) | [API](api/CLAUDE.md) | [Prompts](../../prompts/CLAUDE.md) | [Tests](../../tests/CLAUDE.md) | [Root](../../CLAUDE.md)
