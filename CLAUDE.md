## Feature exploration

When asked to "walk me through", "explain", "check", or "review" a feature — always start by fetching the relevant GitHub issues and PRs before reading any code:
1. `gh issue list --search "<feature name>"` to find the issue(s)
2. `gh pr list --search "<feature name>"` to find related PRs
3. `gh issue view <N>` / `gh pr view <N>` for full context, acceptance criteria, and discussion

Only after reviewing GH context should you explore code or files.

## Automatic PM & CTO reviews

Before creating a PR or GitHub issue for any feature/epic, **automatically** run both reviews without being asked:
1. `/pm-review` — product fit, scope, priority, core loop alignment
2. `/cto-review` — architecture, red lines, dependency direction, SaaS readiness

Present both reviews to the user and wait for their decision before proceeding. This applies to all features and epics — skip only for trivial bugfixes and typo-level changes.

## Feature development workflow

Every epic/feature follows this sequence. Do not skip or reorder steps. Skip this workflow for minors/bugfixes/refactoring.

1. **Spec** — read the epic issue and acceptance criteria on GitHub (`gh issue view <N>`)
2. **Review** — run `/pm-review` and `/cto-review` on the spec; wait for user decision
3. **Plan** — invoke the architect skill for detailed design; wait for approval before coding
4. **Code** — implement the feature
5. **Verify** — `ruff check src/ tests/` then `python -m pytest tests/ -q`; fix until both pass
6. **Docs** — update all affected `CLAUDE.md` files, `README.md`, and user guide in `docs/`
7. **Commit & PR** — atomic commit, then `gh pr create`. Link the PR to its epic by including `Part of #N` in the PR body. After creating the PR, tick off the completed task checkboxes in the epic issue body (`gh issue edit <N> --body "..."` with updated `- [x]` items). The PR body must also include a **Release Note** mini-article (~300 words) with four sections:
   ```
   ## Release Note

   ### Problem
   What gap or limitation this change addresses. Why it matters for the paper/tool.

   ### Academic Foundation
   Which papers from klemma-paper library justify the design decisions.
   Cite specific authors, years, and key findings that informed the approach.

   ### Implementation
   What was built: modules, commands, key design patterns.
   Reference specific files and architectural choices.

   ### Results
   Quantitative outcomes: test counts, LOC, lint status, measurable improvements.
   ```
7a. **Version bump** — after merging a PR, trigger the `Bump version` GitHub Actions
    workflow (Actions → Bump version → Run workflow). Pick `patch` for bug fixes,
    `minor` for new features, `major` for breaking changes.
    Both `pyproject.toml` and `src/klemma/__init__.py` are updated automatically.
    Never edit version numbers by hand.
8. **Paper draft** — export the Release Note into `~/research/klemma-paper/sections/` as a section draft. Russian academic style, `[@citekey]` references, matching existing sections format. File name: `section_N_<topic>.md` where N maps to the paper outline section. Add any missing BibTeX entries to `~/research/klemma-paper/references.bib`. Also create a results file in `~/research/klemma-paper/results/` with frontmatter `step`, `date`, `paper_sections` and sections: Baseline / Implementation / Results / Delta / Paper Section.

## Maintaining CLAUDE.md documentation

This documentation is a modular knowledge graph — 9 interconnected CLAUDE.md files loaded incrementally as the agent navigates directories. **Keep it up to date when changing code.**

### When to update
- **Adding a module**: add entry to the parent directory's CLAUDE.md (module name, line count, purpose, key functions)
- **Adding a CLI command**: update "Key commands" section here + relevant skill/tool CLAUDE.md
- **Adding a SQLite table**: update "SQLite tables" here + `src/klemma/CLAUDE.md` state.py section
- **Adding a prompt template**: update `prompts/CLAUDE.md` (template table + variables) + skill's CLAUDE.md
- **Adding a data flow**: document in the primary owner's CLAUDE.md, add cross-references
- **Renaming/removing a module**: update the CLAUDE.md where it's documented, fix any cross-reference links
- **Changing function signatures or key behavior**: update the relevant module entry

### When to create a new CLAUDE.md
Create a new child CLAUDE.md when a **new subdirectory** is added that contains 2+ modules with shared context. Follow this template:

```markdown
# <Subsystem Name>

<One-line purpose.>

## Modules

### module.py (N lines)
<Purpose.>
- `key_function()` — what it does
- `KeyClass` — what it represents

## Data flows

### <Flow name>
<Step-by-step description of the end-to-end flow.>

## Maintaining this file
Update when modules are added/removed/renamed in this directory, or when key functions/classes change.

See: [links to related CLAUDE.md files]
```

After creating a new child, add a link to the **Module documentation** section above.

### Structure rules
- **Primary owner**: each data flow is documented fully in one CLAUDE.md, other files only link to it
- **Line counts**: listed as `(N lines)` next to module names — update after significant changes
- **Cross-references**: every child ends with `See:` links to related CLAUDE.md files; use relative paths
- **Self-contained**: each child should be understandable without reading the root
- **Concise**: document what an agent needs to navigate and modify code, not exhaustive API docs
