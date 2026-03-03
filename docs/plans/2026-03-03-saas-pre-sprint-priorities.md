# Pre-SaaS Sprint Issue Prioritization

**Date:** 2026-03-03
**Context:** Preparing for CiteQ.ru SaaS epic (klemma → SaaS). Evaluating 11 open issues to decide what to close before the sprint vs defer.

## Prioritization Criteria

1. **Blocks SaaS?** — can't extract klemma-core cleanly without it
2. **Resolved by SaaS refactoring?** — don't duplicate effort
3. **Needed for paper?** — klemma-paper is in parallel
4. **Effort vs Impact** — small wins to clear backlog

---

## Tier 1: DO BEFORE SaaS

| Issue | Title | Why | Effort |
|-------|-------|-----|--------|
| [#34](https://github.com/klemma-ai/klemma/issues/34) | Organize reports into notes/ subdirs | 6/8 done. SaaS storage adapter depends on dir structure | ~1h |
| [#54](https://github.com/klemma-ai/klemma/issues/54) | `klemma init --non-interactive` | SaaS creates projects programmatically (WebContext) | ~2h |
| [#36](https://github.com/klemma-ai/klemma/issues/36) | Class-based model routing | BYOK + LiteLLM routing for cost control | ~4h |

**Total estimated:** ~7h

## Tier 2: DO DURING SaaS

| Issue | Title | Phase | Rationale |
|-------|-------|-------|-----------|
| [#67](https://github.com/klemma-ai/klemma/issues/67) | Semantic section types | Phase 1 | Cross-cutting refactor, better with klemma-core package |
| [#39](https://github.com/klemma-ai/klemma/issues/39) | Online source ingest | Phase 2 | Part of SaaS upload flow |
| [#63](https://github.com/klemma-ai/klemma/issues/63) | SPECTER vs OpenAI embeddings | Phase 2 | Benchmark determines default CiteQ embedder |

## Tier 3: DEFERRED

| Issue | Title | Rationale |
|-------|-------|-----------|
| [#60](https://github.com/klemma-ai/klemma/issues/60) | `klemma ask --save` | Resolved by SaaS web UI auto-save |
| [#59](https://github.com/klemma-ai/klemma/issues/59) | `klemma bib export` | Resolved by SaaS `/export` endpoint |
| [#37](https://github.com/klemma-ai/klemma/issues/37) | Run intent benchmark | Blocked by paper first draft |
| [#31](https://github.com/klemma-ai/klemma/issues/31) | Epic-E: Deferred expansion | Meta-issue, #54 promoted out |
| [#30](https://github.com/klemma-ai/klemma/issues/30) | Epic-D: Advanced features | Stretch goals → CiteQ post-MVP |

---

## Execution Order

```
BEFORE SaaS sprint:
  1. #34 — finish remaining 2/8 acceptance criteria
  2. #54 — klemma init --non-interactive
  3. #36 — class-based model routing

SaaS Phase 1 (Foundation):
  4. #67 — semantic section types (with klemma-core extraction)

SaaS Phase 2 (Core Processing):
  5. #39 — online source ingest
  6. #63 — SPECTER benchmark (parallel)
```

## GitHub Labels & Milestones

- Label `pre-saas` on: #34, #54, #36
- Label `priority-high` on: #54, #36
- Milestone `SaaS Phase 1` on: #67
- Milestone `SaaS Phase 2` on: #39, #63
