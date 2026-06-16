# ADR-018 Citation Integrity Verification

**Status**: Accepted  
**Date**: 2026-06-14  
**Epic**: #394

## Context

The WMO incident (2026-06-13) demonstrated a fabrication class not caught by existing checks:
correct citation number + real citekey + fabricated definitional gloss. A human reviewer caught it.

Klemma's verbatim pass-through (ADR-017) surfaces `✓дословно`/`~парафраз` badges but doesn't
verify claims against source text. The gap: numeric values can drift, quoted text may not exist
in the source, and definitions may contradict the paper's actual content.

## Decision

Implement an **LLM-as-judge citation verifier** as a standalone `klemma check-citations` command
(not wired inline during draft generation — that's PR 3 / ADR-018b).

### Evidence model

Each claim anchor has three bits:
- `source_available` — is there any source text for this citekey?
- `search_complete` — did we search the *full* untruncated PDF (sidecar only)?
- `anchor_found` — is the exact anchor text present in the source?

These bits drive dispatch:

| anchor_found | search_complete | action |
|---|---|---|
| True | any | AI judge (drift/consistency check) |
| False | True | hard_warn without AI |
| False | False | unverifiable |

### Anchor types

- **quote** — text in `«»` or `""` with ≥5 words or ≥30 chars
- **numeric** — numbers with optional units
- **definitional** — sentence contains a definitional trigger word/phrase

### Dispatch table

| Anchor type | anchor_found | handler |
|---|---|---|
| quote | any | `verify_claim()` — deterministic, exact match check |
| numeric | False | `verify_claim()` — hard_warn if search_complete else unverifiable |
| numeric | True | `verify_claim_batch()` — AI drift check |
| definitional | any | `verify_claim_batch()` — AI entailment check |

### Fail-open guarantee

The verifier never blocks draft generation. Status: `ok` / `degraded` / `error`.
`degraded` when AI is unavailable or deadline/budget exhausted.

### AI judge provider (isolated)

A separate `AIProvider` is cloned from `config.ai` with:
- `json_mode=True`
- `retries=citation_check_retries` (default 0)
- `timeout=citation_check_timeout` (default 60s)
- Explicit `_resolved_api_keys` copy (no shared mutable state)

**CTO RC3 (claude backend):** route judge through litellm only if `citation_check_model`
is explicitly `anthropic/model-name` AND anthropic key is available. Otherwise `judge_ai=None`
(degraded). This prevents the claude CLI backend from being used as a JSON judge.

### Anti-injection

Data boundaries: `<<<CLAIM>>>...<<<END>>>` in the prompt template. All user-provided text
(claim sentences, anchor raw text, passages) is sanitized by replacing `<<<` → `<<` and
`>>>` → `>>` before rendering.

### Per-file budget controls

- `citation_check_max_wall_clock` (default 120s): deadline per file
- `max_ai_calls_per_draft` (default 12): max judge calls per file
- Deadline is checked BEFORE the AI call (not via timeout=0, which would be falsy in LiteLLM)

### Verdict envelope (always batch format)

```json
{
  "verdicts": [
    {
      "anchor_id": "START:END",
      "verdict": "ok|unsupported|contradicted|not_found",
      "contradiction": false,
      "severity": "ok|unverifiable|soft_warn|hard_warn",
      "offending_span": "...",
      "reason": "one sentence"
    }
  ]
}
```

## Source resolution priority

1. **Sidecar** (`.klemma/pdfs/<citekey>.md`) — full untruncated PDF text via `read_pdf_sidecar()`
   (search_complete=True)
2. **paper_store raw_text** — 50K truncated cache via three-tier library (search_complete=False)
3. **Legacy state fragments** — extracted fragment texts (search_complete=False)

## Files

- `src/klemma/skills/citation_checker.py` — engine (anchors, evidence, dispatch, judge)
- `src/klemma/commands/verify.py` — CLI command
- `prompts/citation_check.md` — Jinja2 judge prompt
- `src/klemma/config.py` — 11 new `AIConfig` fields (`citation_check_*`)
- `tests/test_citation_checker.py` — engine unit tests
- `tests/test_verify_command.py` — CLI integration tests

## Severity levels

| Level | Meaning | Default fail? |
|---|---|---|
| `ok` | supported | no |
| `unverifiable` | no source or incomplete search | no |
| `soft_warn` | source is silent (definitional) | no |
| `hard_warn` | number absent or quote missing or contradiction | **yes** |
| `error` | engine failure | yes (exit 2) |

## Rejected alternatives

- **Inline blocking**: would slow draft generation and break fail-open guarantee
- **Always-AI**: too slow and expensive per claim; deterministic path handles ~40% of cases
- **Single judge call per file**: requires building the full prompt for all claims; batch per anchor gives finer control and budget management
