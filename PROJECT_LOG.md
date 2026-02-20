# Klemma — Project Log

## v0.5.0 — 2026-02-20: Paper Acquisition + Agent Skills

### What was done

**`klemma acquire` command:**
- Full pipeline: download PDF → create Zotero item (pyzotero) → attach PDF → poll BBT for citekey → register in DB
- Single mode: `klemma acquire <url> --title "..." --authors "..." --year N --section X.X`
- Batch mode: `klemma acquire --batch papers.json` (JSON array with url, title, authors, year, sections, etc.)
- `--no-process` flag to skip fragment extraction
- New `skills/acquirer.py` module: `acquire_paper()`, `download_pdf()`, `poll_bbt_citekey()`, `parse_authors()`
- New `ZoteroLibrary.create_item()` and `ZoteroLibrary.attach_pdf()` write methods
- Requires `ZOTERO_API_KEY` env var and `zotero.library_id` in config.yaml

**Batch `klemma process`:**
- `klemma process key1 key2 key3` — multiple citekeys via `nargs=-1`
- Parallel execution by default with `ThreadPoolExecutor(max_workers=3)`
- `--serial` flag for sequential processing

**Claude Code Agent Skills (`.claude/skills/`):**
- `klemma-acquire/SKILL.md` — full acquire pipeline instructions with batch JSON format
- `klemma-process/SKILL.md` — fragment extraction (single, batch, parallel)
- `klemma-status/SKILL.md` — coverage and gaps check
- Agent (`klemma ask`) auto-discovers Skills instead of reading source code
- `agent.md` simplified: inline instructions replaced with `/klemma-acquire`, `/klemma-process`, `/klemma-status` references

**Library prune improvements (from prior commits):**
- `klemma library prune -c N -v drop|maybe` — browse prune verdicts by chapter/verdict
- Fixed `@` prefix bug in AI-returned citekeys (caused empty prune_verdicts table)
- Drop-verdicted sources excluded from librarian AI context (no repeated recommendations)
- Orphan DB entries cleaned up from pre-existing citekey renames
- Auto-detect Zotero citekey renames via immutable itemKey

### Files changed
- `src/klemma/skills/acquirer.py` — **new**: paper acquisition pipeline
- `src/klemma/literature/zotero.py` — `create_item()`, `attach_pdf()` write methods
- `src/klemma/cli.py` — `acquire` command, batch `process`, `library prune` subcommand
- `prompts/agent.md` — Skills references instead of inline instructions
- `.claude/skills/klemma-{acquire,process,status}/SKILL.md` — **new**: Agent Skills
- `pyproject.toml` — added `requests` dependency
- `.gitignore` — exclude `.claude/settings.local.json`
- `config.yaml` — `zotero.library_id` configured

### Researcher workflow (updated)
```
Morning:  klemma plan             → focus, recommendations, deadlines
Work:     klemma research -s X    → auto-extract + analysis
Find:     klemma ask "find papers on ..." → agent searches, saves to vault
Acquire:  klemma acquire --batch /tmp/papers.json → download + Zotero + DB
Process:  klemma process key1 key2 → parallel fragment extraction
Review:   klemma library           → library health assessment
Check:    klemma status -ch N      → coverage & gaps
Prune:    klemma library prune -c N -v drop → review drop verdicts
```

---

## v0.4.1 — 2026-02-18: Auto-Sync Section Assignments

### What was done
- **Auto-sync vault→DB**: `_sync_sections()` reads all vault @citekey.md frontmatter on every `research`, `library`, `status` command. Updates section assignments when vault differs from DB (~60ms for 138 notes)
- **New Zotero discovery**: auto-detects entries in BetterBibTeX JSON not yet in DB, registers them as `pending` with auto-classified sections (regex patterns from config.yaml)
- **`sync_source_sections()`** in state.py: core DB sync method, compares vault data vs DB, updates in single transaction
- **Removed 15-source hardcap**: `pre_extract_sources()` now accepts `max_sources=50` parameter (was hardcoded 15)
- **Raised `_load_section_sources()` limit**: from 10 to 25 max sources, chapter supplement threshold from 3 to 5
- **Simplified `klemma import`**: delegates to `_sync_sections()`, removed 60 lines of duplicated frontmatter parsing

### Impact
- Section 1.1: 15 → 21 sources visible
- Chapter 1: 15 → 50 sources accessible in research mode (section+chapter combined)
- 46 new Zotero entries auto-discovered and registered as pending

### Files changed
- `src/klemma/state.py` — `_set_sections_inline()`, `sync_source_sections()`
- `src/klemma/cli.py` — `_sync_sections()` orchestrator, wired into 3 commands, simplified `import_vault`
- `src/klemma/skills/researcher.py` — `max_sources=50`, `max_sources=25`, threshold 3→5

---

## v0.4.0 — 2026-02-18: UX Simplification + AI Librarian

### What was done

**UX refactoring (10 → 7 commands):**
- Merged `stats` + `coverage` + `gaps` → unified `klemma status` (compact by default, `--verbose` for full tables, `--chapter N` filter)
- Renamed: `morning` → `plan`, `extract` → `process`, `agent` → `ask`, `prepopulate` → `import`
- `klemma process` without args: batch-processes all pending sources
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
