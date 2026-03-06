# AI Skills

AI-powered features that compose config + state + vault + ai + literature into higher-level operations.
All skills receive dependencies via function arguments (not global state).
Dissertation context and tags are loaded from `~/.klemma/` at CLI startup and passed as parameters.

## Modules

### context_loader.py (230 lines)
Shared context-loading helpers (ADR-008). Extracted from researcher.py for reuse across skills.
- `load_chapter_draft(chapter, config, vault, project?, project_root?)` — project_root first (md > tex > bare), vault fallback
- `extract_section(content, section_id)` — extract section text from markdown by heading number
- `load_section_sources(section, chapter, state, vault)` — enrich sources with vault AI summaries
- `fit_prompt_budget(chapter_draft, sources, fragments, max_chars?)` — progressive prompt reduction
- `validate_citekeys(data, valid_citekeys)` — strip hallucinated citekeys from AI JSON response
- `load_research_report(section, project_root)` — read research report from `notes/research/`

### drafter.py (135 lines)
Section draft generation — general prose from research context.
- `DraftResult` — dataclass: section, chapter, text, word_count, citations_used, filtered_citekeys, research_report_used
- `generate_draft(section, chapter, config, ai, ...)` — pure skill: renders `prompts/section_draft.md` → AI → parse citations → filter hallucinated → `DraftResult`
- `_extract_citations(text)` — regex `[@citekey]` parsing
- `_filter_hallucinated_citations(text, valid_ids)` — remove invalid `[@citekey]` from prose, return cleaned text + removed list

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

### researcher.py (~620 lines)
Section research briefings. Shared helpers extracted to `context_loader.py` — backward-compat aliases `_load_chapter_draft`, `_extract_section`, `_load_section_sources`, `_fit_prompt_budget`, `_validate_citekeys` re-exported via imports. Two modes:
- **Initial**: auto-extract fragments → collect context → `prompts/research.md` → `ResearchResult` → `project_root/Research_{section}.md`
- **Incremental**: reads existing research note from project_root `## ✏️ Что нового` (user annotations), computes delta (new sources, fragment count), `prompts/research_incremental.md` → merged result. User notes archived to `## 📋 История изменений` with timestamp.
- `research_section(project_root=..., embeddings=?)` — main entry point; optional `embeddings` param enables RAG-first fragment retrieval (semantic search via `retrieve_similar_fragments`), falls back to section-based lookup when RAG yields <10 results or is unavailable
- `_fit_prompt_budget(chapter_draft, sources, fragments, max_chars=80K)` — progressively reduces prompt content to fit TPM budget; reduction order: draft→summaries→fragment text→source count→fragment count
- `pre_extract_sources()` — auto-extract fragments for section/chapter sources before research
- `_load_previous_research(section, chapter, state, project_root)` — reads from `notes/research/` first, falls back to project_root for legacy projects
- `_save_report(section, content, project_root)` — writes report to `project_root/notes/research/`
- `_load_section_sources()` — enrich sources with vault AI summaries

### librarian.py (522 lines)
Library health analysis. Three modes: `status` (health), `recommend` (section-focused), `audit` (deep quality check + citation graph + prune).
- `analyze_library(project_root=...)` — gathers context → `prompts/librarian.md` → `LibraryReport` → `project_root/notes/library/Library_{mode}_{date}.md`
- `_gather_library_context()` — summary, quality tiers, ref-gaps, sources compact list, citation graph stats
- `_format_sources_compact()` — compact list for prompt (citekey, author, year, title, q, ch, s, f, intent)
- `_get_citation_graph_stats()` — co-citation analysis, author network, hub scores from `citation_links`
- Prune mode: `prompts/librarian_prune.md` → AI generates drop/maybe verdicts → `state.save_prune_verdicts()`
- `list_prune_verdicts()` / `clear_prune_verdict()` — CLI for browsing/clearing verdicts

### agent.py (~290 lines)
Builds full project context for interactive Claude sessions.
- `build_agent_context(project_root=..., embeddings=?, query=?)` — gathers sources, coverage, gaps, fragments, plan, reading queue + scans project_root for reports/files → optional fragment RAG (embeds query → top-K retrieval) → `prompts/agent.md` → system prompt
- `_scan_project_reports(project_root)` — finds Outline/Research/Library/Agent reports at project root (legacy) + `notes/{research,library,agents}/` (new layout) + project files (.md, .tex, .bib, .pdf, .doc, .docx)
- `update_agents_index(project_root)` — regenerates `notes/AGENTS.md` index from `notes/agents/Agent_*.md` files; parses YAML frontmatter for date/query, writes sorted table (newest first)
- For Claude backend: launches `claude --system-prompt` interactively
- For non-Claude backends: `ai.call()` with full context → terminal output
- Agent saves responses to `project_root/notes/agents/Agent_<date>.md`; `notes/AGENTS.md` auto-updated after session

