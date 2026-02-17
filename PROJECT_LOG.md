# Klemma — Project Log

## v0.1.0 — 2026-02-17: Initial Implementation

### What was done
- Created project from plan: dual-mode CLI/TUI academic assistant
- Absorbed zobsidian-processor patterns (state tracker, PDF extraction, models)
- Rewrote Claude integration: anthropic SDK replaces CLI subprocess calls
- Added pyzotero for live Zotero API access (replaces JSON export)
- Added Obsidian vault adapter with CLI/file I/O fallback
- Implemented fragment extraction pipeline (PDF → Claude → SQLite)
- Implemented morning planning with coverage-aware context
- Built Textual TUI with dashboard and fragment browser screens
- Unified SQLite schema: sources, fragments, daily_plans, reading_queue

### Tech stack
- Python 3.11+, Click 8.1.8, Textual 1.0.0, Rich 13.9.4
- anthropic 0.49.0, pyzotero 1.10.0, PyMuPDF 1.25.3
- Pydantic 2.10.6, PyYAML 6.0.2, Jinja2 3.1.5
- SQLite (WAL mode, foreign keys)

### Structure
See CLAUDE.md for full project structure.

### Next objectives
- Iteration 2: Absorb remaining zobsidian-processor commands (process, sources, quote, export)
- Reading queue with snippet delivery
- TUI reader and sources screens
- ChromaDB semantic search (Iteration 3)
- Telegram bot delivery (Iteration 3)
