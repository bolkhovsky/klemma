# ADR-011: Mutation Command Pattern

**Status**: Accepted
**Date**: 2026-03-07
**Context**: Inconsistent confirmation UIs across mutation commands

## Problem

Klemma CLI has several commands that mutate state (DB, vault files, external services). Each implemented its own confirmation UI:

| Command | Mutation | Confirmation style |
|---------|----------|--------------------|
| `prune --apply` | Delete sources from DB | Per-item y/n/q with details |
| `reassign --apply` | Add sections to vault frontmatter | Per-item y/n/q with details |
| `acquire` | Download PDF + register source | Batch confirm |
| `process` | Extract fragments, overwrite existing | No confirmation |
| `library prune` | AI generates drop/keep verdicts | No confirmation (suggest-only) |

No shared code, inconsistent default choices, different detail levels.

## Decision

All mutation commands follow a two-phase **suggest → apply** pattern with a shared `interactive_review()` helper.

### Command Categories

| Category | Flags | Behavior |
|----------|-------|----------|
| **Read-only** | (none) | Default. Show suggestions/status. No mutation. |
| **Suggest + apply** | `--apply` | Show suggestions, then per-item interactive confirmation. |
| **Batch apply** | `--apply --yes` | Auto-accept all items. For scripts and CI. |
| **Dry run** | `--dry-run` | Preview what would happen without API calls or mutations. |

### Shared Helper: `cli_confirm.py`

```python
from klemma.cli_confirm import ReviewItem, interactive_review

items = [ReviewItem(
    key="chevallier2013",
    header="@chevallierSeasonalForecastsPanArctic2013",
    details=[
        ("Fragment", "Seasonal forecasts of Pan-Arctic sea ice..."),
        ("Current", "3.2 — Разработка методики (sim=0.412)"),
        ("Suggested", "1.4 — Анализ предметной области (sim=0.687)"),
    ],
    action_label="Add section 1.4 to vault frontmatter",
    data={"citekey": "chevallier2013", "section": "1.4"},
)]

result = interactive_review(
    items,
    console=console,
    title="Review reassignment suggestions",
    default_choice="y",
    yes=auto_yes_flag,
)

# result.accepted — list of ReviewItem the user said "y" to
# result.skipped — count of "n" responses
# result.quit_early — True if user pressed "q"
```

### Per-Item Display Format

```
── [1/20] @chevallierSeasonalForecastsPanArctic2013 ──
  Fragment: Seasonal forecasts of Pan-Arctic sea ice...
  Current: 3.2 — Разработка методики (sim=0.412)
  Suggested: 1.4 — Анализ предметной области (sim=0.687)
  Action: Add section 1.4 to vault frontmatter
  Accept? [y/n/q] (y):
```

Every item shows:
1. **What** — the entity being changed (source, fragment, gap)
2. **Why** — context for the suggestion (scores, reasons, descriptions)
3. **Action** — exactly what will happen if accepted
4. **Prompt** — `y`/`n`/`q` with configurable default

### Rules for New Mutation Commands

1. Default mode is always **read-only** (suggest/display)
2. `--apply` enables mutations with per-item confirmation
3. `--yes` / `-y` skips prompts (requires `--apply`)
4. Use `ReviewItem` + `interactive_review()` from `cli_confirm.py`
5. After all items, print summary: `N accepted, M skipped, K applied`
6. Mutations are visible: print what changed after each accepted item

### Migration Plan

| Command | Current | Target | Priority |
|---------|---------|--------|----------|
| `reassign --apply` | Custom loop | `interactive_review()` | Now |
| `prune --apply` | Custom loop | `interactive_review()` | Next |
| `acquire` (batch) | `click.confirm()` | Keep as-is (batch, not per-item) | Low |
| `process` | No confirmation | Add `--yes` for overwrite | Low |

## Consequences

- **Consistent UX** across all mutation commands
- **Shared code** reduces per-command boilerplate (~30 lines → ~10 lines)
- **Testable** — `ReviewItem` is a dataclass, `interactive_review` is a pure function with injected console
- **`--yes` flag** enables scripting and CI pipelines
- All mutations remain **opt-in** — read-only by default

## Alternatives Considered

- **Batch confirmation** (single "apply all?" prompt): Rejected. Per-item review is essential for academic work where wrong assignments have real consequences.
- **TUI checkbox list**: Over-engineering for CLI. Textual/curses adds complexity for marginal UX gain.
- **Config-based auto-apply**: Dangerous. Mutations should require explicit intent.
