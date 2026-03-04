"""Parser for dissertation plan-prospect .docx files.

Extracts structured data from a standard Russian dissertation plan-prospect:
title, description (актуальность), research objectives, scientific results,
and full chapter structure.

Designed as a pure module — no CLI dependencies. Works with file paths
or BytesIO for SaaS use.

Requires: python-docx (optional dependency).
"""

import re
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path
from typing import Optional, Union


@dataclass
class Section:
    """A section/subsection in the dissertation structure."""

    number: str          # e.g. "1.1", "2.3.1"
    title: str
    level: int           # 1=chapter, 2=section, 3=subsection
    children: list["Section"] = field(default_factory=list)


@dataclass
class Chapter:
    """A chapter in the dissertation structure."""

    number: int          # 1, 2, 3, 4
    title: str
    sections: list[Section] = field(default_factory=list)


@dataclass
class ScientificResult:
    """A scientific result (НР) from the plan."""

    number: int          # 1, 2, ...
    title: str           # short title from Heading 2
    description: str     # full description from body text


@dataclass
class PlanData:
    """Structured data extracted from a dissertation plan-prospect."""

    title: str = ""
    description: str = ""          # актуальность
    research_object: str = ""      # объект исследования
    research_subject: str = ""     # предмет исследования
    research_goal: str = ""        # цель исследования
    tasks: list[str] = field(default_factory=list)
    results: list[ScientificResult] = field(default_factory=list)
    chapters: list[Chapter] = field(default_factory=list)
    language: str = "ru"


# --- Heading markers for section detection ---
_HEADING_MARKERS = {
    "актуальность": "description",
    "объект исследования": "research_object",
    "предмет исследования": "research_subject",
    "цель исследования": "research_goal",
    "задачи исследования": "tasks",
    "выносимые на защиту": "results",
    "содержание диссертации": "chapters",
}

# Regex for chapter lines: "Глава 1. Title"
_RE_CHAPTER = re.compile(r"^Глава\s+(\d+)\.\s*(.+)")

# Regex for section/subsection lines: "- 1.1. Title" or "1.1. Title"
_RE_SECTION = re.compile(r"^-?\s*(\d+(?:\.\d+)+)\.?\s*(.+)")

# Regex for "Выводы по главе N"
_RE_CONCLUSIONS = re.compile(r"^Выводы по главе\s+\d+", re.IGNORECASE)

# Regex for НР heading: "НР 1. Title"
_RE_NR = re.compile(r"^НР\s+(\d+)\.\s*(.+)")

# Structural lines to skip in chapter parsing
_STRUCTURAL_LINES = {"ВВЕДЕНИЕ", "ЗАКЛЮЧЕНИЕ", "СПИСОК ЛИТЕРАТУРЫ", "ПРИЛОЖЕНИЯ"}


def parse(source: Union[str, Path, BytesIO]) -> PlanData:
    """Parse a dissertation plan-prospect .docx file.

    Args:
        source: file path (str or Path) or BytesIO with .docx content.

    Returns:
        PlanData with extracted fields.

    Raises:
        ImportError: if python-docx is not installed.
        FileNotFoundError: if path doesn't exist.
        ValueError: if file cannot be parsed.
    """
    try:
        from docx import Document
    except ImportError:
        raise ImportError(
            "python-docx is required to parse .docx files. "
            "Install it with: pip install python-docx"
        )

    if isinstance(source, (str, Path)):
        path = Path(source)
        if not path.exists():
            raise FileNotFoundError(f"Plan file not found: {path}")
        doc = Document(str(path))
    else:
        doc = Document(source)

    paragraphs = doc.paragraphs
    data = PlanData()

    # --- Extract title ---
    data.title = _extract_title(paragraphs)

    # --- Split paragraphs by Heading 1 sections ---
    sections = _split_by_headings(paragraphs)

    for heading_text, body_paragraphs in sections:
        heading_lower = heading_text.lower()

        # Match heading to known markers
        matched_field = None
        for marker, field_name in _HEADING_MARKERS.items():
            if marker in heading_lower:
                matched_field = field_name
                break

        if matched_field == "description":
            data.description = _join_paragraphs(body_paragraphs)
        elif matched_field == "research_object":
            data.research_object = _join_paragraphs(body_paragraphs)
        elif matched_field == "research_subject":
            data.research_subject = _join_paragraphs(body_paragraphs)
        elif matched_field == "research_goal":
            data.research_goal = _join_paragraphs(body_paragraphs)
        elif matched_field == "tasks":
            data.tasks = _extract_tasks(body_paragraphs)
        elif matched_field == "results":
            data.results = _extract_results(body_paragraphs)
        elif matched_field == "chapters":
            data.chapters = _parse_chapters(body_paragraphs)

    return data


