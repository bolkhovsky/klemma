# Prompt Templates

Jinja2 templates rendered by skills via `ai.render_prompt()`. Each template receives specific context variables.

## Templates

| Template | Used by | Purpose |
|----------|---------|---------|
| `morning.md` | `skills/planner.py` | Daily briefing |
| `extract.md` | `skills/extractor.py` | Fragment extraction |
| `annotate.md` | `literature/note_factory.py` | AI annotation for vault notes |
| `research.md` | `skills/researcher.py` | Research briefing (initial) |
| `research_incremental.md` | `skills/researcher.py` | Research briefing (incremental) |
| `librarian.md` | `skills/librarian.py` | Library analysis (3 modes) |
| `agent.md` | `skills/agent.py` | System prompt for interactive agent |

## Key variables by template

### morning.md
`dissertation_context`, `current_chapter`, `chapter_name`, `current_deadline`, `days_until_deadline`, `days_without_progress`, `streak`, `yesterday_plan`, `chapter_plan`, `library_digest`, `coverage`, `gaps`, `ref_gaps`

### extract.md
`title`, `authors`, `year`, `journal`, `doi`, `abstract`, `pdf_text`, `dissertation_context`, `available_tags`

### annotate.md
`title`, `authors`, `year`, `journal`, `abstract`, `pdf_text`, `library_context`, `dissertation_context`, `available_tags`

### research.md / research_incremental.md
`section`, `chapter`, `section_title`, `dissertation_context`, `draft_text`, `fragments`, `sources`, `coverage` + (incremental: `user_notes`, `delta_sources`, `delta_fragments`, `previous_analysis`)

### librarian.md
`mode`, `dissertation_context`, `library_summary`, `quality_tiers`, `ref_gaps`, `sources_compact`, `focus_section`, `deadline`, `days_until_deadline`

### agent.md
`dissertation_context`, `chapters`, `scientific_results`, `priority_terms`, `current_chapter`, `chapter_name`, `current_section`, `current_deadline`, `days_until_deadline`, `sources`, `coverage`, `gaps`, `min_sources`, `fragment_stats`, `today_plan`, `next_reading`, `vault_path`, `today`

## Conventions
- All templates in Russian (dissertation language) except `extract.md` (English for international papers)
- `{{ variable }}` syntax with Jinja2 control flow (`{% for %}`, `{% if %}`)
- Rendered by `AIProviderBase.render_prompt()` which loads file and applies `Template()`

See: [AI Skills](../src/klemma/skills/CLAUDE.md) for the skill that uses each template
