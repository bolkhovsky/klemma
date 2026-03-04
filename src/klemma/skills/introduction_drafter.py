"""Introduction draft generation — 12 mandatory ГОСТ sections."""

import logging
from dataclasses import dataclass
from typing import Optional

from ..ai import AIProvider
from ..config import KlemmaConfig, ProjectConfig, resolve_prompt
from ..state import StateManager

logger = logging.getLogger(__name__)

# ГОСТ Р 7.0.11-2011 introduction sections
GOST_SECTIONS = [
    "актуальность",
    "цель",
    "задачи",
    "научная новизна",
    "практическая значимость",
    "методология",
    "положения на защиту",
    "достоверность",
    "апробация",
    "личный вклад",
    "публикации",
    "объём и структура",
]


@dataclass
class IntroductionResult:
    """Generated introduction draft."""

    text: str = ""
    section_count: int = 0


def generate_introduction(
    config: KlemmaConfig,
    state: StateManager,
    ai: AIProvider,
    *,
    project: Optional[ProjectConfig] = None,
    dissertation_context: str = "",
    klemma_home=None,
    project_chain=None,
    target_section: Optional[str] = None,
) -> IntroductionResult:
    """Generate introduction draft using AI.

    Args:
        target_section: if set, generate only this section (e.g. "актуальность")
    """
    # Gather fragments grouped by section_type
    fragments_by_type: dict[str, list[dict]] = {}
    all_frags = state.get_fragments()
    for f in all_frags:
        st = f.get("section_type") or "other"
        fragments_by_type.setdefault(st, []).append(f)

    # Reference gaps
    ref_gaps = state.get_reference_gaps(limit=5)

    # Author publication stats
    author_publications = ""
    try:
        from ..source_role import format_gost_phrase
        counts = state.get_author_publication_counts()
        if counts:
            author_publications = format_gost_phrase(counts)
    except Exception:
        pass

    # Chapters and scientific results
    chapters = {}
    scientific_results = {}
    if project:
        chapters = project.chapters or {}
        scientific_results = project.scientific_results or {}

    # Render prompt
    prompt_text = ai.render_prompt(
        resolve_prompt("introduction_draft.md", klemma_home, project_chain=project_chain),
        dissertation_context=dissertation_context,
        chapters=chapters,
        scientific_results=scientific_results,
        fragments_by_type=fragments_by_type,
        ref_gaps=ref_gaps,
        author_publications=author_publications,
        target_section=target_section or "",
    )

    system = (
        "Ты — научный консультант по написанию кандидатских диссертаций. "
        "Генерируй черновик секций введения по ГОСТ Р 7.0.11-2011. "
        "Формат: markdown, русский академический стиль."
    )

    logger.info("Generating introduction draft (section=%s)", target_section or "all")
    text = ai.call(system, prompt_text) or ""

    # Count sections in output
    section_count = text.count("\n## ")
    if text.startswith("## "):
        section_count += 1

    return IntroductionResult(text=text, section_count=section_count)