def _extract_title(paragraphs) -> str:
    """Extract dissertation title from 'на тему «...»' paragraph."""
    for p in paragraphs[:10]:
        text = p.text.strip()
        # Look for «...» quoted title
        m = re.search(r"[«\"](.*?)[»\"]", text)
        if m and len(m.group(1)) > 20:
            return m.group(1)
        # Look for "на тему" prefix with title continuing
        if "на тему" in text.lower():
            # Title might be after «
            m = re.search(r"[«\"](.*?)$", text)
            if m:
                title = m.group(1).rstrip("»\"")
                # Check next paragraphs for continuation
                idx = list(paragraphs).index(p)
                for next_p in paragraphs[idx + 1 : idx + 3]:
                    next_text = next_p.text.strip()
                    if next_text and not next_text.startswith("на соискание"):
                        title += " " + next_text.rstrip("»\"")
                    else:
                        break
                return title
    return ""


def _split_by_headings(paragraphs) -> list[tuple[str, list]]:
    """Split paragraphs into (heading_text, [body_paragraphs]) groups by Heading 1."""
    sections: list[tuple[str, list]] = []
    current_heading = ""
    current_body: list = []

    for p in paragraphs:
        if p.style and p.style.name == "Heading 1":
            if current_heading:
                sections.append((current_heading, current_body))
            current_heading = p.text.strip()
            current_body = []
        elif current_heading:
            current_body.append(p)

    if current_heading:
        sections.append((current_heading, current_body))

    return sections


def _join_paragraphs(paragraphs) -> str:
    """Join paragraph texts into a single string."""
    parts = [p.text.strip() for p in paragraphs if p.text.strip()]
    return " ".join(parts)


def _extract_tasks(paragraphs) -> list[str]:
    """Extract numbered research tasks from paragraphs."""
    tasks = []
    for p in paragraphs:
        text = p.text.strip()
        if not text:
            continue
        # Tasks are typically numbered list items
        # Clean leading markers like "1.", "1)", "-"
        cleaned = re.sub(r"^\d+[.)]\s*", "", text)
        cleaned = re.sub(r"^[-–—]\s*", "", cleaned)
        if cleaned and len(cleaned) > 10:
            tasks.append(cleaned)
    return tasks


def _extract_results(paragraphs) -> list[ScientificResult]:
    """Extract scientific results (НР) from Heading 2 + body pairs."""
    results: list[ScientificResult] = []
    current_nr: Optional[ScientificResult] = None

    for p in paragraphs:
        if p.style and p.style.name == "Heading 2":
            # Save previous
            if current_nr:
                results.append(current_nr)
            # Parse "НР N. Title"
            m = _RE_NR.match(p.text.strip())
            if m:
                current_nr = ScientificResult(
                    number=int(m.group(1)),
                    title=m.group(2).strip(),
                    description="",
                )
            else:
                current_nr = ScientificResult(
                    number=len(results) + 1,
                    title=p.text.strip(),
                    description="",
                )
        elif current_nr:
            text = p.text.strip()
            if text:
                if current_nr.description:
                    current_nr.description += " " + text
                else:
                    current_nr.description = text

    if current_nr:
        results.append(current_nr)

    return results


