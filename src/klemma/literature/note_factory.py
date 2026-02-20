"""Auto-create @citekey vault notes from BetterBibTeX metadata."""

import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

from ..ai import AIProvider
from ..config import KlemmaConfig, resolve_prompt
from ..state import StateManager
from ..vault import VaultAdapter
from .models import ZoteroEntry

logger = logging.getLogger(__name__)


def auto_classify(entry: ZoteroEntry, config: KlemmaConfig) -> dict:
    """Determine chapter/section/tags from title+abstract via regex patterns.

    Uses config.dissertation.chapter_mapping and config.tags.auto_mapping.
    Returns {"chapter": int|None, "section": str|None, "chapters": [], "sections": [], "tags": []}.
    """
    text = " ".join(filter(None, [entry.title, entry.abstract])).lower()
    chapter = None
    section = None
    chapters: set[int] = set()
    sections: set[str] = set()
    tags: list[str] = []

    # Chapter/section mapping
    for mapping in config.dissertation.chapter_mapping:
        if re.search(mapping.pattern, text, re.IGNORECASE):
            if chapter is None:
                chapter = mapping.chapter
                section = mapping.section
            chapters.add(mapping.chapter)
            sections.add(mapping.section)

    # Tag mapping
    seen_tags: set[str] = set()
    for mapping in config.tags.auto_mapping:
        if re.search(mapping.pattern, text, re.IGNORECASE):
            if mapping.tag not in seen_tags:
                tags.append(mapping.tag)
                seen_tags.add(mapping.tag)

    return {
        "chapter": chapter or 1,
        "section": section or "1.1",
        "chapters": sorted(chapters) if chapters else [chapter or 1],
        "sections": sorted(sections) if sections else [section or "1.1"],
        "tags": tags,
    }


def _format_library_entries(entry_lookup: Optional[dict] = None) -> str:
    """Format library entries as compact list for prompt injection."""
    if not entry_lookup:
        return "Список не предоставлен"
    lines = []
    for citekey, e in sorted(entry_lookup.items()):
        year = e.year or "n.d."
        title = (e.title or "")[:80]
        lines.append(f"- {citekey}: {e.authors_str}, {year}. {title}")
    return "\n".join(lines)


def annotate_source(
    entry: ZoteroEntry,
    pdf_text: str,
    config: KlemmaConfig,
    ai: AIProvider,
    entry_lookup: Optional[dict] = None,
    dissertation_context: str = "",
    available_tags: list[str] | None = None,
    klemma_home: Optional[Path] = None,
) -> Optional[dict]:
    """Generate AI annotation (summary, methodology, key findings, relevance).

    Returns dict with keys: summary, methodology, key_findings,
    relevance_to_dissertation, quality_score, citation_priority,
    dissertation_relevance, chapters, sections, suggested_tags,
    key_references.
    Returns None on failure.
    """
    prompt_path = resolve_prompt("annotate.md", klemma_home) if klemma_home else Path(__file__).parent.parent.parent.parent / "prompts" / "annotate.md"
    user_prompt = ai.render_prompt(
        prompt_path,
        title=entry.title or "Unknown",
        authors=entry.authors_str,
        year=entry.year or "Unknown",
        journal=entry.container_title or "N/A",
        doi=entry.DOI or "N/A",
        paper_language=entry.language or "Unknown",
        abstract=entry.abstract or "Not available",
        pdf_text=pdf_text,
        dissertation_context=dissertation_context,
        available_tags=", ".join(available_tags) if available_tags else "",
        library_entries=_format_library_entries(entry_lookup),
        language=config.ai.language,
    )

    system = (
        "You are a research assistant annotating scientific papers for a PhD dissertation. "
        "Output only valid JSON."
    )

    data = ai.call_json(system, user_prompt, max_tokens=4096)
    if not data:
        logger.warning("Annotation failed for %s", entry.id)
        return None

    logger.info("Annotation generated for %s", entry.id)
    return data


