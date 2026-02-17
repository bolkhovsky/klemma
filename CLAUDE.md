# Klemma — AI Academic Assistant

## What is this
Klemma is a dual-mode CLI/TUI tool for PhD dissertation work. It manages literature (via Zotero), extracts citation fragments from PDFs (via Claude AI), generates daily plans, and tracks dissertation coverage.

## Architecture
- **Dual mode**: `klemma` → Textual TUI dashboard; `klemma morning/extract` → headless CLI
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
├── vault.py        — Obsidian adapter (CLI/file I/O)
├── tui/            — Textual screens (dashboard, fragments)
├── skills/         — AI skills (planner, extractor)
└── literature/     — Zotero, PDF, models
```

## Key commands
- `klemma` — TUI dashboard
- `klemma morning` — daily plan generation
- `klemma extract <citekey>` — extract citation fragments from PDF
- `klemma stats` — processing statistics
- `klemma coverage` — dissertation coverage by chapter/section
- `klemma gaps` — find underserved sections
- `klemma fragments` — browse extracted fragments

## Config
- `config.yaml` — main config (Zotero, Obsidian, AI, dissertation structure)
- Requires: Claude Code CLI (`claude` in PATH), optionally `ZOTERO_API_KEY`

## SQLite tables
- `sources` — Zotero entries with processing status and dissertation metadata
- `fragments` — extracted citation fragments mapped to chapters/sections
- `daily_plans` — generated daily plans
- `reading_queue` — prioritized reading list

## Development
```bash
pip install -e ".[dev]"
klemma --help
```
