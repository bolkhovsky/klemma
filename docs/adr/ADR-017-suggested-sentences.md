# ADR-017: Suggested Sentences as Curation Primitive

**Status**: Accepted
**Date**: 2026-04-13
**Context window**: Epic — verbatim fragment integrity (PRs #308/#312) established "library fragment = exact substring of source PDF, no paraphrase." That integrity removed an invisible step researchers had been doing mentally: translating English verbatim quotes into Russian academic prose with proper author attribution and `[@citekey]` before typing them into a draft. For a 200-citation dissertation this silently cost several hours per write session.

## Decision

Introduce **suggested sentences** as a first-class curation primitive distinct from the fragment itself. A suggested sentence is a model-generated Russian academic formulation of a verbatim fragment — editable by the user and persisted per project.

Pipeline: `verbatim fragment` → `suggested sentence (per project, per language)` → `accepted/rejected curation decision carrying the edited sentence` → `drafter candidate_sentences input`.

## Storage

Suggested sentences live on `fragment_curation` (project-scoped, language-dependent), **not** on `fragments` (library-global, language-agnostic).

New columns in `fragment_curation` (schema v13):
- `suggested_text TEXT` — the current sentence (user-edited value wins over generator value)
- `sentence_model TEXT` — the AI model that produced the original sentence

Migration strategy: idempotent `_migrate_schema()`, gated on `PRAGMA user_version`, checks `sqlite_master` for legacy DBs that pre-date the table and creates it from scratch in v13 shape rather than ALTERing a missing table.

## Regeneration semantics

`POST /projects/{project_id}/fragments/generate-sentences` accepts:
- `mode: "missing"` (default) — skip fragments that already have `suggested_text`
- `mode: "force"` — regenerate all fragments for the citekey, overwriting existing sentences

User-initiated "Сгенерировать предложения" in the review UI uses `missing`. "Перегенерировать все" uses `force`. Per-card retry uses `missing` after clearing the local sentence.

## Partial failure

Never all-or-nothing. The skill persists whatever JSON the model returns, reports `{generated, failed, failed_ids, sentences}`. Successful fragments hydrate immediately. Failed fragment ids surface a per-card retry affordance. The top-of-view toast summarises partial completion but does not block further interaction.

## Language invalidation (V1 policy)

If `project.language` changes, stored sentences are considered stale but are **not** auto-invalidated. V1: the user clicks "Перегенерировать все" (mode=force) after a language switch. V2 can auto-invalidate by comparing `sentence_model` language metadata to the current project language.

## Draft-pipeline composition

On `generate_draft()`, the backend loads accepted curation rows for the target section that have non-empty `suggested_text` and passes them as `candidate_sentences` to `skills/drafter.py`. The drafter forwards them through the `section_draft.md` Jinja2 template as a "Предложения-кандидаты для интеграции" block. Instruction to the model: integrate verbatim or with minimal stylistic edits — do not invent new formulations when a candidate already covers the meaning.

No deduplication: accepted fragment in → candidate out; rejected fragment in → candidate out absent. This preserves the user's curation decisions as the authoritative signal.

## Red-line compliance

- Skills do **not** import `state.py` — `skills/sentence_generator.py` receives all state via function arguments and returns `SentenceResult`
- `LocalUserStore` migration is idempotent via `PRAGMA user_version` check
- Mutations are visible in both CLI (via future `klemma sentences` command) and API (response payload reports `generated`/`failed`/`failed_ids`)
- No new state in `cli.py`; the feature lives entirely in routes → tasks → skills
- Backward compatibility: legacy `fragment_curation` rows with NULL `suggested_text` continue to round-trip through `get_pending` / `get_curated` unchanged

## Consequences

- **Positioning shift**: Klemma moves from "AI writes drafts" toward "Klemma hands you sentences, you assemble drafts." Lower fabrication risk (each sentence traces to a verbatim fragment), stronger vertical moat vs. horizontal research agents that stop at "here is a relevant quote."
- **Cost**: ~1000 tokens per source (10 fragments). At Sonnet pricing ≈ $0.003/source — negligible vs. extraction (~$0.05/source). Regenerations are opt-in, so cost scales with intent, not traffic.
- **Schema growth**: two NULLABLE columns on `fragment_curation`. No index added — queries are always citekey/project scoped and the existing indexes cover access patterns.

## References

- Plan: `~/.claude/plans/happy-honking-trinket.md`
- Verbatim fragment integrity: PRs #308, #312
- Drafter pattern: `src/klemma/skills/drafter.py`
- Three-tier library: ADR-014
