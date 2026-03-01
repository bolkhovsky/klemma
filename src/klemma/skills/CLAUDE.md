# AI Skills

AI-powered features that compose config + state + vault + ai + literature into higher-level operations.
All skills receive dependencies via function arguments (not global state).
Dissertation context and tags are loaded from `~/.klemma/` at CLI startup and passed as parameters.

## Modules

### planner.py (248 lines)
Morning briefing generation. Gathers: deadline, streak, yesterday's plan, chapter plan, library digest, coverage stats, ref-gaps.
- `generate_morning_plan(config, state, vault, ai, dissertation_context, klemma_home)` — full context → `prompts/morning.md` → Claude → `DailyPlan` → state + vault
- `_get_current_deadline()` — calculate days remaining for current chapter (also used by librarian, agent)
- `_read_chapter_plan()` — load session plan from vault
- Intervention types: `NONE`, `REPLAN`, `BOOST`, `SKIP`

### extractor.py (215 lines)
Fragment extraction from PDFs with citation intent classification.
- `extract_fragments(entry, pdf_text, config, state, ai, dissertation_context, available_tags, klemma_home)` — renders `prompts/extract.md` → Claude → `ExtractionResult` (fragments + citation_intent + citation_links)
- `extract_from_citekey()` — full pipeline (find PDF → extract text → analyze → save citation_links to graph)
- `save_fragments_to_vault(citekey, fragments, vault, ..., dissertation_context, available_tags, klemma_home)` — appends to `@citekey.md`; auto-creates note if missing

### researcher.py (771 lines — largest skill)
Section research briefings. Two modes:
- **Initial**: auto-extract fragments → collect context → `prompts/research.md` → `ResearchResult` → `project_root/Research_{section}.md`
- **Incremental**: reads existing research note from project_root `## ✏️ Что нового` (user annotations), computes delta (new sources, fragment count), `prompts/research_incremental.md` → merged result. User notes archived to `## 📋 История изменений` with timestamp.
- `research_section(project_root=...)` — main entry point
- `pre_extract_sources()` — auto-extract fragments for section/chapter sources before research
- `_load_previous_research(section, chapter, state, project_root)` — reads from project_root, parses user notes, history, delta
- `_save_report(section, content, project_root)` — writes report to project_root
- `_load_section_sources()` — enrich sources with vault AI summaries

### librarian.py (522 lines)
Library health analysis. Three modes: `status` (health), `recommend` (section-focused), `audit` (deep quality check + citation graph + prune).
- `analyze_library(project_root=...)` — gathers context → `prompts/librarian.md` → `LibraryReport` → `project_root/Library_{mode}_{date}.md`
- `_gather_library_context()` — summary, quality tiers, ref-gaps, sources compact list, citation graph stats
- `_format_sources_compact()` — compact list for prompt (citekey, author, year, title, q, ch, s, f, intent)
- `_get_citation_graph_stats()` — co-citation analysis, author network, hub scores from `citation_links`
- Prune mode: `prompts/librarian_prune.md` → AI generates drop/maybe verdicts → `state.save_prune_verdicts()`
- `list_prune_verdicts()` / `clear_prune_verdict()` — CLI for browsing/clearing verdicts

### agent.py (~230 lines)
Builds full project context for interactive Claude sessions.
- `build_agent_context(project_root=..., embeddings=?, query=?)` — gathers sources, coverage, gaps, fragments, plan, reading queue + scans project_root for reports/files → optional fragment RAG (embeds query → top-K retrieval) → `prompts/agent.md` → system prompt
- `_scan_project_reports(project_root)` — finds Outline/Research/Library/Agent reports + project files (.md, .tex, .bib, .pdf, .doc, .docx)
- For Claude backend: launches `claude --system-prompt` interactively
- For non-Claude backends: `ai.call()` with full context → terminal output
- Saves responses to `project_root/Agent_<date>.md`

