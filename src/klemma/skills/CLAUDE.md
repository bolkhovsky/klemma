# AI Skills

AI-powered features that compose config + state + vault + ai + literature into higher-level operations.
All skills receive dependencies via function arguments (not global state).
Dissertation context and tags are loaded from `~/.klemma/` at CLI startup and passed as parameters.

## Modules

### context_loader.py (420 lines)
Shared context-loading helpers (ADR-008). Extracted from researcher.py for reuse across skills.
- `load_chapter_draft(chapter, config, vault, project?, project_root?)` — project_root first (md > tex > bare), vault fallback
- `extract_section(content, section_id)` — extract section text from markdown by heading number
- `load_section_sources(section, chapter, state, vault)` — enrich sources with vault AI summaries
- `fit_prompt_budget(chapter_draft, sources, fragments, max_chars?, rag_fragments?)` — progressive prompt reduction; RAG fragments prioritized over section-level, trimmed last; returns 4-tuple
- `validate_citekeys(data, valid_citekeys)` — strip hallucinated citekeys from AI JSON response
- `parse_argument_blocks(research_text)` — extract argument blocks (order, title, description, citations) from research report markdown `## Структура аргументации` section
- `retrieve_rag_fragments_per_block(blocks, embeddings, state, top_k=5)` — embed each block description, retrieve top-K fragments per block via cosine similarity; deduplicates across blocks; graceful per-block failure handling
- `load_research_report(section, project_root)` — read research report from `notes/research/`
- `extract_previous_section_ending(content, section_id, max_chars=500)` — returns last paragraph of section preceding `section_id` (e.g., `"1.3"` → finds `"1.2"` ending); caps at `max_chars`; returns `""` if not found or first section of chapter
- `load_outline_context(section, project_root)` — reads KLEMMA.md `## Outline` section + frontmatter; returns dict with `section_title`, `current_section_desc`, `current_chapter_desc`, `scientific_contributions`, `title`, `description`; falls back to `Outline_*.md` for backward compat
- `supplement_fragments_from_library(section_fragments, seen_ids, section_sources, paper_store, user_library, section)` — adds library fragments when local count is low; deduplicates by fragment_id **and** fragment_text (cross-store text dedup); per-source try/except so transient DB errors degrade gracefully to local-only fragments; skips sources not in user_library

### drafter.py (145 lines)
Section draft generation — general prose from research context.
- `DraftResult` — dataclass: section, chapter, text, word_count, citations_used, filtered_citekeys, research_report_used
- `generate_draft(section, chapter, config, ai, ..., rag_fragments?, prev_ending="", outline_context=None)` — pure skill: renders `prompts/section_draft.md` → AI → parse citations → filter hallucinated → `DraftResult`; accepts `prev_ending` (last paragraph of previous section) and `outline_context` (dict from `load_outline_context()`) for structured context
- `_extract_citations(text)` — regex `[@citekey]` parsing
- `_filter_hallucinated_citations(text, valid_ids)` — remove invalid `[@citekey]` from prose, return cleaned text + removed list

### planner.py (248 lines)
Morning briefing generation. Gathers: deadline, streak, yesterday's plan, chapter plan, library digest, coverage stats, ref-gaps.
- `generate_morning_plan(config, state, vault, ai, dissertation_context, klemma_home)` — full context → `prompts/morning.md` → Claude → `DailyPlan` → state + vault
- `_get_current_deadline()` — calculate days remaining for current chapter (also used by librarian, agent)
- `_read_chapter_plan()` — load session plan from vault
- Intervention types: `NONE`, `REPLAN`, `BOOST`, `SKIP`

### extractor.py (335 lines)
Fragment extraction from PDFs with citation intent classification.
- `extract_fragments(entry, pdf_text, config, state, ai, dissertation_context, available_tags, klemma_home)` — renders `prompts/extract.md` → Claude → `ExtractionResult` (fragments + citation_intent + citation_links + `downgrade_stats: DowngradeStats`)
- `_validate_verbatim_fragments(fragments, pdf_text, source_id)` — post-AI integrity check: two-stage match (NFKC-normalized exact substring → difflib fuzzy rescue ≥0.95) against the same `pdf_text` the AI saw. Scope-gated on `frag.verbatim=True`; flips failing claims to `False` (paraphrase) instead of dropping them. Returns counts via `DowngradeStats`
- `extract_from_citekey()` — full pipeline (find PDF → extract text → analyze → save citation_links to graph)
- `save_fragments_to_vault(citekey, fragments, vault, ..., dissertation_context, available_tags, klemma_home)` — appends to `@citekey.md`; auto-creates note if missing

