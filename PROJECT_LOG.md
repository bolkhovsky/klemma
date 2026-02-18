# Klemma — Project Log

## v0.4.0 — 2026-02-18: UX Simplification + AI Librarian

### What was done

**UX refactoring (10 → 7 commands):**
- Merged `stats` + `coverage` + `gaps` → unified `klemma status` (compact by default, `--verbose` for full tables, `--chapter N` filter)
- Renamed: `morning` → `plan`, `extract` → `process`, `agent` → `ask`, `prepopulate` → `import`
- `klemma process` without args: batch-processes up to 10 pending sources
- Removed `fragments` CLI command (still available in TUI via `f` key)
- All old names preserved as hidden aliases for backward compatibility
- Improved research auto-extract UX with per-source progress messages

**AI Librarian (`klemma library`):**
- New `skills/librarian.py` module (pattern follows planner.py)
- Three modes: `status` (library health), `recommend -s X.X` (section reading plan), `audit` (deep quality check)
- New `prompts/librarian.md` Jinja2 prompt with mode-dependent analysis
- `state.get_library_summary()` — comprehensive aggregation (total, by_status, by_quality, avg_quality, zero_sections, ref_gaps_open)
- `state.get_sources_by_quality()` — sources grouped by quality tier
- `LibraryReport` model in `literature/models.py`
- Reports saved to `Library/Library_{mode}_{date}.md` in vault
- Library summary digest integrated into morning plan context (`klemma plan`)

### Files changed
- `src/klemma/cli.py` — unified status, renamed commands, hidden aliases, library command, batch process, improved research UX
- `src/klemma/skills/librarian.py` — **new**: AI library analysis skill
- `prompts/librarian.md` — **new**: librarian prompt (3 modes)
- `src/klemma/state.py` — `get_library_summary()`, `get_sources_by_quality()`
- `src/klemma/literature/models.py` — `LibraryReport` model
- `src/klemma/skills/planner.py` — library summary in morning plan context
- `prompts/morning.md` — `{{ library_summary }}` section

### Researcher workflow
```
Morning:  klemma plan          → focus, recommendations, deadlines
Work:     klemma research -s X → auto-extract + analysis
New PDF:  klemma process       → batch processing
Review:   klemma library       → library health assessment
Question: klemma ask "..."     → agent with full context
```

### Next objectives
- Real-world test of `klemma library` in all 3 modes
- Consider `klemma library` auto-suggest in `klemma plan` when health score is low
- TUI integration for library reports

---

## v0.3.0 — 2026-02-18: Reference Gap Tracking & Vault Note Auto-Creation

### What was done
- **AI annotation of vault notes**: `annotate.md` prompt analyzes PDF with full library context (174 entries), extracts summary, methodology, relevance, and bibliography cross-references
- **Vault note auto-creation**: `note_factory.py` — `create_vault_note()` generates structured @citekey.md when missing during `klemma extract`
- **Reference gap tracking**: new `reference_gaps` SQLite table tracks missing references found in source bibliographies
  - Score formula: `count × avg_source_quality × section_weight` (2.0 for НР sections)
  - Auto-resolve: `resolve_gaps(entry_lookup)` matches by surname+year when new sources added
  - Supports both Latin and Cyrillic surname matching (BetterBibTeX format)
- **CLI status line**: every `klemma` command prints `| N sources | M fragments | K ref-gaps (top: Author Year ×N)`
- **CLI gaps command**: extended with reference gaps table (Score, Count, Authors, Year, Title, Sections, Why)
- **TUI dashboard**: added "Ref Gaps" StatBox + top-5 gaps panel
- **TUI gaps screen**: added reference gaps DataTable below coverage gaps
- **entry_lookup propagation**: passed through extractor.py, researcher.py, cli.py to note_factory

### Files changed
- `prompts/annotate.md` — new: AI annotation prompt with library_entries and key_references
- `src/klemma/literature/note_factory.py` — new: vault note creation + annotation pipeline
- `src/klemma/state.py` — reference_gaps table, save/get/resolve methods, get_gap_summary
- `src/klemma/cli.py` — status line, gaps table, auto-resolve, entry_lookup propagation
- `src/klemma/skills/extractor.py` — entry_lookup propagation to save_fragments_to_vault
- `src/klemma/skills/researcher.py` — entry_lookup propagation
- `src/klemma/tui/dashboard.py` — Ref Gaps StatBox + gaps panel
- `src/klemma/tui/gaps.py` — reference gaps DataTable

### Next objectives
- Real-world test: `klemma extract <citekey>` with new annotation prompt
- Contextual gap surfacing in `klemma research --section X`
- Morning plan integration: top-3 gaps when score >= 6

---

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
