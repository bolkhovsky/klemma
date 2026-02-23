# Klemma — AI Academic Assistant

## What is this
Klemma is a CLI tool for academic writing. It manages literature (via Zotero), extracts citation fragments from PDFs (via Claude AI) with citation intent classification, generates research briefings, daily plans, semantic search via SPECTER embeddings, citation graph analysis, and tracks coverage. Supports multiple concurrent projects (dissertation, papers, theses) with separate databases and bibliographies.

## Architecture
- **CLI mode**: `klemma <command>` — all commands are headless CLI
- **Stack**: Python 3.11+, Click, PyMuPDF, SQLite
- **Pattern**: Config (Pydantic) → State (SQLite) → Skills (AI-powered) → Output (CLI/project_root)
- **Report output**: All AI reports (outline, research, library) save to `project_root/` directly. Only `@citekey.md` bibliography notes go to vault `notes_folder`.
- **Library abstraction**: LocalLibrary backend (BBT JSON)
- **AI abstraction**: AIProvider protocol with ClaudeClient (CLI), OpenAIClient, LiteLLMClient backends + `create_ai()` factory
- **Embeddings**: EmbeddingProvider protocol with SemanticScholar (free S2 API), LocalSPECTER (sentence-transformers), OpenAI backends + `create_embeddings()` factory
- **Context**: KlemmaContext dataclass created once per CLI command, holds config/state/vault/ai/embeddings/library/project
- **Multi-project**: Git/NPM-style per-directory projects. `klemma init` in any dir creates `.klemma/` + `KLEMMA.md`. Nested projects inherit shared resources (vault, zotero) from parent. System config in `~/.klemma/`.

## Project structure
```
src/klemma/
├── cli.py              — Click CLI entry point (2268 lines)
├── config.py           — Pydantic config models + discover_project_root(), resolve_effective_config(), resolve_prompt() (635 lines)
├── context.py          — KlemmaContext dataclass (single object per CLI command) (41 lines)
├── setup.py            — `klemma init` logic — init_project() + init_system() (263 lines)
├── state.py            — SQLite state manager facade (488 lines, delegates to repositories/)
├── ai.py               — AIProvider protocol + ClaudeClient + create_ai() factory (249 lines)
├── ai_openai.py        — OpenAI-compatible backend (105 lines)
├── ai_litellm.py       — LiteLLM universal backend (70 lines)
├── embeddings.py       — EmbeddingProvider protocol + 3 backends + cosine_similarity (257 lines)
├── discovery.py        — Auto-discovery for klemma init (Obsidian vault, Zotero, BBT JSON) (260 lines)
├── vault.py            — Obsidian adapter (CLI/file I/O, update_section) (263 lines)
├── library_provider.py — LibraryProvider protocol + LocalLibrary (BBT JSON) (86 lines)
├── repositories/       — Domain repositories (decomposed from state.py)
│   ├── sources.py      — Source CRUD, status, sections, Zotero keys, vault sync (~400 lines)
│   ├── fragments.py    — Fragment CRUD, intent coverage (~130 lines)
│   ├── embeddings_store.py — Vector BLOB storage (~80 lines)
│   ├── gaps.py         — Reference gaps, coverage, scoring (~330 lines)
│   ├── citations.py    — Citation graph, co-citation, authors (~180 lines)
│   ├── plans.py        — Daily plans, reading queue (~130 lines)
│   └── prune.py        — Prune verdicts, protection (~110 lines)
├── evaluation/         — Benchmark framework (dataset, metrics, runners)
│   ├── dataset.py     — Pydantic schema, load/export (~80 lines)
│   ├── metrics.py     — intent_metrics, precision@K, recall@K, nDCG@K (~120 lines)
│   └── runners.py     — Intent, gap, embedding benchmark runners (~150 lines)
├── skills/             — AI skills (planner, extractor, researcher, librarian, agent, acquirer, outliner, work_context)
├── literature/         — PDF extraction, models, note_factory
└── tools/              — MCP tool infrastructure (567 lines)
    ├── client.py       — MCPClient for stdio transport (129 lines)
    ├── registry.py     — ToolRegistry for multi-server routing (99 lines)
    └── specter_server.py — SPECTER MCP server + citation intent comparison (339 lines)
prompts/                    — Shipped Jinja2 prompt templates (overridable via ~/.klemma/prompts/)
├── morning.md              — daily plans
├── extract.md              — fragment extraction (scaffold prompting + citation intent)
├── annotate.md             — vault note AI annotation (+ citation intent in key_references)
├── research.md             — research briefing (first run)
├── research_incremental.md — incremental research update
├── librarian.md            — library analysis (3 modes)
├── librarian_prune.md      — prune recommendation generation
├── agent.md                — interactive agent system prompt
├── outline.md              — project outline generation
└── outline_incremental.md  — incremental outline update
config.project.example.yaml — Template for per-project .klemma/config.yaml
config.system.example.yaml  — Template for ~/.klemma/config.yaml (system defaults)
config.example.yaml         — Legacy template config (kept for migration)
klemma.example.md           — Template KLEMMA.md (project context)
context.example.md          — Legacy template context (kept for migration)
tags.example.yaml           — Template tag taxonomy
.claude/skills/
├── klemma-acquire/SKILL.md — Agent skill: paper acquisition pipeline
├── klemma-process/SKILL.md — Agent skill: fragment extraction
└── klemma-status/SKILL.md  — Agent skill: coverage & gaps check
```

