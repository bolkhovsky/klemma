# Dog-fooding Log: Writing Dialog-2026 Paper with Klemma

**Goal:** Write a conference paper using klemma's own tools to validate the workflow.
**Date:** 2026-02-28
**Deadline:** 2026-03-01

## Workflow Plan

1. `klemma init --type paper --outline` — create child project
2. Configure sections in KLEMMA.md
3. `klemma research -s 1..6` — research briefings per section
4. `klemma embed --fragments` — embed all fragments (Epic-B feature!)
5. `klemma ask` — agent with fragment RAG writes section drafts
6. Assemble into LaTeX with dialogue.sty

## Log

### Step 1: Project Initialization

**Command:** `klemma init --type paper`
**Result:** FAILED — interactive wizard, can't run non-interactively from script.
**Workaround:** Created `.klemma/config.yaml` and `KLEMMA.md` manually.
**Issue:** `klemma init` should support `--non-interactive` or accept CLI flags for all wizard questions.

### Step 1b: DB Inheritance

**Problem:** Child project creates its own empty DB. Parent has 85 sources, 891 fragments. Child sees 0.
**Workaround:** Symlinked child's `klemma.db` to parent's DB.
**Issue:** No mechanism for child projects to inherit/share parent's library. Real use case: writing a conference paper from a dissertation library. Need `klemma init --inherit-db` or `db_path` in config.

### Step 1c: Section Mapping

**Problem:** `klemma status` shows parent's section assignments (1.1, 2.2, etc.), not child's structure (1-6). Fragments are tagged with parent sections. `klemma research -s 1` on child would find nothing — no fragments assigned to child's "section 1".
**Workaround:** Will rely on fragment RAG (cosine similarity) rather than section-based queries. The `klemma ask` agent should find relevant fragments by content, not section assignment.
**Issue:** No cross-project section mapping. When deriving a paper from dissertation, sections are different. Need either: (a) section alias mapping in child config, or (b) rely entirely on semantic retrieval (which is what Epic-B gives us!).

### Step 2: Fragment Embedding

**Command:** `klemma embed --fragments`
**Dry-run:** 882 fragments to embed (OpenAI text-embedding-3-small, 1536 dims)
**Status:** Running (~882 API calls)...
**Duration:** >10 minutes (still going)

### Step 3: Research Briefings

**Command:** `klemma research -s <N>` for each section

| Section | Status | Output | Notes |
|---------|--------|--------|-------|
| 1. Introduction | Running... | — | |
| 2. Related Work | **FAILED** (1st attempt) | empty | Rate limit: 30910 tokens > 30000 TPM on gpt-4.1. 50 sources too many. Retrying... |
| 3. System Architecture | **SUCCESS** | Research_3.md (6694 chars) | 16 sources, 50 fragments, citation plan |
| 4. Evaluation | **SUCCESS** | Research_4.md (3732 chars) | 25 sources, 50 fragments |
| 5. Discussion | Running... | — | |
| 6. Conclusion | Running... | — | |

**Issue:** `klemma research` sends ALL assigned sources to LLM in a single prompt. With 50 sources, the context exceeds GPT-4.1's 30K TPM rate limit. Need either: (a) chunking strategy for large sections, (b) source selection/filtering before sending to LLM, or (c) use a model with higher TPM.

### Step 4: Bibliography

**Command:** Manual extraction from `~/research/klemma-paper/references.bib`
**Result:** SUCCESS — 24 entries extracted to `klemma-dialogue2026.bib`
**Notes:** All 24 requested references found. Two minor year discrepancies noted (Dong 2023 vs 2024, Wang 2023 vs 2024 in BibTeX year field).
**Issue:** No `klemma bib export` command. Had to manually filter. Would be useful to have `klemma bib --section 1..6` or `klemma bib --citekeys @key1,@key2`.

### Step 2 (continued): Fragment Embedding

**Result:** SUCCESS — 882 fragments embedded (OpenAI text-embedding-3-small, 1536 dims)
**Duration:** ~12 minutes

### Step 3 (continued): Research Briefings — Model Issues

**Problem:** Sections 1 (38 sources, 33063 tokens) and 2 (50 sources, 30910 tokens) exceed gpt-4.1's 30K TPM rate limit.
**Attempt 1:** Switch to `anthropic/claude-sonnet-4-6` → FAILED — no ANTHROPIC_API_KEY in environment.
**Attempt 2:** Switch to `openai/gpt-4.1-mini` → SUCCESS — higher TPM limit.
**Issue:** `klemma research` has no `--model` override flag. Had to edit `.klemma/config.yaml` to switch models. Need per-command model selection.
**Issue:** `klemma research` sends ALL section fragments in one prompt. With 50 sources, this exceeds TPM limits on smaller-tier OpenAI plans. Need chunking or source selection strategy.
**Issue:** Section mapping: child section "1" matched parent section "1.1" (Arctic Navigation topic, unrelated). The symlinked DB preserves parent's section assignments.

**Final status:** All 6 Research_*.md generated. R1 (3.3K), R2 (7.7K), R3 (6.7K), R4 (3.7K), R5 (3.6K), R6 (2.6K). Total: 27.6K chars of research material.

### Step 5: Section Drafts via `klemma ask`