def build_frontmatter(entry: ZoteroEntry, classification: dict) -> str:
    """Build YAML frontmatter matching zobsidian format."""
    lines = ["---"]

    lines.append(f'citekey: "{entry.id}"')
    if entry.title:
        title = entry.title.replace('"', '\\"')
        lines.append(f'title: "{title}"')
    lines.append(f'author: "{entry.authors_str}"')
    if entry.year:
        lines.append(f"year: {entry.year}")
    lines.append(f'type: "{entry.type}"')

    if entry.container_title:
        journal = entry.container_title.replace('"', '\\"')
        lines.append(f'journal: "{journal}"')
    if entry.volume:
        lines.append(f'volume: "{entry.volume}"')
    if entry.issue:
        lines.append(f'issue: "{entry.issue}"')

    if entry.DOI:
        lines.append(f'doi: "{entry.DOI}"')
    if entry.URL:
        lines.append(f'url: "{entry.URL}"')

    lines.append("quality: 3")
    lines.append('priority: "medium"')

    ch = classification.get("chapter")
    sec = classification.get("section")
    if ch:
        lines.append(f"chapter: {ch}")
    if sec:
        lines.append(f'section: "{sec}"')

    chapters = classification.get("chapters", [])
    if chapters:
        lines.append("chapters:")
        for c in chapters:
            lines.append(f"  - {c}")

    sections = classification.get("sections", [])
    if sections:
        lines.append("sections:")
        for s in sections:
            lines.append(f'  - "{s}"')

    lines.append("relevance_nr1: 0")
    lines.append("relevance_nr2: 0")

    tags = classification.get("tags", [])
    if tags:
        lines.append("tags:")
        for tag in tags:
            lines.append(f'  - "{tag}"')
        lines.append('  - "source/zotero"')
        lines.append("topics:")
        for tag in tags:
            lines.append(f'  - "[[{tag}]]"')

    lines.append("read_start: ")
    lines.append("read_end: ")
    lines.append("---")
    return "\n".join(lines)


