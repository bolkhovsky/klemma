"""Fragment extraction skill — extracts citation fragments from PDFs."""

import logging
from pathlib import Path
from typing import Optional

from ..ai import AIProvider
from ..config import KlemmaConfig, resolve_prompt
from ..literature.models import ExtractionResult, Fragment, ZoteroEntry
from ..literature.pdf import PDFExtractor
from ..state import StateManager
from ..vault import VaultAdapter

logger = logging.getLogger(__name__)


def extract_fragments(
    entry: ZoteroEntry,
    pdf_text: str,
    config: KlemmaConfig,
    state: StateManager,
    ai: AIProvider,
    dissertation_context: str = "",
    available_tags: list[str] | None = None,
    klemma_home: Optional[Path] = None,
) -> Optional[ExtractionResult]:
    """Extract citation fragments from a paper's PDF text."""

    prompt_path = resolve_prompt("extract.md", klemma_home) if klemma_home else Path(__file__).parent.parent.parent.parent / "prompts" / "extract.md"
    user_prompt = ai.render_prompt(
        prompt_path,
        title=entry.title or "Unknown",
        authors=entry.authors_str,
        year=entry.year or "Unknown",
        journal=entry.container_title or "N/A",
        doi=entry.DOI or "N/A",
        abstract=entry.abstract or "Not available",
        pdf_text=pdf_text,
        dissertation_context=dissertation_context,
        available_tags=", ".join(available_tags) if available_tags else "",
    )

    system = (
        "You are a research assistant extracting citation-worthy fragments from scientific papers. "
        "Output only valid JSON with fragments array."
    )

    data = ai.call_json(system, user_prompt, max_tokens=4096)
    if not data:
        logger.error("Failed to extract fragments for %s", entry.id)
        return None

    # Parse fragments
    fragments = []
    for f_data in data.get("fragments", []):
        try:
            fragment = Fragment(
                text=f_data.get("text", ""),
                type=f_data.get("type", "key_idea"),
                chapter=f_data.get("chapter"),
                section=f_data.get("section"),
                relevance=max(1, min(5, f_data.get("relevance", 3))),
                usage_hint=f_data.get("usage_hint", ""),
                page=f_data.get("page"),
            )
            if fragment.text:
                fragments.append(fragment)
        except Exception as e:
            logger.warning("Invalid fragment: %s", e)

    if not fragments:
        logger.warning("No valid fragments extracted for %s", entry.id)
        return None

    # Save to database
    fragment_dicts = [
        {
            "text": f.text,
            "type": f.type,
            "chapter": f.chapter,
            "section": f.section,
            "relevance": f.relevance,
            "usage_hint": f.usage_hint,
            "page": f.page,
        }
        for f in fragments
    ]
    saved = state.save_fragments(entry.id, fragment_dicts)
    logger.info("Saved %d fragments for %s", saved, entry.id)

    return ExtractionResult(
        source_id=entry.id,
        fragments=fragments,
        summary=data.get("summary", ""),
    )


def _format_fragments_for_vault(fragments: list[Fragment]) -> str:
    """Format fragments as Obsidian callouts matching zobsidian note style."""
    lines = []
    for f in sorted(fragments, key=lambda x: (-x.relevance, x.section or "")):
        page_str = f"Стр. {f.page}" if f.page else "—"
        section_str = f"Раздел {f.section}" if f.section else f"Глава {f.chapter}" if f.chapter else "—"
        stars = "\u2b50" * f.relevance
        lines.append(f"> [!quote] {page_str} | {section_str} | {f.type} | {stars}")
        lines.append(f"> \u00ab {f.text} \u00bb")
        if f.usage_hint:
            lines.append(f"> *{f.usage_hint}*")
        lines.append("")
    return "\n".join(lines).rstrip()


def save_fragments_to_vault(
    citekey: str,
    fragments: list[Fragment],
    vault: VaultAdapter,
    entry: Optional[ZoteroEntry] = None,
    config: Optional[KlemmaConfig] = None,
    state: Optional[StateManager] = None,
    pdf_text: Optional[str] = None,
    ai: Optional["AIProvider"] = None,
    entry_lookup: Optional[dict] = None,
    dissertation_context: str = "",
    available_tags: list[str] | None = None,
    klemma_home: Optional[Path] = None,
) -> Optional[str]:
    """Save extracted fragments to the @citekey note in vault.

    Updates the '## 💬 Цитаты для диссертации' section.
    If note doesn't exist and entry/config provided, auto-creates it
    (with AI annotation and reference analysis if pdf_text, ai, entry_lookup given).
    """
    note_name = f"@{citekey}"
    content = _format_fragments_for_vault(fragments)
    section_heading = "## \U0001f4ac Цитаты для диссертации"

    path = vault.update_section(note_name, section_heading, content)
    if path:
        logger.info("Фрагменты сохранены в vault: %s", path)
        return str(path)

    # Auto-create note if entry metadata available
    if entry and config:
        from ..literature.note_factory import create_vault_note
        logger.info("Заметка %s не найдена — создаю из метаданных", note_name)
        create_vault_note(
            citekey, entry, config, vault,
            state=state, pdf_text=pdf_text, ai=ai,
            entry_lookup=entry_lookup,
            dissertation_context=dissertation_context,
            available_tags=available_tags,
            klemma_home=klemma_home,
        )
        path = vault.update_section(note_name, section_heading, content)
        if path:
            logger.info("Фрагменты сохранены в новую заметку: %s", path)
            return str(path)

    logger.warning("Заметка %s не найдена в vault — фрагменты не сохранены", note_name)
    return None


def extract_from_citekey(
    citekey: str,
    config: KlemmaConfig,
    state: StateManager,
    ai: AIProvider,
    pdf_extractor: PDFExtractor,
    pdf_search_paths: list[Path],
    pdf_lookup: Optional[dict[str, str]] = None,
    entry_lookup: Optional[dict[str, ZoteroEntry]] = None,
    dissertation_context: str = "",
    available_tags: list[str] | None = None,
    klemma_home: Optional[Path] = None,
) -> Optional[ExtractionResult]:
    """Full extraction pipeline: find source, get PDF, extract fragments."""

    # Check if source exists in state
    source = state.get_source(citekey)
    if not source:
        logger.error("Source %s not found in database", citekey)
        return None

    # Get rich entry from lookup or build minimal one
    entry = (entry_lookup or {}).get(citekey) or ZoteroEntry(id=citekey, title=citekey)

    # Try to find PDF
    pdf_path = pdf_extractor.find_pdf(
        citekey,
        pdf_search_paths,
        entry_title=entry.title or "",
        direct_path=source.get("pdf_path") or entry.pdf_path,
        pdf_lookup=pdf_lookup,
    )

    if not pdf_path:
        logger.error("PDF not found for %s", citekey)
        return None

    pdf_text = pdf_extractor.extract(pdf_path)
    if not pdf_text or len(pdf_text) < config.processing.min_pdf_length:
        logger.error("PDF text too short or extraction failed for %s", citekey)
        return None

    return extract_fragments(
        entry, pdf_text, config, state, ai,
        dissertation_context=dissertation_context,
        available_tags=available_tags,
        klemma_home=klemma_home,
    )
