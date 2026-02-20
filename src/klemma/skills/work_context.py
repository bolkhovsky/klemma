"""Dynamic work context builder — replaces hardcoded DISSERTATION_CONTEXT.

Generates a context string from ProjectConfig fields, supporting any work type
(dissertation, paper, thesis) with flexible structure.
"""

from datetime import date, datetime

from ..config import ProjectConfig

# Labels by language
_LABELS = {
    "ru": {
        "title": "Тема",
        "chapters": {"dissertation": "Главы", "paper": "Разделы", "thesis": "Главы", "_default": "Разделы"},
        "deadline": "дедлайн",
        "key_terms": "Ключевые понятия",
        "not_specified": "не указан",
    },
    "en": {
        "title": "Topic",
        "chapters": {"dissertation": "Chapters", "paper": "Sections", "thesis": "Chapters", "_default": "Sections"},
        "deadline": "deadline",
        "key_terms": "Key terms",
        "not_specified": "not specified",
    },
}


def _get_labels(language: str) -> dict:
    """Get labels for the given language, falling back to English."""
    return _LABELS.get(language, _LABELS["en"])


def build_work_context(project: ProjectConfig, language: str = "ru") -> str:
    """Build a context string describing the current academic work.

    This replaces the hardcoded DISSERTATION_CONTEXT constant.
    Handles all project types: dissertation (chapters + NR), paper (sections), thesis.
    """
    labels = _get_labels(language)
    lines = []

    # Title
    if project.title:
        lines.append(f"{labels['title']}: {project.title}")
        lines.append("")

    # Scientific results (optional, typically for dissertations)
    if project.scientific_results:
        for key, value in project.scientific_results.items():
            lines.append(f"{key.upper()}: {value}")
        lines.append("")

    # Structure (chapters with deadlines)
    if project.chapters:
        ch_labels = labels["chapters"]
        type_label = ch_labels.get(project.type, ch_labels["_default"])
        lines.append(f"{type_label}:")

        # Build deadline lookup
        deadline_map = {}
        for dl in project.deadlines:
            deadline_map[dl.chapter] = dl.deadline

        dl_word = labels["deadline"]
        for ch_num in sorted(project.chapters.keys()):
            ch_name = project.chapters[ch_num]
            dl = deadline_map.get(str(ch_num), "")
            dl_suffix = f" ({dl_word}: {dl})" if dl else ""
            lines.append(f"{ch_num}. {ch_name}{dl_suffix}")
        lines.append("")

    # Priority terms
    if project.priority_terms:
        lines.append(f"{labels['key_terms']}: {', '.join(project.priority_terms)}")

    return "\n".join(lines).rstrip()


def get_current_deadline(project: ProjectConfig, language: str = "ru") -> tuple[str, int]:
    """Get deadline and days remaining for current focus chapter."""
    chapter_str = str(project.current_chapter)
    today = date.today()

    for dl in project.deadlines:
        if dl.chapter == chapter_str:
            deadline_date = datetime.strptime(dl.deadline, "%Y-%m-%d").date()
            days_remaining = (deadline_date - today).days
            return dl.deadline, days_remaining

    labels = _get_labels(language)
    return labels["not_specified"], -1