## Key commands (16)
- `klemma init [--type paper|thesis]` — create project in current directory (.klemma/ + KLEMMA.md)
- `klemma outline [-p "directive"] [--fresh] [--scan-only]` — AI-generate project structure; incremental on repeat, `-p` for custom directive, `--fresh` for full regeneration
- `klemma plan` — daily plan generation (library digest included)
- `klemma status` — unified stats + coverage + gaps + ref-gaps (`--verbose` adds: intent coverage matrix, embedding stats, citation graph stats; `--chapter N` to filter)
- `klemma process [<citekeys>...]` — extract fragments from PDF with citation intent classification; no arg = batch all pending; parallel by default; auto-embeds if embeddings configured
- `klemma embed [<citekey>] [--dry-run] [--backend]` — generate SPECTER/OpenAI embeddings for semantic search; no arg = backfill all completed sources
- `klemma similar <citekey|section> [-k N]` — find semantically similar sources by embedding cosine similarity; section mode shows cross-section recommendations
- `klemma acquire <url> [--batch file.json]` — download PDF locally → register in DB
- `klemma research -s 1.3.2` — research briefing for a section
- `klemma library [-s 2.3] [--audit]` — AI library analysis (status / recommend / audit); audit includes co-citation analysis, author network, prune recommendations
- `klemma library prune [-c N] [-v drop|maybe] [--clear KEY]` — browse/clear prune recommendations from audit
- `klemma ask "query"` — interactive research agent with full dissertation context
- `klemma info` — show current project info (root, chain, config, DB)
- `klemma tree` — show nested project tree from current root
- `klemma benchmark [-d dataset.json] [--metrics all|intent|gaps|embeddings] [--export path] [--json-output]` — evaluation framework: run benchmarks against annotated ground truth; `--export` to generate dataset template from DB
- `klemma migrate [--dry-run]` — migrate from old ~/.klemma/ to per-directory project
- Global options: `--config/-c <path>`

Hidden aliases (backward compat): `morning`→`plan`, `extract`→`process`, `agent`→`ask`, `stats`/`coverage`/`gaps`→`status`, `prepopulate`→`import`

## Config

Two-level configuration: system (global) and project (per-directory).

### System directory (`~/.klemma/`) — global defaults
```
~/.klemma/
├── config.yaml    — AI defaults
└── prompts/       — optional global prompt overrides
```
Created automatically on first `klemma init`. Override location via `KLEMMA_HOME` env var.

### Project directory (`.klemma/` in any dir) — per-project data
```
project_dir/
├── KLEMMA.md          — project context for AI (visible, like CLAUDE.md)
└── .klemma/
    ├── config.yaml    — project config (zotero, obsidian, project)
    ├── tags.yaml      — tag taxonomy for fragment classification
    ├── prompts/       — optional project-level prompt overrides
    └── data/
        └── klemma.db  — SQLite database
```
Created by `klemma init` in any directory. Navigate to project dir and run commands.

