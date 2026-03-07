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
| `librarian_prune.md` | `skills/librarian.py` | Prune recommendation generation |
| `agent.md` | `skills/agent.py` | System prompt for interactive agent |
| `outline.md` | `skills/outliner.py` | Project outline generation |
| `outline_incremental.md` | `skills/outliner.py` | Incremental outline update |
| `section_draft.md` | `skills/drafter.py` | Section draft generation from research context |
| `analyst.md` | `evaluation/reconstruction.py` | Extract ground truth citation map from paper PDF |
| `reconstruct.md` | `evaluation/reconstruction.py` | AI citation recommendation (blind to paper text) |

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

### librarian_prune.md
`source_count`, `sources_compact`

### agent.md
`dissertation_context`, `chapters`, `scientific_results`, `priority_terms`, `current_chapter`, `chapter_name`, `current_section`, `current_deadline`, `days_until_deadline`, `sources`, `coverage`, `gaps`, `min_sources`, `fragment_stats`, `today_plan`, `next_reading`, `vault_path`, `today`, `project_root`, `outline_content`, `report_index` (list of {name, size}), `project_file_list` (list of {name, size}), `relevant_fragments` (list of fragment dicts with citekey, fragment_text, citation_intent, similarity — populated via RAG when embeddings+query available)

### outline.md
`project_type`, `dissertation_context`, `project_files` (list of {name, path, size, content_preview}), `library_summary`, `custom_prompt`, `language`

### outline_incremental.md
`project_type`, `dissertation_context`, `project_files`, `library_summary`, `previous_outline`, `user_notes`, `previous_date`, `custom_prompt`, `language`

### section_draft.md
`dissertation_context`, `section`, `chapter_num`, `chapter_name`, `research_report` (full research briefing text), `existing_draft` (current section text — expand, don't rewrite), `fragments` (list of relevant fragment dicts), `source_summaries` (list of source metadata dicts with citekey, quality, priority, summary), `language`

### analyst.md
`pdf_text`, `library_entries`, `paper_citekey`, `paper_title`

### reconstruct.md
`paper_title`, `abstract`, `keywords`, `sections` (list of {section_id, title, description}), `sources` (list of {citekey, title, year, abstract, fragments: [{intent, text}]}), `max_recs_per_section` (int, optional — caps recommendations per section), `examples` (list of {section_id, section_title, citekey, intent, justification}, optional — few-shot golden examples for ablation)

## Methodology grounding

Templates encode domain-specific academic writing knowledge from peer-reviewed sources:

| Methodology | Source | Used in templates |
|-------------|--------|-------------------|
| Results-first writing order | Kallestinova (2011) | `outline.md`, `outline_incremental.md`, `research.md`, `research_incremental.md` |
| CARS model (3 moves) | Swales (1990) | `outline.md`, `outline_incremental.md`, `section_draft.md`, `research.md`, `research_incremental.md` |
| Argument-grouped lit review | Turbek et al. (2016) | `outline.md`, `outline_incremental.md`, `section_draft.md`, `research.md`, `research_incremental.md` |
| Iterative drafting | Shrestha (2018) | `section_draft.md` |
| Citation function taxonomy | Teufel et al. (2006) | `extract.md`, `annotate.md`, `analyst.md`, `reconstruct.md` |

### Citation intent types (6 values)
`background`, `method`, `result_comparison`, `extends`, `contrasts`, `uses_data` — used across extraction, annotation, analysis, and recommendation prompts. Intent-weighted scoring in `gaps.py` assigns higher weights to `method` (3.0) and `extends` (2.5).

## Conventions
- All templates in Russian (dissertation language) except `extract.md` (English for international papers)
- `{{ variable }}` syntax with Jinja2 control flow (`{% for %}`, `{% if %}`)
- Rendered by `AIProviderBase.render_prompt()` which loads file and applies `Template()`

## Maintaining this file
Update when: adding a new prompt template (add to table + variables section), changing template variables (update the variables list), or changing which skill uses a template. The template table and variable lists must stay in sync with the actual `.md` files in this directory.

See: [AI Skills](../src/klemma/skills/CLAUDE.md) for the skill that uses each template
