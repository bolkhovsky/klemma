# Klemma — AI Academic Assistant

## What is this
Klemma is a CLI tool for PhD dissertation work. It manages literature (via Zotero), extracts citation fragments from PDFs (via Claude AI), generates research briefings, daily plans, and tracks dissertation coverage.

## Architecture
- **CLI mode**: `klemma <command>` — all commands are headless CLI
- **Stack**: Python 3.11+, Click, pyzotero, PyMuPDF, MCP, SQLite
- **Pattern**: Config (Pydantic) → State (SQLite) → Skills (AI-powered) → Output (CLI/Obsidian)
- **MCP layer**: ToolRegistry → MCPClient (stdio transport) → external servers (zotero-mcp, academia-mcp)
- **Library abstraction**: LibraryProvider protocol with LocalLibrary (BBT JSON) and MCPLibrary (zotero-mcp) backends
- **AI abstraction**: AIProvider protocol with ClaudeClient (CLI), OpenAIClient, LiteLLMClient backends + `create_ai()` factory
- **Context**: KlemmaContext dataclass created once per CLI command, holds config/state/vault/ai/library/tools

## Project structure
```
src/klemma/
├── cli.py              — Click CLI entry point
├── config.py           — Pydantic config models + get_klemma_home(), load helpers, resolve_prompt()
├── context.py          — KlemmaContext dataclass (single object per CLI command)
├── setup.py            — `klemma init` logic — scaffolds ~/.klemma/ from example files
├── state.py            — SQLite state manager
├── ai.py               — AIProvider protocol + AIProviderBase + ClaudeClient + create_ai() factory
├── ai_openai.py        — OpenAI-compatible backend (OpenAI, Ollama, vLLM, LM Studio)
├── ai_litellm.py       — LiteLLM universal backend (100+ providers)
├── vault.py            — Obsidian adapter (CLI/file I/O, update_section)
├── library_provider.py — LibraryProvider protocol + LocalLibrary + MCPLibrary
├── skills/             — AI skills (planner, extractor, researcher, librarian, agent, acquirer)
├── literature/         — Zotero, PDF, models, note_factory
└── tools/              — MCP tool integration
    ├── client.py       — MCPClient (sync wrapper over async MCP SDK)
    ├── registry.py     — ToolRegistry (server management, lazy client creation)
    └── discovery.py    — Hybrid discovery pipeline (MCP search + Claude assessment)
prompts/                    — Shipped Jinja2 prompt templates (overridable via ~/.klemma/prompts/)
├── morning.md              — daily plans
├── extract.md              — fragment extraction
├── annotate.md             — vault note AI annotation
├── research.md             — research briefing (first run)
├── research_incremental.md — incremental research update
├── librarian.md            — library analysis (3 modes)
└── agent.md                — interactive agent system prompt
config.example.yaml         — Template config for `klemma init`
context.example.md          — Template dissertation context
tags.example.yaml           — Template tag taxonomy
.claude/skills/
├── klemma-acquire/SKILL.md — Agent skill: paper acquisition pipeline
├── klemma-process/SKILL.md — Agent skill: fragment extraction
└── klemma-status/SKILL.md  — Agent skill: coverage & gaps check
```

## Key commands (11)
- `klemma init` — scaffold `~/.klemma/` with config templates (first-time setup)
- `klemma plan` — daily plan generation (library digest included)
- `klemma status` — unified stats + coverage + gaps + ref-gaps (`--verbose`, `--chapter N`)
- `klemma process [<citekeys>...]` — extract fragments from PDF; no arg = batch all pending; parallel by default
- `klemma acquire <url> [--batch file.json]` — download PDF → Zotero → BBT citekey → register in DB
- `klemma research -s 1.3.2` — research briefing for a section (`--enrich` for MCP enrichment)
- `klemma library [-s 2.3] [--audit]` — AI library analysis (status / recommend / audit)
- `klemma ask "query"` — interactive research agent with full dissertation context
- `klemma tools {add,list,remove,call}` — manage MCP servers (zotero, academia, etc.)
- `klemma search "query"` — search papers via MCP (arXiv, Semantic Scholar)
- `klemma discover -s X.X` — hybrid discovery pipeline (`--background`, `--status`, `--review`)

Hidden aliases (backward compat): `morning`→`plan`, `extract`→`process`, `agent`→`ask`, `stats`/`coverage`/`gaps`→`status`, `prepopulate`→`import`

## Config

User data lives in `~/.klemma/` (overridable via `KLEMMA_HOME` env var):
```
~/.klemma/
├── config.yaml    — main config (Zotero, Obsidian, AI, dissertation structure, MCP)
├── context.md     — dissertation context (topic, results, chapters, key terms)
├── tags.yaml      — tag taxonomy for fragment classification (list of strings)
├── prompts/       — optional user overrides for shipped Jinja2 prompt templates
└── data/
    └── klemma.db  — SQLite database
```

Run `klemma init` to scaffold from `config.example.yaml`, `context.example.md`, `tags.example.yaml`.

**Config keys:**
- `zotero.library_json` — path to BetterBibTeX JSON export (for PDF lookup)
- `zotero.backend` — `"local"` (default, BBT JSON) or `"mcp"` (zotero-mcp server)
- `mcp.servers` — registered MCP servers (managed via `klemma tools add/remove`)
- `ai.backend` — `"claude"` (default, CLI), `"openai"` (OpenAI-compatible API), or `"litellm"` (100+ providers)
- `ai.base_url` — endpoint URL for OpenAI-compatible servers (Ollama, vLLM, LM Studio)
- `ai.api_key_env` — env var name for API key (e.g. `"OPENAI_API_KEY"`)
- `ai.json_mode` — enable structured JSON output when backend supports it
- Requires: AI backend (`claude` CLI by default, or `pip install klemma[openai]` / `klemma[litellm]`), optionally `ZOTERO_API_KEY`

**Prompt resolution:** `resolve_prompt(name, klemma_home)` checks `~/.klemma/prompts/<name>` first, then falls back to shipped `prompts/<name>`.

**Fallbacks:** If `context.md` is missing, context is built from `config.dissertation` fields. If `tags.yaml` is missing, tags are extracted from `config.tags.auto_mapping` keys.

## SQLite tables
- `sources` — Zotero entries with processing status and dissertation metadata
- `source_sections` — junction table: source_id × section (multi-section support)
- `fragments` — extracted citation fragments mapped to chapters/sections
- `reference_gaps` — missing references found in source bibliographies (status: open/resolved)
- `discoveries` — papers found by discovery pipeline (MCP search + Claude assessment, status: pending/accepted/rejected)
- `daily_plans` — generated daily plans
- `reading_queue` — prioritized reading list

## Module documentation

Detailed documentation for each subsystem lives in its directory, loaded incrementally as the agent navigates:

- [Core infrastructure](src/klemma/CLAUDE.md) — config, state, AI providers, vault, library, CLI, context
- [AI Skills](src/klemma/skills/CLAUDE.md) — planner, extractor, researcher, librarian, agent, acquirer
- [MCP Tools](src/klemma/tools/CLAUDE.md) — MCPClient, ToolRegistry, discovery pipeline
- [Literature](src/klemma/literature/CLAUDE.md) — Zotero, PDF extraction, models, vault note factory
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

This documentation is a modular knowledge graph — 8 interconnected CLAUDE.md files loaded incrementally as the agent navigates directories. **Keep it up to date when changing code.**

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
