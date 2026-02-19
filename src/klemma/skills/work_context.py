"""Dynamic work context builder — replaces hardcoded DISSERTATION_CONTEXT.

Generates a context string from ProjectConfig fields, supporting any work type
(dissertation, paper, thesis) with flexible structure.
"""

from datetime import date, datetime
from typing import Optional

from ..config import KlemmaConfig, ProjectConfig


def build_work_context(project: ProjectConfig) -> str:
    """Build a context string describing the current academic work.

    This replaces the hardcoded DISSERTATION_CONTEXT constant.
    Handles all project types: dissertation (chapters + NR), paper (sections), thesis.
    """
    lines = []

    # Title
    if project.title:
        lines.append(f"Тема: «{project.title}»")
        lines.append("")

    # Scientific results (optional, typically for dissertations)
    if project.scientific_results:
        for key, value in project.scientific_results.items():
            lines.append(f"{key.upper()}: {value}")
        lines.append("")

    # Structure (chapters with deadlines)
    if project.chapters:
        type_label = {
            "dissertation": "Главы",
            "paper": "Разделы",
            "thesis": "Главы",
        }.get(project.type, "Разделы")
        lines.append(f"{type_label}:")

        # Build deadline lookup
        deadline_map = {}
        for dl in project.deadlines:
            deadline_map[dl.chapter] = dl.deadline

        for ch_num in sorted(project.chapters.keys()):
            ch_name = project.chapters[ch_num]
            dl = deadline_map.get(str(ch_num), "")
            dl_suffix = f" (дедлайн: {dl})" if dl else ""
            lines.append(f"{ch_num}. {ch_name}{dl_suffix}")
        lines.append("")

    # Priority terms
    if project.priority_terms:
        lines.append(f"Ключевые понятия: {', '.join(project.priority_terms)}")

    return "\n".join(lines).rstrip()


def get_current_deadline(project: ProjectConfig) -> tuple[str, int]:
    """Get deadline and days remaining for current focus chapter."""
    chapter_str = str(project.current_chapter)
    today = date.today()

    for dl in project.deadlines:
        if dl.chapter == chapter_str:
            deadline_date = datetime.strptime(dl.deadline, "%Y-%m-%d").date()
            days_remaining = (deadline_date - today).days
            return dl.deadline, days_remaining

    return "не указан", -1