### researcher.py (~620 lines)
Section research briefings. Shared helpers extracted to `context_loader.py` — backward-compat aliases `_load_chapter_draft`, `_extract_section`, `_load_section_sources`, `_fit_prompt_budget`, `_validate_citekeys` re-exported via imports. Two modes:
- **Initial**: auto-extract fragments → collect context → `prompts/research.md` → `ResearchResult` → `project_root/Research_{section}.md`
- **Incremental**: reads existing research note from project_root `## ✏️ Что нового` (user annotations), computes delta (new sources, fragment count), `prompts/research_incremental.md` → merged result. User notes archived to `## 📋 История изменений` with timestamp.
- `research_section(project_root=..., embeddings=?, required_citekeys=?)` — main entry point; optional `embeddings` param enables RAG-first fragment retrieval (semantic search via `retrieve_similar_fragments`), falls back to section-based lookup when RAG yields <10 results or is unavailable; optional `required_citekeys` list pins specific citekeys into context regardless of similarity rank
- `_get_required_fragments(required_citekeys, state, section, chapter)` → `(fragments, missing_citekeys)` — fetches top-10 fragments per required citekey for the target section; returns missing list for citekeys with no fragments in that section (caller logs warning)
- `_fit_prompt_budget(chapter_draft, sources, fragments, max_chars=80K)` — progressively reduces prompt content to fit TPM budget; reduction order: draft→summaries→fragment text→source count→fragment count
- `pre_extract_sources()` — auto-extract fragments for section/chapter sources before research
- `_load_previous_research(section, chapter, state, project_root)` — reads from `notes/research/` first, falls back to project_root for legacy projects
- `_save_report(section, content, project_root)` — writes report to `project_root/notes/research/`
- `_load_section_sources()` — enrich sources with vault AI summaries

### librarian.py (522 lines)
Library health analysis. Three modes: `status` (health), `recommend` (section-focused), `audit` (deep quality check + citation graph + prune).
- `analyze_library(project_root=..., project_store=None)` — gathers context → `prompts/librarian.md` → `LibraryReport` → `project_root/notes/library/Library_{mode}_{date}.md`; when `project_store` is provided, drop_ids are merged from both `state` and `project_store` for active-source filtering
- `_gather_library_context(suggest_config?)` — summary, quality tiers, ref-gaps, sources compact list, citation graph stats; optional recency filter via `SuggestConfig` (skip old sources unless high-quality classics)
- `_format_sources_compact()` — compact list for prompt (citekey, author, year, title, q, ch, s, f, intent)
- `_get_citation_graph_stats()` — co-citation analysis, author network, hub scores from `citation_links`
- Prune mode: `prompts/librarian_prune.md` → AI generates drop/maybe verdicts → `project_store.save_prune_verdicts()` when available (with high-quality source protection from `all_sources`), else `state.save_prune_verdicts()`; drop_ids merged from both stores for active-source filtering
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
- **Initial**: scan files → library context → `prompts/outline.md` → Claude → `OutlineResult` → KLEMMA.md (frontmatter + `## Outline` body section)
- **Incremental**: reads previous outline from KLEMMA.md `## Outline` section (fallback: `Outline_*.md`), `prompts/outline_incremental.md` → updated outline.
- `generate_outline(config, state, ai, project_root, project_name, custom_prompt, force_initial)` → `(OutlineResult, mode)`
- `save_outline(result, project_name, project_root)` — **ADR-013**: writes to KLEMMA.md (updates frontmatter chapters/scientific_results + replaces `## Outline` body section); preserves `## Notes` and `## History` sections; falls back to `Outline_*.md` only when KLEMMA.md doesn't exist
- `_load_previous_outline(project_name, project_root)` — reads from KLEMMA.md `## Outline` section first, fallback to `Outline_*.md`
- `OutlineResult` dataclass: title, description, chapters, sections, scientific_results, outline_text, update_summary
- CLI options: `-p/--prompt` (custom directive), `--fresh` (force full regeneration)

