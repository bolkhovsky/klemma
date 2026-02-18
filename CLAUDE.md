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
├── skills/         — AI skills (planner, extractor, researcher)
└── literature/     — Zotero, PDF, models
prompts/
├── morning.md              — Jinja2 prompt for daily plans
├── extract.md              — Jinja2 prompt for fragment extraction
├── research.md             — Jinja2 prompt for research briefing (first run)
└── research_incremental.md — Jinja2 prompt for incremental research update
```

## Key commands
- `klemma` — TUI dashboard
- `klemma morning` — daily plan generation
- `klemma extract <citekey>` — extract citation fragments from PDF, save to DB + vault
- `klemma research -s 1.3.2` — research briefing for a section (auto-extracts fragments)
- `klemma research -s 1.3.2 --force` — re-extract all fragments before analysis
- `klemma stats` — processing statistics
- `klemma coverage` — dissertation coverage by chapter/section
- `klemma gaps` — find underserved sections
- `klemma fragments` — browse extracted fragments
- `klemma prepopulate` — import vault notes into DB (reads sections/chapters lists)

## Config
- `config.yaml` — main config (Zotero, Obsidian, AI, dissertation structure)
- `zotero.library_json` — path to BetterBibTeX JSON export (for PDF lookup)
- Requires: Claude Code CLI (`claude` in PATH), optionally `ZOTERO_API_KEY`

## SQLite tables
- `sources` — Zotero entries with processing status and dissertation metadata
- `source_sections` — junction table: source_id × section (multi-section support)
- `fragments` — extracted citation fragments mapped to chapters/sections
- `daily_plans` — generated daily plans
- `reading_queue` — prioritized reading list

## Key data flows

### PDF finding (3-tier)
1. `direct_path` from DB
2. BetterBibTeX JSON lookup (`library_json` → citekey → attachment path)
3. Fuzzy filename matching (exact citekey, title words + year, author in prefix)

### Fragment extraction
PDF → PyMuPDF text → Claude analysis → fragments to SQLite + vault (`## 💬 Цитаты для диссертации`)

### Research briefing
`klemma research -s X.X` → auto-extract fragments → collect context (draft, fragments, sources, coverage) → Claude analysis → `Research_X.X.md` in vault

Incremental mode: if Research note exists, reads `## ✏️ Что нового` (user notes), computes delta (new sources, fragments), sends incremental prompt. User notes archived to `## 📋 История изменений` with timestamp.

### Multi-section sources
Frontmatter `sections: [1.1, 1.4.1, 3.2.2]` → `source_sections` table → `get_by_section()` uses JOIN to find all relevant sources.

## Development
```bash
pip install -e ".[dev]"
klemma --help
```
