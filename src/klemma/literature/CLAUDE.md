# Literature Management

PDF text extraction, Pydantic data models, vault note generation, and metadata auto-extraction.

## Modules

### metadata.py (~320 lines)
Auto-extract paper metadata from PDF properties + CrossRef lookup.
- `extract_pdf_metadata(pdf_path)` — PyMuPDF `doc.metadata` for title/author; first-page heuristic fallback for generic titles
- `_extract_abstract_from_text(text)` — regex-based abstract extraction from PDF text (markers: Abstract/Аннотация/Резюме); cap 2000 chars; empty string on no match or empty input
- `_extract_doi_from_text(text)` — regex DOI extraction from first 3000 chars; filters sentinel values (10.0000/*, 10.1000/*); arxiv DOIs kept
- `lookup_crossref(title, mailto?, timeout=10)` — CrossRef `/works` API with polite-pool `mailto`; fuzzy title match; `timeout` kwarg (use 5s in SaaS routes, 10s for CLI); JATS tags stripped from abstract
- `lookup_crossref_by_doi(doi, mailto?, timeout=10)` — CrossRef `/works/{doi}` exact lookup; 404 → None; no retry; same return format as `lookup_crossref`
- `lookup_s2(title)` — S2 `paper/search` API (kept for CLI acquirer; **not** called from `resolve_metadata` — S2 is rate-limited and unreliable under load)
- `resolve_metadata(pdf_path, cli_title?, cli_authors?, cli_year?, cli_doi?)` — orchestrator: CLI flags → PDF metadata → CrossRef enrichment → empty fallback. **CLI only** — not called from SaaS worker (CrossRef moved to user-triggered `enrich-metadata` endpoint)
- `_titles_match(query, candidate)` — fuzzy word-overlap comparison (>0.6 threshold)

### zotero_api.py (~80 lines)
Zotero local API integration via Connector + Better BibTeX JSON-RPC.
- `is_zotero_running()` — checks BBT `api.ready` on localhost:23119 (2s timeout)
- `create_zotero_item(title, authors_str, year, doi, abstract, pdf_path)` — `POST /connector/saveItems` with parsed creators + optional PDF attachment
- `get_bbt_citekey(title, retries=3)` — `POST /better-bibtex/json-rpc` → `item.search(title)` → citekey (retries with 1s delay)
- `_parse_authors(authors_str)` — splits "Smith J., Jones K." into Zotero creators array

### models.py (202 lines)
All Pydantic models for the data layer:
- `ZoteroEntry`, `Author` — Zotero item representation (`year`, `authors_str`, `citation` properties)
- `DissertationRelevance` — chapter/section relevance scoring (NR1/NR2, 0-5)
- `Fragment`, `ExtractionResult` — extraction output (text, type, chapter, section, relevance, page, `verbatim: bool`)
- `DowngradeStats` — verbatim validator counts (`verbatim_claimed`, `verbatim_confirmed`, `fuzzy_rescued`, `downgraded`); attached to `ExtractionResult.downgrade_stats` + SaaS job result
- `DailyPlan` — daily briefing output
- `CitationEntry`, `ArgumentBlock`, `ResearchResult` — research briefing output; `ResearchResult.required_missing: list[str]` — citekeys passed via `--require` that had no fragments in the target section
- `LibraryReport` — library analysis output
- `AnnotationResult`, `Quote` — AI annotation output

### pdf.py (202 lines)
`PDFExtractor` — PyMuPDF-based text extraction with BBT JSON integration.
- `extract()` — text with `[Page N]` markers, truncated to `max_chars` (fed to the AI extraction prompt)
- `extract_pages(pdf_path: Path) -> list[str]` — **public API, stable signature**. One cleaned string per page, no truncation, no inline `[Page N]` markers. Used by `write_pdf_sidecar()` and (planned) the semantic citation drift checker. Returns `[]` for missing files, `[""]` for an empty page.
- `find_pdf()` — 3-tier PDF finding (see data flows below)
- `load_pdf_lookup()` — citekey → pdf_path from BBT JSON
- `load_entry_lookup()` — citekey → `ZoteroEntry` from BBT JSON
- CamelCase splitting: `wagnerSeaiceInformation2020` → `[wagner, seaice, information, 2020]`

### sidecar.py (~230 lines)
Raw PDF text sidecar writer + reader — feynman-style format at `<project_root>/.klemma/pdfs/<citekey>.md`. Introduced in ADR-016 as the on-disk trace of processed PDFs and the primary-source passage store for downstream tooling. Written by `_process_single()` **immediately after text extraction, before the AI call** — the full text survives AI failure or zero-fragment extraction (claim-provenance substrate).
- `write_pdf_sidecar(project_root, citekey, pages, metadata) -> Path` — atomic write via `tempfile.mkstemp` + `os.fdopen` + `os.replace`; rejects `..`, `/`, `\\`, empty citekeys (pattern mirrors `LocalFileStore._file_path()`); idempotent (reprocessing overwrites cleanly); creates missing `.klemma/pdfs/` directory
- `SidecarDoc` — dataclass: `text` (canonical text) + `page_spans: list[(page, char_start, char_end)]` (half-open, trimmed to non-whitespace page content) + `page_for(offset) -> int | None`
- `load_sidecar_doc(project_root, citekey) -> SidecarDoc | None` — parses page markers into per-page character spans at read time (no extra storage). **HARD CONTRACT**: `load_sidecar_doc(...).text` is byte-for-byte equal to `read_pdf_sidecar(...)` — fragment/claim offsets are always in canonical text coordinates
- `read_pdf_sidecar(project_root, citekey) -> str | None` — delegates to `load_sidecar_doc`; canonical text = frontmatter stripped, each `\n<!-- Page N -->\n` marker replaced by a single `\n`, then `str.strip()`

**Format contracts** (must not drift without a version bump — the planned semantic citation drift checker is the second consumer):
1. **Path**: always `<project_root>/.klemma/pdfs/<citekey>.md`. No config override. Downstream consumers can hardcode.
2. **Page delimiter**: exactly `\n<!-- Page N -->\n` between pages, where `N = 2, 3, ...`. Page 1 has no marker — it starts right after the frontmatter `---`. Regex `\n<!-- Page (\d+) -->\n` is a stable split point.
3. **Frontmatter fields**: stable set is `Citekey`, `Authors`, `Year`, `DOI`, `Pages`, `Source`. Additions allowed (append-only). Renames/removals require a version bump note in the sidecar header.

Layout:
```markdown
# <title>

> Citekey: <citekey>
> Authors: <authors>
> Year: <year>
> DOI: <doi>
> Pages: <N>
> Source: <pdf_path>

---

<page 1 prose>

<!-- Page 2 -->

<page 2 prose>
```

### note_factory.py (470 lines)
Vault note creation pipeline — largest module in the package:
1. `auto_classify()` — regex-based chapter/section/tag assignment from title+abstract; returns `matched: bool` (True when any chapter_mapping pattern matched)
2. `annotate_source()` — AI annotation via `prompts/annotate.md` → JSON
3. `build_frontmatter()` — YAML frontmatter matching zobsidian format
4. `create_vault_note()` — renders structured note with frontmatter + sections
5. Reference gap extraction — bibliography cross-check against library

### draft_parser.py (~200 lines)
Parse structure and bibliography from draft PDFs for Klemma `--from-draft` onboarding (#76). Uses PyMuPDF.
- `DraftParseResult` — dataclass: title, sections, references, full_text, page_count
- `DetectedSection` — dataclass: heading, level (1-3), text, page_start
- `parse_draft_pdf(pdf_path) -> DraftParseResult` — extract title (font-size heuristic), numbered sections, bibliography entries
- `find_bibliography_section(text) -> tuple[int, int] | None` — char span of the bibliography content: starts after the marker line (EN/RU, plain or markdown heading), ends at the next markdown heading or EOF. Reused by `skills/reference_matcher.py` and the numbered-mode claim parser
- `_extract_bibliography(full_text)` — `find_bibliography_section()` + `reference_parser.parse_references()`

### reference_parser.py (~170 lines)
Parse bibliography strings into structured `ParsedReference` dataclass. Pure string processing, no AI, no external deps. Foundation for Klemma `--from-draft` onboarding (#76).
- `ParsedReference` — dataclass: raw, authors, year, title, journal, doi, url
- `parse_reference(raw) -> ParsedReference` — parse single entry (APA, numbered, DOI/URL extraction)
- `parse_references(text) -> list[ParsedReference]` — split bibliography section into entries, filter short
- `parse_numbered_references(text) -> list[tuple[int, ParsedReference]]` — like `parse_references()` but PRESERVES entry numbers ([1] / 1. / 1) markers, ≤3 digits so wrapped year lines don't split entries); feeds the `[N] → citekey` ref map for numbered manuscripts

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

## Maintaining this file
Update when: adding/changing Pydantic models in `models.py`, modifying PDF finding tiers, changing vault note structure in `note_factory.py`, or changing the reference gap scoring formula. If `annotate.md` variables change, also update [Prompts](../../../prompts/CLAUDE.md).

See: [Core infrastructure](../CLAUDE.md) for `state.py` tables | [AI Skills](../skills/CLAUDE.md) for extraction pipeline | [Prompts](../../../prompts/CLAUDE.md) for `annotate.md` template
