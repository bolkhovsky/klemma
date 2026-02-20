# Literature Management

Zotero library access, PDF text extraction, Pydantic data models, and vault note generation.

## Modules

### models.py (202 lines)
All Pydantic models for the data layer:
- `ZoteroEntry`, `Author` — Zotero item representation (`year`, `authors_str`, `citation` properties)
- `DissertationRelevance` — chapter/section relevance scoring (NR1/NR2, 0-5)
- `Fragment`, `ExtractionResult` — extraction output (text, type, chapter, section, relevance, page)
- `DailyPlan` — daily briefing output
- `CitationEntry`, `ArgumentBlock`, `ResearchResult` — research briefing output
- `LibraryReport` — library analysis output
- `AnnotationResult`, `Quote` — AI annotation output

### pdf.py (202 lines)
`PDFExtractor` — PyMuPDF-based text extraction with BBT JSON integration.
- `extract()` — text with `[Page N]` markers, truncated to `max_chars`
- `find_pdf()` — 3-tier PDF finding (see data flows below)
- `load_pdf_lookup()` — citekey → pdf_path from BBT JSON
- `load_entry_lookup()` — citekey → `ZoteroEntry` from BBT JSON
- CamelCase splitting: `wagnerSeaiceInformation2020` → `[wagner, seaice, information, 2020]`

### zotero.py (205 lines)
`ZoteroLibrary` — pyzotero wrapper for Zotero API access.
- Read: `get_all_items()`, `get_item()`, `search()`
- Write (used by acquirer): `create_item()`, `create_attachment_record()` (metadata-only, no cloud upload)
- Citekey discovery: `extra` field `"Citation Key:"` → fallback to `item_key`

### note_factory.py (470 lines)
Vault note creation pipeline — largest module in the package:
1. `auto_classify()` — regex-based chapter/section/tag assignment from title+abstract
2. `annotate_source()` — AI annotation via `prompts/annotate.md` → JSON
3. `build_frontmatter()` — YAML frontmatter matching zobsidian format
4. `create_vault_note()` — renders structured note with frontmatter + sections
5. Reference gap extraction — bibliography cross-check against library

## Data flows

### PDF finding (3-tier)
1. `direct_path` from DB (`state.get_source()["pdf_path"]`)
2. BetterBibTeX JSON lookup (`library_json` → citekey → attachment path)
3. Fuzzy filename matching (exact citekey, title words + year, author in prefix)

### Vault note creation
Triggered by `klemma process <citekey>` when `@citekey.md` is missing.
`note_factory.create_vault_note()` → AI annotation → structured note → reference gap extraction.

### Reference gap tracking
Each annotated paper's bibliography is cross-checked against the library.
- **Score formula**: `count × avg_source_quality × section_weight` (section_weight=2.0 for NR1/NR2 sections)
- **Auto-resolve**: when a gap's author+year matches a newly added source, it's marked resolved
- **Surfacing**: CLI status line, `klemma status`, TUI dashboard, TUI gaps screen

See: [Core infrastructure](../CLAUDE.md) for `state.py` tables | [AI Skills](../skills/CLAUDE.md) for extraction pipeline | [Prompts](../../../prompts/CLAUDE.md) for `annotate.md` template
