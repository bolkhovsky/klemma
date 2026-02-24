---
name: github-product-backlog
description: Use when creating GitHub issues, organizing the backlog, assigning milestones, or managing issue status for the klemma project
---

# Klemma GitHub Product Backlog

## Overview

Track Product Backlog Items (PBIs) using GitHub Issues + Labels + Milestones on `bolkhovsky/klemma`. Each PBI is a GitHub Issue following the user story template. Milestones map to epics, not sprints.

## Issue Creation Flow

Every feature, bug, or task follows this pipeline:

```
Idea/Problem → GitHub Issue (this skill) → Feature Workflow (CLAUDE.md)
```

1. **Draft** — write User Story + Acceptance Criteria + files-to-modify
2. **Label** — type (`feature`/`bug`/`task`/`spike`) + priority + optional track
3. **Milestone** — assign to the relevant Epic (A–E)
4. **Create** — `gh issue create` with structured body
5. **Work begins** — follow CLAUDE.md feature workflow: Spec → Plan → Code → Verify → Docs → PR → Paper → Cross-check → Blog

## Issue Template

Every PBI body follows this format:

```markdown
**User Story**
As a [user type], I need [feature or functionality] so that [reason/benefit].

**Current State**
Brief description of how things work now (optional, useful for improvements).

**Desired State**
What the end result should look like (code blocks, directory trees welcome).

**Acceptance Criteria**
- [ ] Criterion 1
- [ ] Criterion 2
- [ ] Criterion 3

**Files to Modify**
- `src/klemma/module.py` — what changes
- `tests/test_module.py` — new tests needed

**Additional Notes**
- Links to plan files, related issues, or design docs
```

## Label Taxonomy (already created in repo)

| Category | Labels | Notes |
|----------|--------|-------|
| **Type** | `feature`, `bug`, `task`, `spike` | Every issue gets exactly one |
| **Priority** | `priority-high`, `priority-low`, `priority-medium` | |
| **Status** | `ready-for-dev`, `in-progress`, `blocked`, `done` | Update as work progresses |
| **Track** | `track-paper-results`, `track-semantic-search`, `track-rag`, `track-reliability`, `track-advanced`, `track-deferred` | Research paper alignment |
| **Other** | `tech-debt`, `documentation`, `duplicate`, `enhancement` | |

Do NOT create new labels — use existing ones. All labels are already in the repo.

## Milestones (Epic-based)

| Milestone | Focus |
|-----------|-------|
| **Epic-A** | Semantic Search Completion |
| **Epic-B** | Fragment RAG |
| **Epic-C** | Critical Reliability Debt |
| **Epic-D** | Advanced Features (Stretch) |
| **Epic-E** | Deferred Expansion |

Assign issues to milestones by epic relevance. Not all issues need a milestone — standalone improvements or bugs can exist without one.

```bash
# Assign to milestone
gh issue edit <N> --milestone "Epic-A"

# View milestone progress
gh api repos/bolkhovsky/klemma/milestones --jq '.[] | {title, open_issues, closed_issues}'
```

## Creating an Issue

```bash
gh issue create \
  --title "Short imperative title" \
  --label "feature,priority-medium" \
  --milestone "Epic-A" \
  --body "$(cat <<'EOF'
**User Story**
As a klemma user, I need ... so that ...

**Acceptance Criteria**
- [ ] ...
- [ ] ...
- [ ] Lint + all tests pass

**Files to Modify**
- `src/klemma/...` — ...

**Additional Notes**
- ...
EOF
)"
```

## Linking PRs to Issues

PR descriptions must reference the epic or parent issue:

```markdown
Part of #N
```

Or for direct closure:

```markdown
Fixes #N
```

After creating the PR, tick off completed checkboxes in the parent issue body:
```bash
# Update issue body with checked items
gh issue view <N> --json body -q .body  # read current body
# Edit the body with [x] marks
gh issue edit <N> --body "..."
```

## Status Management

```bash
# Start work
gh issue edit <N> --add-label "in-progress" --remove-label "ready-for-dev"

# Block
gh issue edit <N> --add-label "blocked" --remove-label "in-progress"

# Complete (after PR merged)
gh issue edit <N> --add-label "done" --remove-label "in-progress"

# View backlog
gh issue list --label "ready-for-dev"
gh issue list --milestone "Epic-A" --state all
```

## Worked Example: Issue #34

Created for the "organize reports into subdirectories" feature:

```bash
gh issue create \
  --title "Organize generated reports into reports/ subdirectories" \
  --label "feature" \
  --body "$(cat <<'EOF'
**User Story**
As a klemma user, I need generated reports (Library_*, Research_*, Agent_*)
organized into subdirectories instead of cluttering project root, so that
the working directory stays clean and navigable.

**Current State**
All klemma-generated files dump into `project_root/`: Agent_*, Library_*,
Research_* mixed with project files.

**Desired State**
reports/library/, reports/research/, reports/agents/ with auto-generated
AGENTS.md index.

**Acceptance Criteria**
- [ ] klemma library saves to reports/library/
- [ ] klemma research saves to reports/research/
- [ ] Agent output goes to reports/agents/
- [ ] reports/AGENTS.md auto-generated index
- [ ] Backward compat for existing projects
- [ ] Lint + all tests pass

**Files to Modify**
- src/klemma/skills/librarian.py
- src/klemma/skills/researcher.py
- src/klemma/skills/agent.py
- prompts/agent.md
EOF
)"
```

Result: https://github.com/bolkhovsky/klemma/issues/34

## Quick Reference

```bash
# Create issue
gh issue create --title "..." --label "feature,priority-medium" --milestone "Epic-A"

# List backlog
gh issue list --label "ready-for-dev"

# List by epic
gh issue list --milestone "Epic-A" --state all

# Move status
gh issue edit <N> --add-label "in-progress" --remove-label "ready-for-dev"

# View issue
gh issue view <N>

# Close milestone
gh api repos/bolkhovsky/klemma/milestones/<ID> --method PATCH --field state=closed
```

## Common Mistakes

| Mistake | Fix |
|---------|-----|
| No acceptance criteria | Don't move to `ready-for-dev` until criteria are defined |
| Missing `Part of #N` in PR | Won't track progress against parent issue |
| Wrong milestone | Epics are thematic (A–E), not temporal |
| Forgetting `--label` at creation | Apply type + priority at creation time |
| Not ticking checkboxes after PR | Update parent issue body with `[x]` items |
