# TUI — Textual Dashboard

Textual-based TUI app launched via bare `klemma` command (no subcommand).
Entry point: `app.py` → `KlemmaApp` → mounts screens.

## Screens

| Screen | Lines | Purpose |
|--------|-------|---------|
| `dashboard.py` | 110 | Main: stats row + today's plan + coverage + ref-gaps |
| `coverage.py` | 68 | Chapter/section coverage table with color-coded status |
| `gaps.py` | 69 | Reference gaps with scores |
| `fragments.py` | 47 | Fragment browser (source, type, section, relevance, text) |
| `stats.py` | 68 | Processing statistics (completed/pending/failed) |

## Architecture
- Pure **read-only layer** over state — no direct AI or literature imports
- All screens import only: `config.KlemmaConfig`, `state.StateManager`, `vault.VaultAdapter`
- Data via: `state.get_stats()`, `state.get_fragment_stats()`, `state.get_plan()`, `state.get_gap_summary()`, `state.get_coverage_stats()`, `state.get_fragments()`
- Status colors: green (ok), yellow (low), red (gap)

See: [Core infrastructure](../CLAUDE.md) for state query methods used by TUI
