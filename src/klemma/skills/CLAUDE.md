# AI Skills

AI-powered features that compose config + state + vault + ai + literature into higher-level operations.
All skills receive dependencies via function arguments (not global state).
Dissertation context and tags are loaded from `~/.klemma/` at CLI startup and passed as parameters.

## Modules

### planner.py (~215 lines)
Morning briefing generation. Gathers: deadline, streak, yesterday's plan, chapter plan, library digest, coverage stats, ref-gaps.
- `generate_morning_plan(config, state, vault, ai, dissertation_context, klemma_home)` — full context → `prompts/morning.md` → Claude → `DailyPlan` → state + vault
- `_get_current_deadline()` — calculate days remaining for current chapter (also used by librarian, agent)
- `_read_chapter_plan()` — load session plan from vault
- Intervention types: `NONE`, `REPLAN`, `BOOST`, `SKIP`

### extractor.py (~205 lines)
Fragment extraction from PDFs.
- `extract_fragments(entry, pdf_text, config, state, ai, dissertation_context, available_tags, klemma_home)` — renders `prompts/extract.md` → Claude → `ExtractionResult`
- `extract_from_citekey()` — full pipeline (find PDF → extract text → analyze)
- `save_fragments_to_vault(citekey, fragments, vault, ..., dissertation_context, available_tags, klemma_home)` — appends to `@citekey.md`; auto-creates note if missing

### researcher.py (730 lines — largest skill)
Section research briefings. Two modes:
- **Initial**: auto-extract fragments → collect context → `prompts/research.md` → `ResearchResult` → vault
- **Incremental**: reads existing research note `## ✏️ Что нового` (user annotations), computes delta (new sources, fragment count), `prompts/research_incremental.md` → merged result. User notes archived to `## 📋 История изменений` with timestamp.
- `research_section()` — main entry point
- `pre_extract_sources()` — auto-extract fragments for section/chapter sources before research
- `_load_previous_research()` — parse user notes, history, delta from existing research note
- `_load_section_sources()` — enrich sources with vault AI summaries

### librarian.py (253 lines)
Library health analysis. Three modes: `status` (health), `recommend` (section-focused), `audit` (deep quality check).
- `analyze_library()` — gathers context → `prompts/librarian.md` → `LibraryReport` → vault
- `_gather_library_context()` — summary, quality tiers, ref-gaps, sources compact list
- `_format_sources_compact()` — compact list for prompt (citekey, author, year, title, q, ch, s, f)
- Prune verdicts (audit mode): saved to DB with "drop" and "maybe" categories

### agent.py (97 lines)
Builds full dissertation context for interactive Claude sessions.
- `build_agent_context()` — gathers sources, coverage, gaps, fragments, plan, reading queue → `prompts/agent.md` → system prompt
- For Claude backend: launches `claude --system-prompt` interactively
- For non-Claude backends: `ai.call()` with full context → terminal output
- Saves responses to `Agent/Agent_<date>.md` in vault

### acquirer.py (258 lines)
Local-only paper acquisition pipeline: download → local storage → DB.
- `acquire_paper()` — orchestrates full pipeline
- `download_pdf()` — HTTP stream with validation (min 10KB, content-type check)
- `_store_pdf_locally()` — copy to Zotero storage dir
- `poll_bbt_citekey()` — polls BBT JSON export for new citekey (2s intervals, 30s timeout)
- `load_batch()` — parse JSON batch file
- Dataclasses: `PaperMetadata`, `AcquireResult`

## Data flows

### Fragment extraction (end-to-end)
`klemma process` → `literature.pdf.PDFExtractor` extracts text → `extractor.extract_fragments()` calls Claude → fragments saved to state → `save_fragments_to_vault()` writes to `@citekey.md`.
If vault note missing: triggers `literature.note_factory.create_vault_note()` first.

### Research briefing
`klemma research -s X.X` → `_sync_sections()` → `researcher.research_section()` → auto-extracts fragments (if needed) → builds context → Claude → `ResearchResult` → vault note.

### Paper acquisition
`klemma acquire <url>` → `acquirer.acquire_paper()` → download PDF → local storage → poll BBT for citekey → `state.register_sources()`.

### Library analysis
`klemma library` → `librarian.analyze_library()` → `LibraryReport` → `Library/Library_{mode}_{date}.md` in vault.

### Agent context
`klemma ask "query"` → `agent.build_agent_context()` → system prompt → interactive Claude or `ai.call()`.

### Agent Skills (Claude Code)
Agent uses Claude Code Skills from `.claude/skills/` instead of reading source code:
- `klemma-acquire` — full acquire pipeline instructions
- `klemma-process` — fragment extraction (single, batch, parallel)
- `klemma-status` — coverage and gaps check

Skills auto-discovered by Claude Code in `--system-prompt` mode. Agent prompt references via `/klemma-acquire`, `/klemma-process`, `/klemma-status`.

## Maintaining this file
Update when: adding a new skill module, changing skill function signatures or data flow steps, adding new Claude Code skills to `.claude/skills/`, or modifying how skills compose with infrastructure. If a new skill uses a prompt template, also update [Prompts](../../../prompts/CLAUDE.md).

See: [Literature](../literature/CLAUDE.md) for PDF extraction and note creation | [Prompts](../../../prompts/CLAUDE.md) for template variables | [Core](../CLAUDE.md) for AI providers and state
