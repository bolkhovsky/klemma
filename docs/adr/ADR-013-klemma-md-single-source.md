# ADR-013: KLEMMA.md as Single Source of Truth for Project Content

**Status**: Accepted
**Date**: 2026-03-08
**Context**: Project content data was split across multiple files with no single authoritative source

## Problem

Project content (title, chapters, scientific results, keywords, section types) was scattered across:

| File | Fields |
|------|--------|
| `config.yaml project:` | title, type, chapters, scientific_results, priority_terms, section_type_map, chapter_mapping |
| `KLEMMA.md` body | prose description only (no structured data) |
| `Outline_*.md` | section descriptions, chapter overviews |

This created three problems:

1. **No single source of truth** — AI commands read `config.yaml` for chapters but `Outline_*.md` for descriptions. Updates required editing multiple files.
2. **Draft context gaps** — `klemma draft -s X.X` started cold: no previous section ending for continuity, no section/chapter descriptions, no structured scientific contributions.
3. **Fragile outline coupling** — `Outline_*.md` was parsed with brittle regex; saving a new outline created a second file without cleaning up the old one.

## Decision

**KLEMMA.md YAML frontmatter is the authoritative source for all project content fields.**

### File Responsibilities

| File | Responsibility |
|------|----------------|
| `KLEMMA.md` frontmatter | All content fields: type, title, chapters, scientific_results, priority_terms, section_type_map, chapter_mapping, deadlines, writing_constraints |
| `KLEMMA.md` body | Human-readable project context (prose for AI), `## Outline` section (detailed section descriptions), `## Notes` (user feedback) |
| `config.yaml` | Infrastructure only: ai, zotero, obsidian, embeddings, search, state, tags, instance, export |
| `Outline_*.md` | Legacy fallback only — no new writes |

### Priority Order

When resolving `ProjectConfig`:
1. **KLEMMA.md frontmatter** (authoritative)
2. `config.yaml project:` section (deprecated, backward compat)
3. `config.yaml dissertation:` section (deprecated, backward compat)
4. Defaults

### Frontmatter Format

```yaml
---
type: paper
title: "TITDS-XV-2025: AI-based validation..."
current_focus: "3.1"
chapters:
  1: Introduction
  2: Related Work
  3: Validation Methodology
scientific_results:
  nr1: "Multi-criteria validation framework..."
  nr2: "Empirical demonstration..."
priority_terms: [sea ice forecasting, neural networks, validation]
section_type_map:
  "1": introduction
  "2": literature_review
  "3": methodology
deadlines: []
writing_constraints: ""
---

# Project Context

...prose description for AI commands...

## Outline

## 1. Introduction

Overview of chapter 1.

### 1.1. Background

This section establishes the theoretical foundations...
```

### Backward Compatibility

- Unmigrated projects (content in `config.yaml`) continue to work via fallback in `resolve_effective_config()`
- A deprecation `warnings.warn()` is emitted when content fields are found in `config.yaml` without KLEMMA.md frontmatter
- `klemma migrate-content` (hidden CLI command) moves content fields from `config.yaml` to KLEMMA.md frontmatter

### AI Commands See Only Prose

`load_project_context()` strips YAML frontmatter before passing KLEMMA.md to AI commands. AI sees only the markdown body — no YAML leaks into prompts.

### Outline Writes to KLEMMA.md

`klemma outline` saves to KLEMMA.md `## Outline` section (replacing it), updates frontmatter chapters/scientific_results from `OutlineResult`. No new `Outline_*.md` files are created. Existing `Outline_*.md` files are kept for backward compat but not written to.

## Consequences

**Benefits:**
- Single file to edit for all project configuration
- `klemma draft` gains structured context: chapter description, section description, scientific contributions, previous section ending
- Outline saves atomically with frontmatter update
- Git history for KLEMMA.md captures both structure and content evolution

**Costs:**
- `klemma migrate-content` required for existing projects (one-time)
- `parse_klemma_md()` called on every config resolution (minor overhead, file is small)

## Implementation

- `config.py`: `parse_klemma_md()`, `save_klemma_md()`, modified `resolve_effective_config()`, `load_project_context()`, `update_project_config()`
- `setup.py`: `_build_klemma_md()` emits frontmatter, `_build_project_config()` strips content, `migrate_content_to_klemma_md()`
- `skills/context_loader.py`: `extract_previous_section_ending()`, `load_outline_context()`
- `skills/outliner.py`: `save_outline()` writes to KLEMMA.md
- `skills/drafter.py`: `generate_draft()` accepts `prev_ending`, `outline_context`
- `prompts/section_draft.md`: structured context block with chapter desc, section desc, contributions, prev ending