def render_note_body(entry: ZoteroEntry, annotation: Optional[dict] = None) -> str:
    """Render note body matching zobsidian template.

    If annotation dict is provided (from annotate_source()), fills sections
    with AI-generated content. Otherwise uses stubs.
    """
    abstract = entry.abstract or "*Abstract not available in metadata*"
    doi_line = f"- DOI: [{entry.DOI}](https://doi.org/{entry.DOI})" if entry.DOI else ""
    url_line = f"- Web: [{entry.URL}]({entry.URL})" if entry.URL else ""

    links_block = f"> [!link]+ Links\n> - Zotero: [Open in Zotero](zotero://select/items/@{entry.id})"
    if doi_line:
        links_block += f"\n> {doi_line}"
    if url_line:
        links_block += f"\n> {url_line}"

    # Content from annotation or stubs
    a = annotation or {}
    summary = a.get("summary", "*AI-аннотация не сгенерирована.*")
    methodology = a.get("methodology", "*Не заполнено*")
    key_findings = a.get("key_findings", [])
    relevance = a.get("relevance_to_dissertation", "*Не заполнено*")

    findings_text = "\n".join(f"- {f}" for f in key_findings) if key_findings else "*Не заполнено*"

    # Место в диссертации
    dr = a.get("dissertation_relevance", {})
    if dr:
        nr1 = dr.get("relevance_nr1", 0)
        nr2 = dr.get("relevance_nr2", 0)
        rationale = dr.get("rationale", "")
        place_text = (
            f"| Параметр | Значение |\n"
            f"|----------|----------|\n"
            f"| **Основная глава** | Глава {dr.get('primary_chapter', '?')} |\n"
            f"| **Раздел** | {dr.get('primary_section', '?')} |\n"
            f"| **Релевантность НР1** | {nr1}/5 (модель валидации ДЗЗ) |\n"
            f"| **Релевантность НР2** | {nr2}/5 (методика IIEE-декомпозиции) |\n"
            f"| **Приоритет цитирования** | {a.get('citation_priority', 'medium')} |"
        )
        if rationale:
            place_text += f"\n\n> [!note] Обоснование\n> {rationale}"
        chs = a.get("chapters", [])
        secs = a.get("sections", [])
        if chs:
            place_text += f"\n\n**Применимо к главам**: {', '.join(f'Глава {c}' for c in chs)}"
        if secs:
            place_text += f"\n\n**Разделы**: {', '.join(str(s) for s in secs)}"
    else:
        place_text = "*Авто-классифицировано по regex-паттернам. Проверьте и уточните вручную.*"

    # Related topics from annotation tags
    tags = a.get("suggested_tags", [])
    topics_text = "\n".join(f"- [[{t}]]" for t in tags) if tags else "*Не заполнено*"

    # Key references from bibliography
    key_refs = a.get("key_references", [])
    if key_refs:
        in_lib = [r for r in key_refs if r.get("in_library")]
        missing = [r for r in key_refs if not r.get("in_library")]
        refs_parts = []
        if in_lib:
            refs_parts.append("> [!success] В нашей библиотеке")
            for r in in_lib:
                ck = r.get("citekey", "")
                why = r.get("why_relevant", "")
                refs_parts.append(f"> - [[@{ck}]] — *{why}*")
        if missing:
            if refs_parts:
                refs_parts.append("")
            refs_parts.append("> [!warning] Отсутствуют — кандидаты на добавление")
            for r in missing:
                auth = r.get("authors", "?")
                year = r.get("year", "")
                title = r.get("title", "")[:60]
                why = r.get("why_relevant", "")
                refs_parts.append(f'> - **{auth} ({year})**. "{title}" — *{why}*')
        refs_text = "\n".join(refs_parts)
    else:
        refs_text = "*Не проанализировано*"

    # Citation
    year = entry.year or "n.d."
    journal_part = f"*{entry.container_title}*" if entry.container_title else ""
    vol_part = f", {entry.volume}" if entry.volume else ""
    issue_part = f"({entry.issue})" if entry.issue else ""
    page_part = f", {entry.page}" if entry.page else ""
    doi_part = f". https://doi.org/{entry.DOI}" if entry.DOI else ""
    apa = (
        f"**APA**: {entry.authors_str} ({year}). {entry.title or 'Untitled'}. "
        f"{journal_part}{vol_part}{issue_part}{page_part}{doi_part}"
    )

    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    return f"""{links_block}

> [!abstract]+
> {abstract}

---

## \U0001f4dd AI Summary

{summary}

---

## \U0001f52c Methodology

{methodology}

---

## \U0001f3af Key Findings

{findings_text}

---

## \U0001f517 Relevance to My Research

> [!important] \u0421\u0432\u044f\u0437\u044c \u0441 \u0434\u0438\u0441\u0441\u0435\u0440\u0442\u0430\u0446\u0438\u0435\u0439
> {relevance}

---

## \U0001f4cd \u041c\u0435\u0441\u0442\u043e \u0432 \u0434\u0438\u0441\u0441\u0435\u0440\u0442\u0430\u0446\u0438\u0438

{place_text}

---

## \U0001f4ac \u0426\u0438\u0442\u0430\u0442\u044b \u0434\u043b\u044f \u0434\u0438\u0441\u0441\u0435\u0440\u0442\u0430\u0446\u0438\u0438

*\u0424\u0440\u0430\u0433\u043c\u0435\u043d\u0442\u044b \u0431\u0443\u0434\u0443\u0442 \u0434\u043e\u0431\u0430\u0432\u043b\u0435\u043d\u044b \u043f\u043e\u0441\u043b\u0435 \u044d\u043a\u0441\u0442\u0440\u0430\u043a\u0446\u0438\u0438.*

---

## \U0001f3f7\ufe0f Related Topics

{topics_text}

---

## \U0001f4da \u041a\u043b\u044e\u0447\u0435\u0432\u044b\u0435 \u0438\u0441\u0442\u043e\u0447\u043d\u0438\u043a\u0438 \u0438\u0437 \u0431\u0438\u0431\u043b\u0438\u043e\u0433\u0440\u0430\u0444\u0438\u0438

{refs_text}

---

## \u270f\ufe0f My Notes

*\u0414\u043e\u0431\u0430\u0432\u044c\u0442\u0435 \u0432\u0430\u0448\u0438 \u0437\u0430\u043c\u0435\u0442\u043a\u0438 \u043f\u043e\u0441\u043b\u0435 \u043f\u0440\u043e\u0447\u0442\u0435\u043d\u0438\u044f \u0441\u0442\u0430\u0442\u044c\u0438*




---

## Highlights

*\u041c\u0435\u0441\u0442\u043e \u0434\u043b\u044f \u0432\u044b\u0434\u0435\u043b\u0435\u043d\u0438\u0439 \u043f\u0440\u0438 \u0447\u0442\u0435\u043d\u0438\u0438 PDF*




---

## \U0001f4da Citation

{apa}

---
*Auto-generated: {now} | klemma note-factory*"""


