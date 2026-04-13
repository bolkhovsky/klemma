"""Section draft generation — general prose from research context."""

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from ..ai import AIProvider, resolve_task_model
from ..config import KlemmaConfig, resolve_prompt

logger = logging.getLogger(__name__)


@dataclass
class DraftResult:
    """Result of section draft generation."""

    section: str = ""
    chapter: int = 0
    text: str = ""
    word_count: int = 0
    citations_used: list[str] = field(default_factory=list)
    filtered_citekeys: list[str] = field(default_factory=list)
    research_report_used: bool = False


def _extract_citations(text: str) -> list[str]:
    """Extract all [@citekey] references from text."""
    return re.findall(r"\[@([\w\-]+)\]", text)


def _filter_hallucinated_citations(
    text: str,
    valid_ids: set[str],
) -> tuple[str, list[str]]:
    """Remove [@citekey] where citekey is not in valid set.

    Returns (cleaned_text, list_of_removed_citekeys).
    """
    removed = []

    def _replace(m: re.Match) -> str:
        citekey = m.group(1)
        if citekey in valid_ids:
            return m.group(0)
        removed.append(citekey)
        return ""

    cleaned = re.sub(r"\[@([\w\-]+)\]", _replace, text)
    # Clean up double spaces left by removals
    cleaned = re.sub(r"  +", " ", cleaned)
    return cleaned, sorted(set(removed))


def generate_draft(
    section: str,
    chapter: int,
    config: KlemmaConfig,
    ai: AIProvider,
    *,
    dissertation_context: str = "",
    klemma_home: Optional[Path] = None,
    project_chain: Optional[list] = None,
    research_report_content: str = "",
    existing_draft: str = "",
    source_summaries: Optional[list[dict]] = None,
    fragments: Optional[list[dict]] = None,
    rag_fragments: Optional[list[dict]] = None,
    valid_citekeys: Optional[set[str]] = None,
    section_title: str = "",
    custom_prompt: str = "",
    prev_ending: str = "",
    outline_context: Optional[dict] = None,
    candidate_sentences: Optional[list[dict]] = None,
) -> DraftResult:
    """Generate a section draft using AI.

    Pure skill — no file I/O. Receives all context as arguments,
    returns DraftResult. CLI handles save.
    """
    if source_summaries is None:
        source_summaries = []
    if fragments is None:
        fragments = []
    if rag_fragments is None:
        rag_fragments = []

    # Determine chapter name and section type from config
    chapter_name = ""
    if hasattr(config, "dissertation") and config.dissertation.chapters:
        chapter_name = config.dissertation.chapters.get(chapter, f"Chapter {chapter}")

    # Render prompt
    prompt_path = resolve_prompt(
        "section_draft.md",
        klemma_home,
        project_chain=project_chain,
    )
    # Build dissertation context title for structured prompt
    dissertation_context_title = ""
    if outline_context:
        dissertation_context_title = outline_context.get("title", "") or dissertation_context

    prompt_text = ai.render_prompt(
        prompt_path,
        dissertation_context=dissertation_context,
        dissertation_context_title=dissertation_context_title,
        section=section,
        chapter_num=chapter,
        chapter_name=chapter_name,
        research_report=research_report_content,
        existing_draft=existing_draft,
        fragments=fragments,
        rag_fragments=rag_fragments,
        source_summaries=source_summaries,
        section_title=section_title,
        custom_prompt=custom_prompt,
        language=config.ai.language,
        prev_ending=prev_ending,
        outline_context=outline_context or {},
        candidate_sentences=candidate_sentences or [],
    )

    system = (
        "Ты — научный консультант по написанию диссертаций. "
        "Генерируй текст раздела на основе исследовательского брифинга и фрагментов из библиотеки. "
        "Используй ТОЛЬКО цитаты [@citekey] из предоставленных source_summaries. "
        "Русский академический стиль, markdown."
    )

    logger.info("Generating section draft for %s (chapter %d)", section, chapter)
    text = (
        ai.call(
            system,
            prompt_text,
            model_override=resolve_task_model("draft", config.ai),
        )
        or ""
    )

    if not text:
        return DraftResult(section=section, chapter=chapter)

    # Extract and filter citations
    citations_used = _extract_citations(text)
    filtered = []
    if valid_citekeys is not None:
        text, filtered = _filter_hallucinated_citations(text, valid_citekeys)
        citations_used = _extract_citations(text)

    if filtered:
        logger.warning(
            "Removed %d hallucinated citekeys from draft: %s",
            len(filtered),
            filtered,
        )

    # Convert [@citekey] → [[@citekey]] for Obsidian wikilinks
    text = re.sub(r"\[@([\w\-]+)\]", r"[[@\1]]", text)

    word_count = len(text.split())

    return DraftResult(
        section=section,
        chapter=chapter,
        text=text,
        word_count=word_count,
        citations_used=citations_used,
        filtered_citekeys=filtered,
        research_report_used=bool(research_report_content),
    )
