# Klemma — AI Academic Assistant

## What is this
Klemma is a CLI tool for academic writing. It manages literature (via Zotero), extracts citation fragments from PDFs (via Claude AI), generates research briefings, daily plans, and tracks coverage. Supports multiple concurrent projects (dissertation, papers, theses) with separate databases and bibliographies.

## Architecture
- **CLI mode**: `klemma <command>` — all commands are headless CLI
- **Stack**: Python 3.11+, Click, PyMuPDF, SQLite
- **Pattern**: Config (Pydantic) → State (SQLite) → Skills (AI-powered) → Output (CLI/project_root)
- **Report output**: All AI reports (outline, research, library) save to `project_root/` directly. Only `@citekey.md` bibliography notes go to vault `notes_folder`.
- **Library abstraction**: LocalLibrary backend (BBT JSON)
- **AI abstraction**: AIProvider protocol with ClaudeClient (CLI), OpenAIClient, LiteLLMClient backends + `create_ai()` factory
- **Context**: KlemmaContext dataclass created once per CLI command, holds config/state/vault/ai/library/project
- **Multi-project**: Git/NPM-style per-directory projects. `klemma init` in any dir creates `.klemma/` + `KLEMMA.md`. Nested projects inherit shared resources (vault, zotero) from parent. System config in `~/.klemma/`.

## Project structure
```
src/klemma/
├── cli.py              — Click CLI entry point
├── config.py           — Pydantic config models (ProjectConfig, SystemConfig, KlemmaConfig) + discover_project_root(), resolve_effective_config(), resolve_prompt()
├── context.py          — KlemmaContext dataclass (single object per CLI command, includes project_root/project_chain)
├── setup.py            — `klemma init` logic — init_project() (per-dir .klemma/) + init_system() (~/.klemma/)
├── state.py            — SQLite state manager
├── ai.py               — AIProvider protocol + AIProviderBase + ClaudeClient + create_ai() factory
├── ai_openai.py        — OpenAI-compatible backend (OpenAI, Ollama, vLLM, LM Studio)
├── ai_litellm.py       — LiteLLM universal backend (100+ providers)
├── vault.py            — Obsidian adapter (CLI/file I/O, update_section)
├── library_provider.py — LibraryProvider protocol + LocalLibrary (BBT JSON)
├── skills/             — AI skills (planner, extractor, researcher, librarian, agent, acquirer, outliner, work_context)
└── literature/         — PDF extraction, models, note_factory
prompts/                    — Shipped Jinja2 prompt templates (overridable via ~/.klemma/prompts/)
├── morning.md              — daily plans
├── extract.md              — fragment extraction
├── annotate.md             — vault note AI annotation
├── research.md             — research briefing (first run)
├── research_incremental.md — incremental research update
├── librarian.md            — library analysis (3 modes)
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

## Key commands (12)
- `klemma init [--type paper|thesis]` — create project in current directory (.klemma/ + KLEMMA.md)
- `klemma outline [-p "directive"] [--fresh] [--scan-only]` — AI-generate project structure; incremental on repeat, `-p` for custom directive, `--fresh` for full regeneration
- `klemma plan` — daily plan generation (library digest included)
- `klemma status` — unified stats + coverage + gaps + ref-gaps (`--verbose`, `--chapter N`)
- `klemma process [<citekeys>...]` — extract fragments from PDF; no arg = batch all pending; parallel by default
- `klemma acquire <url> [--batch file.json]` — download PDF locally → register in DB
- `klemma research -s 1.3.2` — research briefing for a section
- `klemma library [-s 2.3] [--audit]` — AI library analysis (status / recommend / audit)
- `klemma ask "query"` — interactive research agent with full dissertation context
- `klemma info` — show current project info (root, chain, config, DB)
- `klemma tree` — show nested project tree from current root
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
- Requires: AI backend (`claude` CLI by default, or `pip install klemma[openai]` / `klemma[litellm]`)

**Project discovery:** `discover_project_root()` traverses up from cwd to find nearest `.klemma/` directory (like `git rev-parse --show-toplevel`).

**Config merge order:** system (`~/.klemma/`) < parent project < child project < CLI `--config`.

**Selective inheritance:** Only shared resources (`obsidian`, `zotero`, `ai`) are inherited from parent. Project-specific keys (`project`, `tags`, `state`, `processing`) are NOT inherited.

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
- `sources` — Zotero entries with processing status and dissertation metadata
- `source_sections` — junction table: source_id × section (multi-section support)
- `fragments` — extracted citation fragments mapped to chapters/sections
- `reference_gaps` — missing references found in source bibliographies (status: open/resolved)
- `daily_plans` — generated daily plans
- `reading_queue` — prioritized reading list

## Module documentation

Detailed documentation for each subsystem lives in its directory, loaded incrementally as the agent navigates:

- [Core infrastructure](src/klemma/CLAUDE.md) — config, state, AI providers, vault, library, CLI, context
- [AI Skills](src/klemma/skills/CLAUDE.md) — planner, extractor, researcher, librarian, agent, acquirer, outliner
- [Literature](src/klemma/literature/CLAUDE.md) — PDF extraction, models, vault note factory
- [TUI](src/klemma/tui/CLAUDE.md) — Textual dashboard and screens
- [Prompts](prompts/CLAUDE.md) — Jinja2 templates for AI calls
- [Tests](tests/CLAUDE.md) — testing patterns and conventions

## Development
```bash
pip install -e ".[dev]"
pip install -e ".[openai]"     # OpenAI / Ollama / vLLM / LM Studio backend
pip install -e ".[litellm]"    # LiteLLM universal backend (100+ providers)
pip install -e ".[all-ai]"     # all AI backends
klemma init                    # scaffold ~/.klemma/ with config templates
# edit ~/.klemma/config.yaml, context.md, tags.yaml for your project
klemma --help
```

## Maintaining CLAUDE.md documentation

This documentation is a modular knowledge graph — 7 interconnected CLAUDE.md files loaded incrementally as the agent navigates directories. **Keep it up to date when changing code.**

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
