"""Universal research agent — builds full project context for interactive Claude session."""

import logging
from datetime import date
from pathlib import Path
from typing import Optional

from jinja2 import Template

from ..config import KlemmaConfig, ProjectConfig, resolve_prompt
from ..state import StateManager
from ..vault import VaultAdapter
from .planner import _get_current_deadline
from .work_context import _get_labels

logger = logging.getLogger(__name__)

# Context separator used by load_project_context() to join parent + child
_CONTEXT_SEPARATOR = "\n\n---\n\n"


def build_agent_context(
    config: KlemmaConfig,
    state: StateManager,
    vault: VaultAdapter,
    section: Optional[str] = None,
    chapter: Optional[int] = None,
    project: Optional[ProjectConfig] = None,
    dissertation_context: str = "",
    klemma_home: Optional[Path] = None,
    project_name: str = "",
) -> str:
    """Build a rich system prompt with full research context for the agent.

    Gathers project structure, sources, coverage, gaps, fragments,
    today's plan, and reading queue. Renders via Jinja2 template.
    """
    # Deadline
    current_deadline, days_until_deadline = _get_current_deadline(config, project=project)

    # Chapter name
    if project:
        focus_chapter = chapter or project.current_chapter
        chapter_name = project.chapters.get(focus_chapter, f"Chapter {focus_chapter}")
        focus_section = section or project.current_section
        chapters = project.chapters
        scientific_results = project.scientific_results
        priority_terms = project.priority_terms
        min_sources = project.min_sources_per_section
    else:
        focus_chapter = chapter or config.dissertation.current_chapter
        chapter_name = config.dissertation.chapters.get(focus_chapter, f"Chapter {focus_chapter}")
        focus_section = section or config.dissertation.current_section
        chapters = config.dissertation.chapters
        scientific_results = config.dissertation.scientific_results
        priority_terms = config.dissertation.priority_terms
        min_sources = config.dissertation.min_sources_per_section

    # Sources: section-specific, chapter-specific, or all
    if section:
        sources = state.get_by_section(section)
        # Supplement with chapter sources
        if chapter:
            ch_sources = state.get_by_chapter(chapter)
        else:
            from ..config import parse_chapter_from_section

            ch = parse_chapter_from_section(section)
            ch_sources = state.get_by_chapter(ch) if ch else []
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
    gaps = state.get_gaps(min_sources=min_sources)

    # Fragment stats
    fragment_stats = state.get_fragment_stats()

    # Today's plan
    today_plan = state.get_plan()

    # Reading queue
    next_reading = state.get_next_reading()

    # Split context: parent vs current project
    project_type = project.type if project else "dissertation"
    parts = dissertation_context.split(_CONTEXT_SEPARATOR)
    if len(parts) > 1:
        parent_context = _CONTEXT_SEPARATOR.join(parts[:-1])
        project_context = parts[-1]
    else:
        parent_context = ""
        project_context = dissertation_context

    # Adaptive labels based on project type and language
    labels = _get_labels(config.ai.language)
    ch_labels = labels["chapters"]
    chapters_label = ch_labels.get(project_type, ch_labels["_default"])
    # Singular form for coverage/fragment stats
    singular_map = {
        "Главы": "Глава", "Разделы": "Раздел",
        "Chapters": "Chapter", "Sections": "Section",
    }
    chapters_label_singular = singular_map.get(chapters_label, chapters_label)

    # Render prompt
    prompt_path = (
        resolve_prompt("agent.md", klemma_home)
        if klemma_home
        else Path(__file__).parent.parent.parent.parent / "prompts" / "agent.md"
    )
    raw = prompt_path.read_text(encoding="utf-8")
    context = Template(raw).render(
        parent_context=parent_context,
        project_context=project_context,
        chapters=chapters,
        chapters_label=chapters_label,
        chapters_label_singular=chapters_label_singular,
        scientific_results=scientific_results,
        priority_terms=priority_terms,
        current_chapter=focus_chapter,
        chapter_name=chapter_name,
        current_section=focus_section,
        current_deadline=current_deadline,
        days_until_deadline=days_until_deadline,
        sources=sources,
        coverage=coverage,
        gaps=gaps,
        min_sources=min_sources,
        fragment_stats=fragment_stats,
        today_plan=today_plan,
        next_reading=next_reading,
        vault_path=config.obsidian.vault_path,
        today=date.today().isoformat(),
        language=config.ai.language,
        project_type=project_type,
        project_name=project_name,
        range=range,
    )

    return context