### acquirer.py (~260 lines)
Local-only paper acquisition pipeline: download → dedup → auto-extract metadata → Zotero → local storage → DB.
- `acquire_paper_local(meta, storage_path, state?, paper_store?, user_library?)` — orchestrates full pipeline with three dedup gates (ADR-014 Phase 1E):
  1. **DOI pre-check** — if `meta.url` is a DOI and `paper_store.find_paper(doi=...)` hits → returns `ok_library_doi` without downloading; registers citekey in `user_library` and `state`
  2. **Hash dedup** — after download, `compute_pdf_hash()` then `paper_store.find_paper(pdf_hash=...)` hit → skip `resolve_metadata()`; uses library record for citekey generation; registers citekey in `user_library` via **step 6b**
  3. **Write-through** — new paper (no dedup hit) → `resolve_metadata()` → Zotero → `paper_store.register_paper()` → `user_library.add_source()`
  - All three paths call `user_library.add_source(paper_id, citekey, status="completed")` to map citekey → paper_id
  - Returns `AcquireResult` with `status` (`"ok"`, `"ok_library_doi"`, `"download_failed"`, etc.), `citekey`, `pdf_hash`
- `download_pdf()` — HTTP stream with validation (min 10KB, content-type check)
- `_store_pdf_locally()` — copy to Zotero storage dir (skipped when Zotero has the PDF)
- `_generate_citekey()` — generates `author2024_title_slug` from metadata (fallback when Zotero/BBT unavailable)
- `load_batch()` — parse JSON batch file
- Dataclasses: `PaperMetadata`, `AcquireResult` (with `zotero_added` bool, `pdf_hash` str — SHA256 of PDF bytes, ADR-014)

### duplicate_checker.py (120 lines)
Duplicate source detection by metadata. Pure skill — receives source list, returns duplicate pairs.
- `DuplicatePair` — dataclass: citekey_a, citekey_b, strategy, confidence, detail
- `find_duplicates(sources)` — runs 3 strategies, deduplicates pairs, returns sorted by confidence
- Strategies: DOI match (1.0), author+year+title prefix (0.9), title prefix 50 chars (0.7)

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

### insights.py (~480 lines)
Guided Serendipity insights — 3-stage pipeline: generate → suppress → curate.
- `BlindSpot`, `HiddenCluster`, `InsightsResult` — Stage 1 dataclasses (SQL + embeddings, no LLM)
- `RawCandidate`, `CuratedInsight`, `CuratedInsightsResult` — Stage 2-3 dataclasses
- `detect_blind_spots(state, project_store?)` — sections with fewer sources than average (pure SQL)
- `detect_hidden_clusters(state, threshold?, max_pairs?)` — cross-section similar sources (embeddings, no LLM)
- `generate_insights(state, project_store?)` — combined Stage 1 result
- `save_insights_as_decisions(insights, state)` — save raw insights as 2-option decisions
- `suppress_candidates(candidates, state)` — Stage 2: heuristic suppression (already-decided, trivial clusters <0.80, duplicate section pairs, same-chapter redundant blind spots). ~36% reduction per Phansalkar 2013
- `check_insights_blocked(state)` — returns (is_blocked, pending_count, pending_list)
- `curate_insights(candidates, config, ai, state, ...)` — Stage 3: LLM curation via `prompts/insight_curator.md`. Multi-objective ranking (novelty × actionability × trajectory × diversity). Max 5 insights, max 2 per diversity_tag. Injects researcher feedback history.
- `generate_curated_insights(state, config, ai?, ...)` — full pipeline orchestrator. Checks blocking → generates broadly → suppresses → curates (or raw mode with `raw_mode=True`)
- `save_curated_insights_as_decisions(insights, state)` — save curated insights as 3-option decisions (Act/Bookmark/Dismiss)
- Academic grounding: Nadkarni 2025 (LLM-as-judge), Paterno 2009 (tiered alerts), Phansalkar 2013 (suppression-first), McNee 2006 (multi-objective), Si 2024 (diversity enforcement), Hummon & Doreian 1989 (trajectories), Kastrin 2025 (interpretability)

### briefer.py (~200 lines)
Guided Serendipity briefing — analyzes new sources, finds connections, generates branching points.
- `BriefingResult` — dataclass: source info, key_claims, connections, niches, forks
- `generate_briefing(source_citekey, config, state, ai, ...)` — renders `prompts/briefing.md` → AI → parse JSON
- `save_briefing_as_decision(result, state)` — save forks as pending decision