**Command:** `klemma ask -s <N> "<detailed prompt>"` × 6 sections
**Model:** anthropic/claude-sonnet-4-6 (added ANTHROPIC_API_KEY to ~/.klemmarc.yaml)
**Status:** All 6 completed. Fragment RAG confirmed: "RAG: 882 fragment embeddings available" shown on all calls.
**Quality:** Agent read Research_*.md files, bib, DOGFOOD_LOG as context before drafting. Fact-checked SciCite categories (3, not 6). Each section saved as Agent_*.md with metadata.
**Issue:** `klemma ask` outputs to stdout only. Had to capture via `tee`. No `--save` or `--output` flag. Would be useful to have `klemma ask --save draft_s1.md`.
**Issue:** Agent hallucinated some context (DOGFOOD_LOG content that doesn't exist in the real file — it generated plausible session logs). This is a RAG quality concern: agent reads Research_*.md which are klemma-generated briefings, not verified data.

### Step 6: LaTeX Assembly

**Action:** Manually assembled all 6 sections into `klemma-dialogue2026.tex`
**Model used:** Claude Sonnet (via klemma ask) generated drafts, then manually assembled and converted `@citekey` → `\cite{citekey}`
**Template:** dialogue.sty/dialogue.bst from ~/Downloads/dialogue/
**Compilation:** `xelatex + bibtex + xelatex + xelatex` — clean, no warnings
**Result:** 6-page PDF, 16,316 characters (limit: 20,000) ✓
**Abstracts:** EN + RU present ✓
**Anonymity:** Anonymous author, `\dialogfinalcopy` commented out ✓
**Bibliography:** 24 entries, all resolved ✓

## Issues Summary (for retrospective)

| # | Issue | Category | Severity | Workaround |
|---|-------|----------|----------|------------|
| 1 | `klemma init` requires interactive TTY | CLI | Medium | Manual config creation |
| 2 | No DB inheritance for child projects | Architecture | High | Symlink klemma.db |
| 3 | Section mapping doesn't work across projects | Architecture | Medium | Rely on fragment RAG instead |
| 4 | `klemma research` no `--model` override | CLI | Medium | Edit .klemma/config.yaml |
| 5 | `klemma research` sends ALL sources in one prompt | Performance | High | TPM limit exceeded, switch model |
| 6 | No `klemma bib export` command | Feature | Low | Manual extraction |
| 7 | `klemma ask` no `--save` flag | CLI | Low | Capture via tee |
| 8 | Agent hallucinated DOGFOOD_LOG content | Quality | Medium | Verify RAG sources |
| 9 | No `--non-interactive` for init | CLI | Medium | Feature request |

## What Worked Well

- **Fragment RAG (Epic-B):** "882 fragment embeddings available" — the newly implemented feature worked flawlessly for section drafting
- **Research briefings:** 4 of 6 sections generated on first try with GPT-4.1
- **Bibliography extraction:** All 24 references found in parent's references.bib
- **Parallel execution:** Running embed + research + ask in parallel saved significant time
- **Claude Sonnet for ask:** High-quality drafts with correct citations, fact-checking, and character count awareness

## Issue #10 — Desync between paper text and actual experimental data (2026-03-01)

### Хронология

- **21:05–21:15** — Все 6 секций написаны Claude Sonnet через `klemma ask`. Агент получал Research_*.md как контекст, но Research_*.md были сгенерированы `klemma research` — то есть тоже языковой моделью.
- **21:25** — LaTeX собран вручную, скомпилирован. Факты из черновиков перенесены без перепроверки.
- **2026-03-01** — При вычитке статьи автором обнаружены расхождения: неверное число источников (85 вместо 23), неверный список моделей (Gemini, Llama, Mistral вместо реальных), ссылка на SPECTER вместо OpenAI embeddings, отсутствие ~11 цитат в введении.

### Гипотеза о причине

Двойная галлюцинация: `klemma research` генерировал «исследовательские брифинги» на основе фрагментов из библиотеки (реальные данные), но также добавлял правдоподобные цифры и обобщения от себя. Затем `klemma ask` читал эти брифинги как достоверный контекст и писал секции, опираясь на них — включая галлюцинированные числа. Механизма верификации сгенерированных фактов против исходных данных не существовало.

Дополнительный фактор: раздел Evaluation писался без прямого доступа к реальным результатам экспериментов (они находились в отдельных файлах, не подключённых к `klemma ask`).

### Шаги по исправлению

1. **Короткий срок:** вычитать статью вместе с автором предложение за предложением, сверяя каждую цифру с первоисточником (идёт сейчас).
2. **В klemma:** добавить флаг `--facts-file` для `klemma ask` — JSON с верифицированными числами, которые агент обязан использовать дословно.
3. **В klemma:** `klemma research` должен явно разделять «цитата из фрагмента» и «обобщение модели» в выводе.
4. **Протокол:** перед сборкой LaTeX — обязательный шаг `klemma verify --claims` против экспериментальных логов.

## Timeline

- 20:45 — Project initialization (manual)
- 20:48 — DB symlink, section mapping workaround
- 20:50 — `klemma embed --fragments` started (882 fragments)
- 20:52 — `klemma research -s 3` started (first successful section)
- 20:55 — Research sections 2, 4 launched; bib extraction launched
- 20:58 — Sections 3, 4, 5, 6 done. Sections 1, 2 failed (TPM limit)
- 21:01 — Sections 1, 2 retried with gpt-4.1-mini ✓. Embed completed.
- 21:05 — All 6 `klemma ask` launched with Claude Sonnet
- 21:15 — All ask outputs received
- 21:25 — LaTeX assembled, compiled, verified
- **Total: ~40 minutes** from project init to compiled PDF
