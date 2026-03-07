# TITDS-XV-2025 Dogfood Log

Real-world benchmark of klemma for conference paper writing. Sub-project inheriting dissertation library (378 sources, 2135 fragments).

## Issues

### 001. AI config not inherited from parent project
**Severity**: High (blocks every sub-project)
**When**: `klemma init` in sub-project, then `klemma status`
**What**: Child project gets default `backend: litellm` + `model: sonnet` even though parent dissertation has `backend: claude, model: opus`. Config inheritance should follow: system (~/.klemma) < parent project < child project. Child should only need to override what's different, not repeat the full AI config.
**Warning**: `ai.model='sonnet' is a Claude shorthand but backend is litellm`
**Expected**: No AI config in child = inherit from parent. Only explicit overrides in child config.
**Root cause**: Two bugs:
1. `klemma init` writes default AI + embeddings sections into every new project config, even when parent exists
2. `_warn_config_issues()` fires on raw child config BEFORE merging with parent — false positive. After merge, parent's `backend: claude` would override, but the warning sees only the child's bare `model: sonnet` with default `litellm`
**Workaround**: Manually edited child config — removed `model`, `embeddings` keys. Only override `ai.language: en`.
**Fix needed (code)**:
- `klemma init`: detect parent project, skip `ai:/embeddings:` defaults when parent provides them
- `_warn_config_issues()`: only run on effective merged config, not per-layer raw YAML
- Also: init wrote `type: dissertation` instead of `type: paper` — should infer from context or ask

### 002. Outline generated in Russian despite `ai.language: en`
**Severity**: High (wrong language = wrong signals for entire paper workflow)
**When**: `klemma outline` in English paper sub-project
**What**: Outline generated entirely in Russian — chapter titles, descriptions, section names all in Russian. Config has `ai.language: en`.
**Root cause**: Three contributing factors:
1. **Prompt contradiction**: `outline.md` line 90 says "respond in the same language as the project materials" which overrides line 92's `Respond in {{ language }}`. Since parent's files (Глава_1.md etc.) are Russian, AI follows the materials.
2. **Inherited chapters leak Russian context**: `dissertation_context` is built from merged config including parent's Russian chapter titles. AI sees "Глава 1: Анализ предметной области..." and writes in Russian.
3. **`chapters` and `chapter_mapping` inherited from parent**: Deep merge brings parent dissertation's 4-chapter Russian structure into child paper config. A 10-page paper doesn't have 4 dissertation chapters.
**Workaround**: Remove `chapters` and `chapter_mapping` from child config. Regenerate outline with `--fresh`.
**Fix needed (code)**:
- `outline.md` prompt: remove "respond in same language as materials", keep only `Respond in {{ language }}`
- `klemma init` for `type: paper` inside a dissertation: do NOT inherit parent's `chapters`/`chapter_mapping`/`section_type_map`
- Consider: `_INHERITED_KEYS` should NOT include `project` section — project structure is per-project, not inherited
- `work_context.build_work_context()`: when `language=en`, translate or skip Russian chapter titles

### 003. `pre_extract_sources` ignores prune verdicts
**Severity**: Low (pruned sources usually have fragments already, so skipped by fragment_count check)
**When**: `klemma research -s N` auto-processes sources
**What**: `pre_extract_sources()` doesn't filter out sources with "drop" prune verdicts. Could waste API calls processing irrelevant papers.
**Root cause**: No prune check in `researcher.py:pre_extract_sources()`. Only checks `fragment_count == 0`.
**Risk**: Low in practice — pruned sources typically already have fragments. But for completeness, should filter.
**Fix**: Add `prune_drops = state.get_prune_drop_ids()` filter in `pre_extract_sources()`

### 004. `klemma research` ignores child project context and language
**Severity**: High (research output is unusable for sub-projects)
**When**: `klemma research -s 1` in paper sub-project
**What**: Research briefing is entirely in Russian, based on dissertation structure, ignores paper's own topic/chapters/language. Output is a dissertation-style analysis, not a conference paper briefing.
**Root cause**: Four contributing factors:
1. **`dissertation_context` is parent-first concat**: `load_project_context()` joins parent KLEMMA.md (Russian, detailed) + child KLEMMA.md (English, brief) with `---`. Parent dominates.
2. **No child-specific framing**: The research prompt doesn't know it's writing for a 10-page English conference paper. It sees the full dissertation structure (4 chapters, 6 research tasks) and produces dissertation-style analysis.
3. **Language instruction is weak**: `research.md` line 140 says `Respond in {{ language }}` but this is the last line. The entire prompt body (context, fragments, sources) is in Russian. AI follows the dominant language.
4. **`_get_dissertation_context()` fallback**: When `dissertation_context` is passed from CLI (parent-first concat), it overrides `build_work_context(project, language)` which would correctly build English context from child's ProjectConfig.
**Fix needed (code)**:
- For sub-projects, `dissertation_context` should prioritize child's context. Options:
  a. Use only child's `build_work_context()` as primary context, append parent as "parent project background"
  b. Add a `## Parent Project` section wrapper so AI knows the hierarchy
- Research prompt: add explicit framing at the TOP: `This is a {{ project_type }} ({{ language }}). Respond entirely in {{ language }}.`
- Research prompt: add paper-specific constraints when `project_type == "paper"` (page limit, conference name, format)
- `load_project_context()`: for child projects, reverse priority — child context first, parent as supplementary

**Status (2026-03-07):** Issues 001, 002, 004 FIXED via PR #106 + issue #108 (ADR-012: child project context isolation). `load_project_context()` now returns child-only context. Parent context no longer leaks. `klemma init` for child projects skips ai/embeddings defaults when parent provides them. Issue 003 remains open (low severity).

---

## Session 2: TITDS-2025 Conference Paper (2026-03-07)

**Goal:** Write a conference paper for TITDS-XV-2025 (Intelligent Transport Systems track) using klemma, testing methodology-driven prompt improvements from Steps 11-12.

### Setup

- Created child project in `~/research/dissertation/papers/titds-2025/`
- Inherits dissertation library (378 sources, 2135 fragments) via `inherit_db`
- Added `previous_paper.md` (prior MDPI Remote Sensing work) + `dissertation_outline.md` as project files
- Language: English, type: paper

### A/B Outline Test

Ran `klemma outline` twice — once with baseline prompts (pre-Step 11), once with methodology-driven prompts (post-Steps 11-12).

**Baseline:** Generic 7-section structure with 800w Software Implementation filler, no Discussion, no CARS structure in introduction, no conference track alignment.

**Methodology-driven:** Clean IMRAD (6 sections), CARS visible in intro (gap = subsection title), argument-grouped literature review (3 subsections), Discussion section added, Software filler eliminated, conference track keywords woven throughout. 500 words shorter but substantially richer.

**Finding:** Methodology-driven prompts produce measurably better structure. First empirical A/B evidence for prompt grounding. Documented in `results/step_12_prompt_audit_and_expansion.md`.

### Issues Found

### 005. No issues found during outline generation
Child project context isolation (ADR-012) worked correctly: English output, paper-appropriate structure, no Russian leakage, no dissertation chapter inheritance. All four fixes from issues 001-004 confirmed working.

### Observations

- `--prompt` flag for custom directives works well for conference-specific instructions
- `scan_project_files()` correctly picked up `previous_paper.md` and `dissertation_outline.md` as context
- Methodology block (CARS, results-first, argument grouping) demonstrably shaped the outline structure
- The outline correctly aligned with the TITDS conference track (AI/ML for transport)