### coach.py (~170 lines)
Contextual research advisor — methodology-driven heuristics (zero AI calls). Thresholds from 21 methodology papers (Pautasso 2013, Cohan 2019, Kallestinova 2011).
- `CoachFinding` — dataclass: category, section, message, severity
- `CoachReport` — dataclass: findings list, optional section focus
- `analyze_section(section, source_count, level, intent_counts, fragment_count, has_draft)` → `list[CoachFinding]` — per-section heuristics: adequacy (Pautasso), intent balance (Cohan), writing readiness (Kallestinova), saturation
- `analyze_project(coverage_stats, intent_coverage, fragment_stats, gap_summary, section_levels, drafts)` → `CoachReport` — project-wide health check: iterates all sections + ref-gap priority
- `coach_section_hint(section, source_count, level, intent_counts, fragment_count, has_draft)` → `str | None` — 1-line hint for inline use in `add`, `draft`, `research`
- Constants: `SOURCE_ADEQUACY_CHAPTER` (15–30), `SOURCE_ADEQUACY_SUBSECTION` (5–10), `INTENT_BALANCE_THRESHOLD` (0.7), `WRITING_READINESS_MIN_SOURCES` (10), `SATURATION_THRESHOLD` (30)

## Data flows

### Fragment extraction (end-to-end)
`klemma process` → `literature.pdf.PDFExtractor` extracts text → `extractor.extract_fragments()` calls Claude → fragments saved to state → `save_fragments_to_vault()` writes to `@citekey.md`.
If vault note missing: triggers `literature.note_factory.create_vault_note()` first.

### Research briefing
`klemma research -s X.X` → `_sync_sections()` → `researcher.research_section()` → auto-extracts fragments (if needed) → builds context → Claude → `ResearchResult` → `project_root/notes/research/Research_{section}.md` (reads legacy `project_root/Research_{section}.md` as fallback).

### Paper acquisition
`klemma acquire <url>` → `acquirer.acquire_paper_local()` → **DOI pre-check** (library hit → skip download, return `ok_library_doi`) → download PDF → **hash dedup** (library hit → skip extraction, use library metadata) → `resolve_metadata()` (PDF props + S2 API) → if Zotero running: create item via Connector + get BBT citekey → else: local citekey + local PDF storage → `paper_store.register_paper()` → `user_library.add_source()` → `state.register_sources()` + `state.update_source_info()`.

### Library analysis
`klemma library` → `librarian.analyze_library()` → `LibraryReport` → `project_root/notes/library/Library_{mode}_{date}.md`.

### Project outline
`klemma outline` → `scan_project_files()` → `outliner.generate_outline()` → Claude → `OutlineResult` → `project_root/Outline_{name}.md` (with feedback sections).
On repeat: reads previous outline → incremental update. With `--fresh`: full regeneration. With `-p`: custom AI directive.

### Agent context
`klemma ask "query"` → pre-creates `notes/agents/` → `agent.build_agent_context(project_root=...)` → scans project_root for outline/reports/files + `notes/{research,library,agents}/` → system prompt → interactive Claude or `ai.call()`. Agent saves to `project_root/notes/agents/Agent_*.md` → `update_agents_index()` regenerates `notes/AGENTS.md`.

### Section draft generation
`klemma draft -s X.X` → `_sync_sections()` → load research report (`context_loader.load_research_report`) → load chapter draft + extract section → load source summaries → **per-block RAG** (parse argument blocks from research report → embed descriptions → retrieve top-K fragments per block via `retrieve_rag_fragments_per_block`) → section-level RAG fallback → `fit_prompt_budget()` (RAG prioritized over section-level) → `extract_previous_section_ending()` (last paragraph of preceding section for continuity) → `load_outline_context()` (chapter/section descriptions + scientific contributions from KLEMMA.md) → `drafter.generate_draft(rag_fragments=..., prev_ending=..., outline_context=...)` → `DraftResult` → `project_root/notes/drafts/Draft_{section}.md`. `--no-rag` flag skips per-block RAG (uses section-level fragments only).

### Agent Skills (Claude Code)
Agent uses Claude Code Skills from `.claude/skills/` instead of reading source code:
- `klemma-acquire` — full acquire pipeline instructions
- `klemma-process` — fragment extraction (single, batch, parallel)
- `klemma-status` — coverage and gaps check

Skills auto-discovered by Claude Code in `--system-prompt` mode. Agent prompt references via `/klemma-acquire`, `/klemma-process`, `/klemma-status`.

## Maintaining this file
Update when: adding a new skill module, changing skill function signatures or data flow steps, adding new Claude Code skills to `.claude/skills/`, or modifying how skills compose with infrastructure. If a new skill uses a prompt template, also update [Prompts](../../../prompts/CLAUDE.md).

See: [Literature](../literature/CLAUDE.md) for PDF extraction and note creation | [Prompts](../../../prompts/CLAUDE.md) for template variables | [Core](../CLAUDE.md) for AI providers and state
