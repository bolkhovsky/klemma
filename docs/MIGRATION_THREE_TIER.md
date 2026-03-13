# Migration Guide: Monolithic DB → Three-Tier Library

**Applies to**: klemma after PRs #143–#145 merged (ADR-014 Phase 1A–1C). The `migrate-library` command ships with PR #145.
**Estimated time**: 5–15 minutes per project

---

## What changes

Before migration each project stored everything in its own database:

```
project/.klemma/data/klemma.db   ← sources, fragments, embeddings, gaps, plans, benchmarks
```

After migration data is split across two shared files:

```
~/.klemma/library.db                     ← papers, fragments, embeddings  (shared across all projects)
project/.klemma/data/project.db          ← section assignments, gaps, plans, benchmarks  (per-project)
```

**Why bother?** Processing the same PDF in project B is now instant if project A already extracted it — no Claude API call, no OpenAI embed call. For a 200-paper dissertation library shared with 3 satellite papers: ~$6 and ~90 minutes saved.

---

## Before you start

**1. Verify `migrate-library` is available**

```bash
klemma migrate-library --help
```

If you get `No such command`, PR #145 hasn't merged into your installed version yet — upgrade first:

```bash
pip install --upgrade klemma
# or, to install directly from the merged branch:
pip install git+https://github.com/klemma-ai/klemma.git@master
```

**2. Check which projects you have**

```bash
find ~/research -name "klemma.db" -path "*/.klemma/data/*" 2>/dev/null
```

Note the paths — you'll migrate each one separately.

**3. Take a manual backup** (the command also makes one automatically, but belt and suspenders)

```bash
cp ~/.klemma/data/klemma.db ~/.klemma/data/klemma.db.manual_bak
# for each project:
cp project/.klemma/data/klemma.db project/.klemma/data/klemma.db.manual_bak
```

---

## Migration steps

### Single project

```bash
cd ~/research/my-thesis/

# 1. Dry run — see what would be migrated, no changes made
klemma migrate-library

# Expected output:
#   Three-tier library migration — DRY RUN
#   Source DB   : .../klemma.db
#   library.db  : ~/.klemma/library.db
#   project.db  : .../project.db
#
#   368 sources · 1838 fragments · 832 section assignments
#
#   Dry run — pass --apply to execute migration

# 2. Run migration
klemma migrate-library --apply

# Expected output:
#   Backup created: .../klemma.db.bak
#   Migration complete:
#     Papers registered : 368
#     Fragments migrated: 1838
#     Section entries   : 832
#   Note: Migrated papers use citekey-based deduplication...

# 3. Verify
klemma status
```

`klemma status` must show the same source counts and section coverage as before.

---

### Multiple projects (dissertation + satellite papers)

Migrate the **parent project first**, then child projects. When a child migrates a paper already in `library.db` (same citekey → same synthetic hash), the existing `paper_id` is reused — deduplication kicks in automatically.

```bash
# 1. Parent (dissertation)
cd ~/research/dissertation/
klemma migrate-library --apply
klemma status

# 2. Child papers (in any order)
cd ~/research/paper-dialog-2026/
klemma migrate-library --apply
klemma status

cd ~/research/paper-sea-ice/
klemma migrate-library --apply
klemma status
```

After all migrations, `~/.klemma/library.db` contains the deduplicated union of all papers. Each project's `project.db` contains only its own section assignments and gaps.

**Note on deduplication**: Migration uses citekey-based dedup (not PDF SHA256). Same paper under the same citekey in two projects → one entry in `library.db`. Same paper under *different* citekeys (e.g. `smith2022` vs `smith2022nlp`) → two entries. Run `klemma process --force <citekey>` after migration to upgrade to content-addressable SHA256 dedup for any paper.

---

## What to check after migration

### 1. Source counts match

```bash
klemma status --verbose
```

Compare total sources and per-section counts with your pre-migration numbers.

### 2. Fragment search still works

```bash
klemma research -s 1.1   # should find fragments as before
klemma ask "What methods does smith2022 use?"
```