### outliner.py (296 lines)
Project outline generation from directory contents + database context. Two modes:
- **Initial**: scan files → library context → `prompts/outline.md` → Claude → `OutlineResult` → `project_root/Outline_{name}.md`
- **Incremental**: reads previous outline from project_root `## ✏️ Что нового` (user feedback), `prompts/outline_incremental.md` → updated outline. User notes archived to `## 📋 История изменений`.
- `generate_outline(config, state, ai, project_root, project_name, custom_prompt, force_initial)` → `(OutlineResult, mode)`
- `save_outline(result, project_name, project_root)` — writes report with feedback sections to project_root
- `_load_previous_outline(project_name, project_root)` — reads previous outline, extracts user notes and history
- `OutlineResult` dataclass: title, description, chapters, sections, scientific_results, outline_text, update_summary
- CLI options: `-p/--prompt` (custom directive), `--fresh` (force full regeneration)

### acquirer.py (164 lines)
Local-only paper acquisition pipeline: download → local storage → DB.
- `acquire_paper()` — orchestrates full pipeline
- `download_pdf()` — HTTP stream with validation (min 10KB, content-type check)
- `_store_pdf_locally()` — copy to Zotero storage dir
- `poll_bbt_citekey()` — polls BBT JSON export for new citekey (2s intervals, 30s timeout)
- `load_batch()` — parse JSON batch file
- Dataclasses: `PaperMetadata`, `AcquireResult`

### work_context.py (93 lines)
Dynamic work context builder — replaces hardcoded DISSERTATION_CONTEXT constant.
- `build_work_context(project, language)` — generates context string from ProjectConfig fields (title, chapters, deadlines, priority terms); supports any project type (dissertation/paper/thesis)
- `get_current_deadline(project, language)` — returns (deadline_str, days_remaining) for current focus chapter
- Multi-language labels (ru/en) for chapter headings, deadlines, etc.

## Data flows

### Fragment extraction (end-to-end)
`klemma process` → `literature.pdf.PDFExtractor` extracts text → `extractor.extract_fragments()` calls Claude → fragments saved to state → `save_fragments_to_vault()` writes to `@citekey.md`.
If vault note missing: triggers `literature.note_factory.create_vault_note()` first.

### Research briefing
`klemma research -s X.X` → `_sync_sections()` → `researcher.research_section()` → auto-extracts fragments (if needed) → builds context → Claude → `ResearchResult` → `project_root/Research_{section}.md`.

### Paper acquisition
`klemma acquire <url>` → `acquirer.acquire_paper()` → download PDF → local storage → poll BBT for citekey → `state.register_sources()`.

### Library analysis
`klemma library` → `librarian.analyze_library()` → `LibraryReport` → `project_root/Library_{mode}_{date}.md`.

### Project outline
`klemma outline` → `scan_project_files()` → `outliner.generate_outline()` → Claude → `OutlineResult` → `project_root/Outline_{name}.md` (with feedback sections).
On repeat: reads previous outline → incremental update. With `--fresh`: full regeneration. With `-p`: custom AI directive.

### Agent context
`klemma ask "query"` → `agent.build_agent_context(project_root=...)` → scans project_root for outline/reports/files → system prompt → interactive Claude or `ai.call()`. Agent saves to `project_root/Agent_*.md`.

### Agent Skills (Claude Code)
Agent uses Claude Code Skills from `.claude/skills/` instead of reading source code:
- `klemma-acquire` — full acquire pipeline instructions
- `klemma-process` — fragment extraction (single, batch, parallel)
- `klemma-status` — coverage and gaps check

Skills auto-discovered by Claude Code in `--system-prompt` mode. Agent prompt references via `/klemma-acquire`, `/klemma-process`, `/klemma-status`.

## Maintaining this file
Update when: adding a new skill module, changing skill function signatures or data flow steps, adding new Claude Code skills to `.claude/skills/`, or modifying how skills compose with infrastructure. If a new skill uses a prompt template, also update [Prompts](../../../prompts/CLAUDE.md).

See: [Literature](../literature/CLAUDE.md) for PDF extraction and note creation | [Prompts](../../../prompts/CLAUDE.md) for template variables | [Core](../CLAUDE.md) for AI providers and state