**Config keys (project-level):**
- `zotero.library_json` — path to BetterBibTeX JSON export (for PDF lookup)
- `zotero.collection` — optional Zotero collection ID for filtering
- `project:` — ProjectConfig (type, title, chapters, scientific_results, etc.)
- `ai.backend` — `"claude"` (default, CLI), `"openai"` (OpenAI-compatible API), or `"litellm"` (100+ providers)
- `ai.base_url` — endpoint URL for OpenAI-compatible servers (Ollama, vLLM, LM Studio)
- `ai.api_key_env` — env var name for API key (e.g. `"OPENAI_API_KEY"`)
- `ai.json_mode` — enable structured JSON output when backend supports it
- `embeddings.backend` — `"s2"` (Semantic Scholar, free), `"local"` (sentence-transformers), `"openai"`, or `""` (disabled)
- `embeddings.model` — model name (backend-specific, e.g. `"allenai/specter2"`)
- `embeddings.throttle` — seconds between S2 API requests (default: 3.1)
- `embeddings.api_key_env` — env var for API key (OpenAI backend)
- Requires: AI backend (`claude` CLI by default, or `pip install klemma[openai]` / `klemma[litellm]`)
- Optional: `pip install klemma[embeddings]` for semantic search, `pip install klemma[mcp]` for MCP tools

**Project discovery:** `discover_project_root()` traverses up from cwd to find nearest `.klemma/` directory (like `git rev-parse --show-toplevel`).

**Config merge order:** system (`~/.klemma/`) < parent project < child project < CLI `--config`.

**Selective inheritance:** Only shared resources (`obsidian`, `zotero`, `ai`, `embeddings`) are inherited from parent. Project-specific keys (`project`, `tags`, `state`, `processing`) are NOT inherited.

**Prompt resolution:** `resolve_prompt(name, klemma_home, project_chain?)` checks project → parent project → system (`~/.klemma/prompts/`) → shipped prompts.

**Context aggregation:** `load_project_context()` reads `KLEMMA.md` from all project roots in chain (parent first, child last). Falls back to `.klemma/context.md` (legacy) then config fields.

**Tags resolution:** `load_available_tags(klemma_home, config, project_chain?)` checks project → parent project → `config.tags.auto_mapping`.

**Fallbacks:** If `KLEMMA.md` is missing, tries `.klemma/context.md`, then builds from `config.project` fields. If `tags.yaml` is missing, falls back to parent project's tags, then `config.tags.auto_mapping` keys.

### Nested projects
```
thesis_dir/
├── KLEMMA.md           — dissertation context
├── .klemma/            — dissertation project
├── paper_ice/
│   ├── KLEMMA.md       — paper context (AI sees both)
│   └── .klemma/        — paper project (inherits vault/zotero from thesis)
└── paper_climate/
    ├── KLEMMA.md
    └── .klemma/
```
Navigate into `thesis_dir/paper_ice/` and run `klemma status` — it uses the paper's DB but inherits vault/zotero from the thesis parent. AI context includes both dissertation and paper KLEMMA.md files.

### Migration from old `~/.klemma/` setup
Run `klemma migrate` in desired project directory. Splits `~/.klemma/config.yaml` into system (AI) and project (everything else), copies context.md → KLEMMA.md, tags.yaml, DB.

## SQLite tables
Schema versioned via `PRAGMA user_version` (currently v3). Migrations in `state.py:_migrate_schema()`.
- `sources` — Zotero entries with processing status, dissertation metadata, `embedding` BLOB (float32), `embedding_model` TEXT
- `source_sections` — junction table: source_id × section (multi-section support)
- `fragments` — extracted citation fragments mapped to chapters/sections; `citation_intent` (background/method/result_comparison)
- `reference_gaps` — missing references from bibliographies (status: open/resolved); `citation_intent`, intent-weighted scoring
- `citation_links` — citation graph: source_id → target (title_hash for dedup, citation_intent, in_library flag)
- `daily_plans` — generated daily plans
- `reading_queue` — prioritized reading list
- `prune_verdicts` — library audit recommendations (drop/maybe)

## Module documentation

Detailed documentation for each subsystem lives in its directory, loaded incrementally as the agent navigates:

- [Core infrastructure](src/klemma/CLAUDE.md) — config, state, AI providers, vault, library, CLI, context
- [Repositories](src/klemma/repositories/CLAUDE.md) — domain repositories decomposed from StateManager
- [Evaluation](src/klemma/evaluation/CLAUDE.md) — benchmark framework (intent, gap, embedding metrics)
- [AI Skills](src/klemma/skills/CLAUDE.md) — planner, extractor, researcher, librarian, agent, acquirer, outliner
- [Literature](src/klemma/literature/CLAUDE.md) — PDF extraction, models, vault note factory
- [TUI](src/klemma/tui/CLAUDE.md) — Textual dashboard and screens
- [Prompts](prompts/CLAUDE.md) — Jinja2 templates for AI calls
- [Tests](tests/CLAUDE.md) — testing patterns and conventions

