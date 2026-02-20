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
├── config.py           — Pydantic config models (incl. MCPServerConfig, MCPConfig)
├── context.py          — KlemmaContext dataclass (single object per CLI command)
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
prompts/
├── morning.md              — Jinja2 prompt for daily plans
├── extract.md              — Jinja2 prompt for fragment extraction
├── annotate.md             — Jinja2 prompt for vault note AI annotation
├── research.md             — Jinja2 prompt for research briefing (first run)
├── research_incremental.md — Jinja2 prompt for incremental research update
├── librarian.md            — Jinja2 prompt for library analysis (3 modes)
└── agent.md                — Jinja2 system prompt for interactive agent
.claude/skills/
├── klemma-acquire/SKILL.md — Agent skill: paper acquisition pipeline
├── klemma-process/SKILL.md — Agent skill: fragment extraction
└── klemma-status/SKILL.md  — Agent skill: coverage & gaps check
```

## Key commands (10)
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
- `config.yaml` — main config (Zotero, Obsidian, AI, dissertation structure, MCP servers)
- `zotero.library_json` — path to BetterBibTeX JSON export (for PDF lookup)
- `zotero.backend` — `"local"` (default, BBT JSON) or `"mcp"` (zotero-mcp server)
- `mcp.servers` — registered MCP servers (managed via `klemma tools add/remove`)
- `ai.backend` — `"claude"` (default, CLI), `"openai"` (OpenAI-compatible API), or `"litellm"` (100+ providers)
- `ai.base_url` — endpoint URL for OpenAI-compatible servers (Ollama, vLLM, LM Studio)
- `ai.api_key_env` — env var name for API key (e.g. `"OPENAI_API_KEY"`)
- `ai.json_mode` — enable structured JSON output when backend supports it
- Requires: AI backend (`claude` CLI by default, or `pip install klemma[openai]` / `klemma[litellm]`), optionally `ZOTERO_API_KEY`

## SQLite tables
- `sources` — Zotero entries with processing status and dissertation metadata
- `source_sections` — junction table: source_id × section (multi-section support)
- `fragments` — extracted citation fragments mapped to chapters/sections
- `reference_gaps` — missing references found in source bibliographies (status: open/resolved)
- `discoveries` — papers found by discovery pipeline (MCP search + Claude assessment, status: pending/accepted/rejected)
- `daily_plans` — generated daily plans
- `reading_queue` — prioritized reading list

## Key data flows

### PDF finding (3-tier)
1. `direct_path` from DB
2. BetterBibTeX JSON lookup (`library_json` → citekey → attachment path)
3. Fuzzy filename matching (exact citekey, title words + year, author in prefix)

### Fragment extraction
PDF → PyMuPDF text → AI analysis → fragments to SQLite + vault (`## 💬 Цитаты для диссертации`)

### Vault note creation
`klemma extract <citekey>` → if @citekey.md missing, auto-creates it via `note_factory.create_vault_note()`:
1. AI annotation (`annotate.md` prompt) analyzes PDF with full library context (174 entries)
2. Extracts: summary, methodology, relevance, key_references (bibliography analysis)
3. Renders structured vault note with sections: metadata, summary, methods, relevance, key references, quotes
4. Saves reference gaps (missing refs from bibliography) to `reference_gaps` table

### Reference gap tracking
Each annotated paper's bibliography is cross-checked against our library. Missing relevant refs accumulate across sources.
- **Score formula**: `count × avg_source_quality × section_weight` (section_weight=2.0 for НР1/НР2 sections)
- **Auto-resolve**: when a gap's author+year matches a newly added source, it's marked resolved
- **Surfacing**: CLI status line (every command), `klemma status` (gaps section)

### Research briefing
`klemma research -s X.X` → auto-extract fragments → collect context (draft, fragments, sources, coverage) → Claude analysis → `Research_X.X.md` in vault

Incremental mode: if Research note exists, reads `## ✏️ Что нового` (user notes), computes delta (new sources, fragments), sends incremental prompt. User notes archived to `## 📋 История изменений` with timestamp.

### Paper acquisition
`klemma acquire <url> --title "..." --authors "..." --year N --section X.X` or `klemma acquire --batch papers.json`.
Pipeline: download PDF → pyzotero `create_items` → `create_attachment_record` (metadata only, no cloud upload) → place PDF in `{storage_path}/{attachment_key}/` → poll BBT JSON for citekey → `state.register_sources([citekey])` + `set_pdf_path()` → optional `klemma process`.
PDF storage: bypasses Zotero cloud (paid quota), stores locally in Zotero storage dir. Zotero sees attachment record and finds file locally.
Core logic in `skills/acquirer.py`. Zotero write methods: `ZoteroLibrary.create_item()`, `ZoteroLibrary.create_attachment_record()`.
Requires: `ZOTERO_API_KEY` env var, `zotero.library_id` in config.yaml.

### Agent
`klemma ask "query"` → build_agent_context() gathers all research data (sources, coverage, gaps, fragments, plan) → renders Jinja2 system prompt → launches `claude --system-prompt <context> <query>` interactively. Claude saves response to `Agent/Agent_<date>.md` in vault. For non-Claude backends (no interactive mode): `ai.call()` with full context → prints response to terminal.

### Agent Skills (Claude Code)
Agent uses Claude Code Skills from `.claude/skills/` instead of reading source code:
- `klemma-acquire` — full acquire pipeline instructions (single + batch JSON format)
- `klemma-process` — fragment extraction (single, batch, parallel)
- `klemma-status` — coverage and gaps check

Skills are auto-discovered by Claude Code in `--system-prompt` mode. Agent prompt (`agent.md`) references Skills via `/klemma-acquire`, `/klemma-process`, `/klemma-status`.

### Library analysis
`klemma library` → gather library context (summary, quality tiers, ref-gaps, sources compact list) → Claude analysis via `librarian.md` prompt → structured LibraryReport → saved to `Library/Library_{mode}_{date}.md` in vault. Three modes: status (health), recommend (section-focused), audit (deep quality check).

### MCP tool integration
`klemma tools add <name> --command <cmd> --args <args>` registers an MCP server in `config.yaml → mcp.servers`. ToolRegistry lazily creates MCPClient instances (sync wrapper over async MCP SDK, stdio transport). Each `call_tool()` spawns a fresh connection. Servers are not installed by Klemma — only launch commands are registered.

### Paper search
`klemma search "query"` → ToolRegistry.call("academia", "arxiv_search", ...) → rich table output. Requires registered `academia` MCP server.

### Discovery pipeline
`klemma discover -s X.X` → Phase 1 (deterministic: MCP search per ref-gap + section keywords, deduplicate against library) → Phase 2 (Claude: relevance assessment, usage type, priority) → results saved to `discoveries` table. Can run as background subprocess via `--background`. Review with `--review`.

### Auto-sync sections
On every `research`, `library`, `status` command: `_sync_sections()` → reads all vault @citekey.md frontmatter (~60ms), compares with DB, updates section assignments where vault differs. Also discovers new Zotero entries not yet in DB (auto-classified via config regex patterns, registered as `pending`).

### Multi-section sources
Frontmatter `sections: [1.1, 1.4.1, 3.2.2]` → `source_sections` table → `get_by_section()` uses JOIN to find all relevant sources.

## Development
```bash
pip install -e ".[dev]"
pip install -e ".[openai]"     # OpenAI / Ollama / vLLM / LM Studio backend
pip install -e ".[litellm]"    # LiteLLM universal backend (100+ providers)
pip install -e ".[all-ai]"     # all AI backends
klemma --help
```
