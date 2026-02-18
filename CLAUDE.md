# Klemma — AI Academic Assistant

## What is this
Klemma is a dual-mode CLI/TUI tool for PhD dissertation work. It manages literature (via Zotero), extracts citation fragments from PDFs (via Claude AI), generates research briefings, daily plans, and tracks dissertation coverage.

## Architecture
- **Dual mode**: `klemma` → Textual TUI dashboard; `klemma morning/extract/research` → headless CLI
- **Stack**: Python 3.11+, Click, Textual, Claude Code CLI, pyzotero, PyMuPDF, SQLite
- **Pattern**: Config (Pydantic) → State (SQLite) → Skills (AI-powered) → Output (CLI/TUI/Obsidian)

## Project structure
```
src/klemma/
├── cli.py          — Click CLI entry point
├── app.py          — Textual TUI app
├── config.py       — Pydantic config models
├── state.py        — SQLite state manager
├── ai.py           — Claude Code CLI wrapper (claude -p)
├── vault.py        — Obsidian adapter (CLI/file I/O, update_section)
├── tui/            — Textual screens (dashboard, fragments, coverage, gaps, stats)
├── skills/         — AI skills (planner, extractor, researcher, agent)
└── literature/     — Zotero, PDF, models, note_factory
prompts/
├── morning.md              — Jinja2 prompt for daily plans
├── extract.md              — Jinja2 prompt for fragment extraction
├── annotate.md             — Jinja2 prompt for vault note AI annotation
├── research.md             — Jinja2 prompt for research briefing (first run)
├── research_incremental.md — Jinja2 prompt for incremental research update
└── agent.md                — Jinja2 system prompt for interactive agent
```

## Key commands
- `klemma` — TUI dashboard
- `klemma morning` — daily plan generation
- `klemma extract <citekey>` — extract citation fragments from PDF, save to DB + vault
- `klemma research -s 1.3.2` — research briefing for a section (auto-extracts fragments)
- `klemma research -s 1.3.2 --force` — re-extract all fragments before analysis
- `klemma stats` — processing statistics
- `klemma coverage` — dissertation coverage by chapter/section
- `klemma gaps` — find underserved sections + reference gaps (missing from library)
- `klemma fragments` — browse extracted fragments
- `klemma prepopulate` — import vault notes into DB (reads sections/chapters lists)
- `klemma agent "query"` — interactive research agent with full dissertation context
- `klemma agent -s 1.3.2 "query"` — agent focused on a specific section

## Config
- `config.yaml` — main config (Zotero, Obsidian, AI, dissertation structure)
- `zotero.library_json` — path to BetterBibTeX JSON export (for PDF lookup)
- Requires: Claude Code CLI (`claude` in PATH), optionally `ZOTERO_API_KEY`

## SQLite tables
- `sources` — Zotero entries with processing status and dissertation metadata
- `source_sections` — junction table: source_id × section (multi-section support)
- `fragments` — extracted citation fragments mapped to chapters/sections
- `reference_gaps` — missing references found in source bibliographies (status: open/resolved)
- `daily_plans` — generated daily plans
- `reading_queue` — prioritized reading list

## Key data flows

### PDF finding (3-tier)
1. `direct_path` from DB
2. BetterBibTeX JSON lookup (`library_json` → citekey → attachment path)
3. Fuzzy filename matching (exact citekey, title words + year, author in prefix)

### Fragment extraction
PDF → PyMuPDF text → Claude analysis → fragments to SQLite + vault (`## 💬 Цитаты для диссертации`)

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
- **Surfacing**: CLI status line (every command), `klemma gaps`, TUI dashboard, TUI gaps screen

### Research briefing
`klemma research -s X.X` → auto-extract fragments → collect context (draft, fragments, sources, coverage) → Claude analysis → `Research_X.X.md` in vault

Incremental mode: if Research note exists, reads `## ✏️ Что нового` (user notes), computes delta (new sources, fragments), sends incremental prompt. User notes archived to `## 📋 История изменений` with timestamp.

### Agent
`klemma agent "query"` → build_agent_context() gathers all research data (sources, coverage, gaps, fragments, plan) → renders Jinja2 system prompt → launches `claude --system-prompt <context> <query>` interactively. Claude saves response to `Agent/Agent_<date>.md` in vault.

### Multi-section sources
Frontmatter `sections: [1.1, 1.4.1, 3.2.2]` → `source_sections` table → `get_by_section()` uses JOIN to find all relevant sources.

## Development
```bash
pip install -e ".[dev]"
klemma --help
```
