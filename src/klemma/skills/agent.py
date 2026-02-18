"""Universal research agent — builds full dissertation context for interactive Claude session."""

import logging
from datetime import date
from pathlib import Path
from typing import Optional

from jinja2 import Template

from ..config import KlemmaConfig
from ..state import StateManager
from ..vault import VaultAdapter
from .planner import DISSERTATION_CONTEXT, _get_current_deadline

logger = logging.getLogger(__name__)


def build_agent_context(
    config: KlemmaConfig,
    state: StateManager,
    vault: VaultAdapter,
    section: Optional[str] = None,
    chapter: Optional[int] = None,
) -> str:
    """Build a rich system prompt with full research context for the agent.

    Gathers dissertation structure, sources, coverage, gaps, fragments,
    today's plan, and reading queue. Renders via Jinja2 template.
    """
    # Deadline
    current_deadline, days_until_deadline = _get_current_deadline(config)

    # Chapter name
    focus_chapter = chapter or config.dissertation.current_chapter
    chapter_name = config.dissertation.chapters.get(focus_chapter, f"Глава {focus_chapter}")

    # Focus section
    focus_section = section or config.dissertation.current_section

    # Sources: section-specific, chapter-specific, or all
    if section:
        sources = state.get_by_section(section)
        # Supplement with chapter sources
        if chapter:
            ch_sources = state.get_by_chapter(chapter)
        else:
            ch = int(section.split(".")[0])
            ch_sources = state.get_by_chapter(ch)
        seen = {s["id"] for s in sources}
        for cs in ch_sources:
            if cs["id"] not in seen:
                sources.append(cs)
                seen.add(cs["id"])
    elif chapter:
        sources = state.get_by_chapter(chapter)
    else:
        sources = state.get_all_sources()

    # Coverage & gaps
    coverage = state.get_coverage_stats()
    gaps = state.get_gaps(min_sources=config.dissertation.min_sources_per_section)

    # Fragment stats
    fragment_stats = state.get_fragment_stats()

    # Today's plan
    today_plan = state.get_plan()

    # Reading queue
    next_reading = state.get_next_reading()

    # Render prompt
    prompt_path = Path(__file__).parent.parent.parent.parent / "prompts" / "agent.md"
    raw = prompt_path.read_text(encoding="utf-8")
    context = Template(raw).render(
        dissertation_context=DISSERTATION_CONTEXT,
        chapters=config.dissertation.chapters,
        scientific_results=config.dissertation.scientific_results,
        priority_terms=config.dissertation.priority_terms,
        current_chapter=focus_chapter,
        chapter_name=chapter_name,
        current_section=focus_section,
        current_deadline=current_deadline,
        days_until_deadline=days_until_deadline,
        sources=sources,
        coverage=coverage,
        gaps=gaps,
        min_sources=config.dissertation.min_sources_per_section,
        fragment_stats=fragment_stats,
        today_plan=today_plan,
        next_reading=next_reading,
        vault_path=config.obsidian.vault_path,
        today=date.today().isoformat(),
        range=range,
    )

    return context