## Development
```bash
pip install -e ".[dev]"
pip install -e ".[openai]"           # OpenAI / Ollama / vLLM / LM Studio backend
pip install -e ".[litellm]"          # LiteLLM universal backend (100+ providers)
pip install -e ".[embeddings]"       # semantic search (S2/OpenAI backends)
pip install -e ".[local-embeddings]" # offline SPECTER2 (sentence-transformers)
pip install -e ".[mcp]"              # MCP server support
pip install -e ".[all-ai]"           # all AI backends
klemma init                          # scaffold ~/.klemma/ with config templates
klemma --help
```

## Feature development workflow

Every feature follows this sequence. Do not skip or reorder steps.

1. **Spec** — read the feature description and acceptance criteria in `ROADMAP.md`
2. **Plan** — invoke the `sparc:architect` skill for detailed design; wait for approval before coding
3. **Code** — implement the feature
4. **Verify** — `ruff check src/ tests/` then `python -m pytest tests/ -q`; fix until both pass
5. **Docs** — update all affected `CLAUDE.md` files, `README.md`, and user guide in `docs/`
6. **Commit & PR** — atomic commit, then `gh pr create`. The PR body must include a **Release Note** mini-article (~300 words) with four sections:
   ```
   ## Release Note

   ### Problem
   What gap or limitation this change addresses. Why it matters for the paper/tool.

   ### Academic Foundation
   Which papers from klemma-paper library justify the design decisions.
   Cite specific authors, years, and key findings that informed the approach.

   ### Implementation
   What was built: modules, commands, key design patterns.
   Reference specific files and architectural choices.

   ### Results
   Quantitative outcomes: test counts, LOC, lint status, measurable improvements.
   ```
7. **Paper draft** — export the Release Note into `~/research/klemma-paper/sections/` as a section draft. Russian academic style, `[@citekey]` references, matching existing sections format. File name: `section_N_<topic>.md` where N maps to the paper outline section. Add any missing BibTeX entries to `~/research/klemma-paper/references.bib`.
8. **Cross-check** — on the GitHub PR, run Codex CLI (`codex`) for independent review; iterate until all findings are resolved
9. **Blog note** — write a short TG blog post draft (3-5 sentences, casual tone); do NOT commit this file

## Maintaining CLAUDE.md documentation

This documentation is a modular knowledge graph — 9 interconnected CLAUDE.md files loaded incrementally as the agent navigates directories. **Keep it up to date when changing code.**

### When to update
- **Adding a module**: add entry to the parent directory's CLAUDE.md (module name, line count, purpose, key functions)
- **Adding a CLI command**: update "Key commands" section here + relevant skill/tool CLAUDE.md
- **Adding a SQLite table**: update "SQLite tables" here + `src/klemma/CLAUDE.md` state.py section
- **Adding a prompt template**: update `prompts/CLAUDE.md` (template table + variables) + skill's CLAUDE.md
- **Adding a data flow**: document in the primary owner's CLAUDE.md, add cross-references
- **Renaming/removing a module**: update the CLAUDE.md where it's documented, fix any cross-reference links
- **Changing function signatures or key behavior**: update the relevant module entry

### When to create a new CLAUDE.md
Create a new child CLAUDE.md when a **new subdirectory** is added that contains 2+ modules with shared context. Follow this template:

```markdown
# <Subsystem Name>

<One-line purpose.>

## Modules

### module.py (N lines)
<Purpose.>
- `key_function()` — what it does
- `KeyClass` — what it represents

## Data flows

### <Flow name>
<Step-by-step description of the end-to-end flow.>

## Maintaining this file
Update when modules are added/removed/renamed in this directory, or when key functions/classes change.

See: [links to related CLAUDE.md files]
```

After creating a new child, add a link to the **Module documentation** section above.

### Structure rules
- **Primary owner**: each data flow is documented fully in one CLAUDE.md, other files only link to it
- **Line counts**: listed as `(N lines)` next to module names — update after significant changes
- **Cross-references**: every child ends with `See:` links to related CLAUDE.md files; use relative paths
- **Self-contained**: each child should be understandable without reading the root
- **Concise**: document what an agent needs to navigate and modify code, not exhaustive API docs