### 3. New papers process faster

Add a new paper that's already in another project's library:

```bash
klemma process <citekey>
# Should print: "Found in library.db — skipping extraction" (no Claude API call)
```

### 4. Library DB is being populated going forward

After processing any new paper, verify it landed in `library.db`:

```bash
sqlite3 ~/.klemma/library.db "SELECT paper_id, title FROM papers ORDER BY created_at DESC LIMIT 5;"
```

---

## Rollback

If anything goes wrong, the command created a backup automatically:

```bash
# Restore monolithic DB from backup
cp project/.klemma/data/klemma.db.bak project/.klemma/data/klemma.db

# Delete the new files (or just leave them — klemma still reads monolithic DB)
rm project/.klemma/data/project.db
rm ~/.klemma/library.db   # only if no other projects have migrated
```

klemma continues to read and write `klemma.db` during the Phase 1B–1C transition. The new stores operate in parallel (dual-write). Rollback is safe.

---

## File layout after migration

```
~/.klemma/
├── library.db                     # papers + fragments + embeddings (ALL projects share this)
└── data/
    └── klemma.db                  # legacy system-level DB (unused by default)

~/research/dissertation/
└── .klemma/
    ├── config.yaml
    └── data/
        ├── klemma.db              # monolithic DB (still used by StateManager in Phase 1C)
        ├── klemma.db.bak          # backup created by migrate-library
        └── project.db             # new: section assignments, gaps, plans

~/research/paper-dialog-2026/
└── .klemma/
    └── data/
        ├── klemma.db
        ├── klemma.db.bak
        └── project.db
```

---

## Known limitations (Phase 1C)

| Limitation | Impact | When fixed |
|------------|--------|------------|
| StateManager still reads monolithic `klemma.db` | `klemma status`, `klemma research` etc. still work via old path | Phase 1D |
| `klemma status` coverage comes from `klemma.db`, not `project.db` | No behavior change yet | Phase 1D |
| Reference gaps still in `klemma.db` | `klemma suggest` unaffected | Phase 1D |
| Migrated papers use citekey-based dedup | Same paper under different citekeys → two entries in `library.db` | Re-process with `--force` |
| `inherit_db` still works | Existing parent-child DB chains unaffected | Removed in Phase 1D |

In other words: **after migration, all existing commands work exactly as before**. The split databases are populated in the background and become the authoritative source of truth after Phase 1D.

---

## Troubleshooting

**`No monolithic DB found`**

The project doesn't have a `klemma.db` yet (e.g. newly initialized project). Nothing to migrate — the three-tier stores will be populated automatically as you run `klemma process`.

**`klemma status` shows fewer sources after migration**

This means `klemma status` is reading `project.db` instead of `klemma.db`. Check your klemma version — in Phase 1C, `klemma status` should still read from `klemma.db`. File an issue if you're on v0.14 and see this.

**`klemma process` still calls Claude after migration**

Expected in Phase 1C. Full dedup (skip-if-in-library.db) is active only for papers processed *after* the dual-write was merged (v0.13+). Migrated papers from `klemma.db` get synthetic hashes — re-run `klemma process --force <citekey>` to register the real SHA256 hash and get future dedup.

**`migrate-library` fails mid-run**

The backup at `klemma.db.bak` is intact. Delete the partially-written `project.db` and `library.db` entries, then re-run `--apply`. The operation is idempotent for papers (upsert) but fragment counts may be off — check with `klemma status`.

---

## What comes next (Phase 1D)

After Phase 1D merges, StateManager will become a facade over the three stores. At that point:

- `klemma status` reads from `project.db` (authoritative section coverage)
- `klemma suggest` reads reference gaps from `project.db`
- `inherit_db` is removed (shared `library.db` replaces it)
- Monolithic `klemma.db` can be archived or deleted

No additional migration step will be required — Phase 1D is a code-only change. Your `library.db` and `project.db` from this migration will be picked up automatically.