def _parse_chapters(paragraphs) -> list[Chapter]:
    """Parse chapter structure from 'Содержание диссертации' section.

    Handles multi-line entries where a title wraps across paragraphs.
    Recognizes: 'Глава N. Title', '- N.N. Title', '- N.N.N. Title',
    'Выводы по главе N', structural markers (ВВЕДЕНИЕ, ЗАКЛЮЧЕНИЕ, etc.)
    """
    # First, join continuation lines into complete entries
    lines = _join_continuation_lines(paragraphs)

    chapters: list[Chapter] = []
    current_chapter: Optional[Chapter] = None

    for line in lines:
        # Skip structural markers
        if line.upper() in _STRUCTURAL_LINES:
            continue
        if _RE_CONCLUSIONS.match(line):
            continue
        # Skip appendices
        if line.startswith("Приложение") or line.startswith("- Приложение"):
            continue

        # Chapter: "Глава N. Title"
        m_ch = _RE_CHAPTER.match(line)
        if m_ch:
            current_chapter = Chapter(
                number=int(m_ch.group(1)),
                title=m_ch.group(2).strip(),
            )
            chapters.append(current_chapter)
            continue

        # Section: "- N.N. Title" or "- N.N.N. Title"
        m_sec = _RE_SECTION.match(line)
        if m_sec and current_chapter is not None:
            num = m_sec.group(1)
            title = m_sec.group(2).strip()
            parts = num.split(".")
            level = len(parts)

            section = Section(number=num, title=title, level=level)

            if level == 2:
                current_chapter.sections.append(section)
            elif level >= 3 and current_chapter.sections:
                # Find parent section
                parent_num = ".".join(parts[:2])
                parent = None
                for s in current_chapter.sections:
                    if s.number == parent_num:
                        parent = s
                        break
                if parent:
                    parent.children.append(section)
                else:
                    current_chapter.sections.append(section)

    return chapters


def _join_continuation_lines(paragraphs) -> list[str]:
    """Join paragraphs that are continuations of the previous line.

    A paragraph is a continuation if it doesn't start with a recognized
    pattern (Глава, - N.N., Выводы, structural marker, Приложение).
    """
    lines: list[str] = []

    for p in paragraphs:
        text = p.text.strip()
        if not text:
            continue

        # Is this a new entry or continuation?
        is_new = (
            _RE_CHAPTER.match(text) is not None
            or _RE_SECTION.match(text) is not None
            or _RE_CONCLUSIONS.match(text) is not None
            or text.upper() in _STRUCTURAL_LINES
            or text.startswith("Приложение")
            or text.startswith("- Приложение")
        )

        if is_new or not lines:
            lines.append(text)
        else:
            # Continuation of previous line
            lines[-1] += " " + text

    return lines


def to_klemma_md(data: PlanData) -> str:
    """Convert PlanData to KLEMMA.md content."""
    lines = [
        "# Project Context\n",
        "<!-- Extracted from dissertation plan-prospect. -->\n",
        f'Topic: "{data.title}"\n',
    ]

    if data.description:
        desc = data.description[:500]
        if len(data.description) > 500:
            desc += "..."
        lines.append(f"Description: {desc}\n")

    if data.research_goal:
        lines.append(f"Goal: {data.research_goal}\n")

    if data.results:
        lines.append("Scientific Results:")
        for nr in data.results:
            lines.append(f"- NR{nr.number}: {nr.title}")
        lines.append("")

    if data.tasks:
        lines.append("Research Tasks:")
        for i, task in enumerate(data.tasks, 1):
            task_short = task[:120] + "..." if len(task) > 120 else task
            lines.append(f"- T{i}: {task_short}")
        lines.append("")

    if data.chapters:
        lines.append("Structure:")
        for ch in data.chapters:
            lines.append(f"- Chapter {ch.number}: {ch.title}")
            for sec in ch.sections:
                lines.append(f"  - {sec.number}. {sec.title}")
        lines.append("")

    return "\n".join(lines)