### outliner.py (296 lines)
Project outline generation from directory contents + database context. Two modes:
- **Initial**: scan files → library context → `prompts/outline.md` → Claude → `OutlineResult` → `project_root/Outline_{name}.md`
- **Incremental**: reads previous outline from project_root `## ✏️ Что нового` (user feedback), `prompts/outline_incremental.md` → updated outline. User notes archived to `## 📋 История изменений`.
- `generate_outline(config, state, ai, project_root, project_name, custom_prompt, force_initial)` → `(OutlineResult, mode)`
- `save_outline(result, project_name, project_root)` — writes report with feedback sections to project_root
- `_load_previous_outline(project_name, project_root)` — reads previous outline, extracts user notes and history
- `OutlineResult` dataclass: title, description, chapters, sections, scientific_results, outline_text, update_summary
- CLI options: `-p/--prompt` (custom directive), `--fresh` (force full regeneration)

### acquirer.py (~240 lines)
Local-only paper acquisition pipeline: download → auto-extract metadata → Zotero → local storage → DB.
- `acquire_paper_local()` — orchestrates full pipeline; calls `resolve_metadata()` after download, then attempts Zotero integration (create item + get BBT citekey), falls back to local citekey generation
- `download_pdf()` — HTTP stream with validation (min 10KB, content-type check)
- `_store_pdf_locally()` — copy to Zotero storage dir (skipped when Zotero has the PDF)
- `_generate_citekey()` — generates `author2024_title_slug` from metadata (fallback when Zotero/BBT unavailable)
- `load_batch()` — parse JSON batch file
- Dataclasses: `PaperMetadata`, `AcquireResult` (with `zotero_added` bool)

### suggester.py (162 lines)
Suggest papers to acquire for filling reference gaps. Pure skill — no file I/O, no CLI.
- `SuggestCandidate` — dataclass: ref_title, ref_authors, ref_year, score, sections, search_result, pdf_url, doi, acquire_cmd
- `suggest_acquisitions(gaps, search, limit, max_age_years, classic_min_score)` → `(list[SuggestCandidate], filtered_count)` — resolve top gaps via SearchProvider, build acquire commands, apply recency filter (skip old papers unless high-score classics)
- `_parse_sections(raw)` — parse DB `dissertation_sections` field (JSON arrays, GROUP_CONCAT joins, plain CSV fallback)

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
`klemma research -s X.X` → `_sync_sections()` → `researcher.research_section()` → auto-extracts fragments (if needed) → builds context → Claude → `ResearchResult` → `project_root/notes/research/Research_{section}.md` (reads legacy `project_root/Research_{section}.md` as fallback).

### Paper acquisition
`klemma acquire <url>` → `acquirer.acquire_paper_local()` → download PDF → `resolve_metadata()` (PDF props + S2 API) → if Zotero running: create item via Connector + get BBT citekey → else: local citekey + local PDF storage → `state.register_sources()` + `state.update_source_info()` (title/authors/year/abstract/doi).

### Library analysis
`klemma library` → `librarian.analyze_library()` → `LibraryReport` → `project_root/notes/library/Library_{mode}_{date}.md`.

### Project outline
`klemma outline` → `scan_project_files()` → `outliner.generate_outline()` → Claude → `OutlineResult` → `project_root/Outline_{name}.md` (with feedback sections).
On repeat: reads previous outline → incremental update. With `--fresh`: full regeneration. With `-p`: custom AI directive.

### Agent context
`klemma ask "query"` → pre-creates `notes/agents/` → `agent.build_agent_context(project_root=...)` → scans project_root for outline/reports/files + `notes/{research,library,agents}/` → system prompt → interactive Claude or `ai.call()`. Agent saves to `project_root/notes/agents/Agent_*.md` → `update_agents_index()` regenerates `notes/AGENTS.md`.

### Section draft generation
`klemma draft -s X.X` → `_sync_sections()` → load research report (`context_loader.load_research_report`) → load chapter draft + extract section → load source summaries → RAG fragments → `fit_prompt_budget()` → `drafter.generate_draft()` → `DraftResult` → `project_root/notes/drafts/Draft_{section}.md`.

### Agent Skills (Claude Code)
Agent uses Claude Code Skills from `.claude/skills/` instead of reading source code:
- `klemma-acquire` — full acquire pipeline instructions
- `klemma-process` — fragment extraction (single, batch, parallel)
- `klemma-status` — coverage and gaps check

Skills auto-discovered by Claude Code in `--system-prompt` mode. Agent prompt references via `/klemma-acquire`, `/klemma-process`, `/klemma-status`.

## Maintaining this file
Update when: adding a new skill module, changing skill function signatures or data flow steps, adding new Claude Code skills to `.claude/skills/`, or modifying how skills compose with infrastructure. If a new skill uses a prompt template, also update [Prompts](../../../prompts/CLAUDE.md).

See: [Literature](../literature/CLAUDE.md) for PDF extraction and note creation | [Prompts](../../../prompts/CLAUDE.md) for template variables | [Core](../CLAUDE.md) for AI providers and state