def create_vault_note(
    citekey: str,
    entry: ZoteroEntry,
    config: KlemmaConfig,
    vault: VaultAdapter,
    state: Optional[StateManager] = None,
    pdf_text: Optional[str] = None,
    ai: Optional[AIProvider] = None,
    entry_lookup: Optional[dict] = None,
    dissertation_context: str = "",
    available_tags: list[str] | None = None,
    klemma_home: Optional[Path] = None,
) -> Path:
    """Create @citekey.md vault note from BetterBibTeX metadata.

    Auto-classifies chapter/section/tags via regex, builds frontmatter and
    body matching zobsidian format. If pdf_text and ai are provided, generates
    AI annotation (summary, methodology, key findings, relevance, key_references)
    to fill note sections. Otherwise uses stubs.
    """
    # 1. AI annotation (if pdf_text + ai available)
    annotation = None
    if pdf_text and ai:
        logger.info("Generating annotation for @%s...", citekey)
        annotation = annotate_source(
            entry, pdf_text, config, ai, entry_lookup=entry_lookup,
            dissertation_context=dissertation_context,
            available_tags=available_tags,
            klemma_home=klemma_home,
        )

    # 2. Classification: prefer AI result, fallback to regex
    if annotation and annotation.get("dissertation_relevance"):
        dr = annotation["dissertation_relevance"]
        classification = {
            "chapter": dr.get("primary_chapter", 1),
            "section": dr.get("primary_section", "1.1"),
            "chapters": annotation.get("chapters", [dr.get("primary_chapter", 1)]),
            "sections": annotation.get("sections", [dr.get("primary_section", "1.1")]),
            "tags": annotation.get("suggested_tags", []),
        }
        quality = annotation.get("quality_score", 3)
        priority = annotation.get("citation_priority", "medium")
        nr1 = dr.get("relevance_nr1", 0)
        nr2 = dr.get("relevance_nr2", 0)
    else:
        classification = auto_classify(entry, config)
        quality = 3
        priority = "medium"
        nr1 = 0
        nr2 = 0

    # 3. Build frontmatter (with AI-derived quality/priority if available)
    frontmatter = build_frontmatter(entry, classification)
    if annotation:
        # Patch quality and priority from annotation
        frontmatter = frontmatter.replace("quality: 3", f"quality: {quality}")
        frontmatter = frontmatter.replace('priority: "medium"', f'priority: "{priority}"')
        frontmatter = frontmatter.replace("relevance_nr1: 0", f"relevance_nr1: {nr1}")
        frontmatter = frontmatter.replace("relevance_nr2: 0", f"relevance_nr2: {nr2}")

    # 4. Render body
    body = render_note_body(entry, annotation=annotation)
    content = frontmatter + "\n\n" + body

    path = vault.create_note(
        f"@{citekey}", content, folder=config.obsidian.notes_folder
    )

    # 5. Register in DB
    if state is not None:
        state.register_sources([citekey])
        state.update_source_metadata(
            source_id=citekey,
            quality_score=quality,
            primary_chapter=classification["chapter"],
            primary_section=classification["section"],
            relevance_nr1=nr1,
            relevance_nr2=nr2,
            citation_priority=priority,
            note_path=str(path),
            status="completed",
        )
        sections = classification.get("sections", [])
        chapters = classification.get("chapters", [])
        if sections:
            state.set_source_sections(citekey, sections, chapters)

        # 6. Save reference gaps from annotation
        if annotation:
            missing_refs = [
                r for r in annotation.get("key_references", [])
                if not r.get("in_library")
            ]
            if missing_refs:
                state.save_reference_gaps(citekey, [
                    {
                        "ref_authors": r.get("authors", ""),
                        "ref_year": r.get("year"),
                        "ref_title": r.get("title", ""),
                        "why_relevant": r.get("why_relevant", ""),
                        "dissertation_sections": r.get("dissertation_sections", []),
                    }
                    for r in missing_refs
                ])
                logger.info("Saved %d reference gaps for @%s", len(missing_refs), citekey)

    logger.info("Created vault note: @%s.md → %s", citekey, path)
    return path
