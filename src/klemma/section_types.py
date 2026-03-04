"""Semantic section types — cross-project vocabulary for dissertation/paper sections.

Replaces fragile numeric section IDs ("1.1", "2.3.2") with semantic labels
(methodology, literature_review, etc.) that survive renumbering and work
across projects.
"""

from __future__ import annotations

from enum import Enum
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from .config import ProjectConfig


class SectionType(str, Enum):
    """Semantic section vocabulary for academic works."""

    INTRODUCTION = "introduction"
    BACKGROUND = "background"
    LITERATURE_REVIEW = "literature_review"
    THEORETICAL_FRAMEWORK = "theoretical_framework"
    METHODOLOGY = "methodology"
    IMPLEMENTATION = "implementation"
    DATA_DESCRIPTION = "data_description"
    EXPERIMENTS = "experiments"
    RESULTS = "results"
    DISCUSSION = "discussion"
    CONCLUSION = "conclusion"
    APPENDIX = "appendix"
    CUSTOM = "custom"


# Keyword lists for heuristic section type inference (ru/en).
# Order matters: first match wins. Keywords are lowercased for comparison.
SECTION_TYPE_KEYWORDS: dict[SectionType, list[str]] = {
    SectionType.INTRODUCTION: [
        "введение", "вступление", "introduction", "intro",
    ],
    SectionType.BACKGROUND: [
        "предпосылки", "background", "предметная область", "subject area",
        "основные понятия", "basic concepts",
    ],
    SectionType.LITERATURE_REVIEW: [
        "обзор", "литературный обзор", "анализ существующих",
        "обзор существующих", "состояние вопроса", "related work",
        "literature review", "survey", "state of the art",
        "анализ литературы", "обзор литературы",
        "анализ предметной", "анализ проблем",
    ],
    SectionType.THEORETICAL_FRAMEWORK: [
        "теоретическ", "теория", "theoretical", "framework",
        "формальная модель", "formal model", "математическая модель",
        "разработка модели", "разработка геоинформационной модели",
        "концептуальная модель",
    ],
    SectionType.IMPLEMENTATION: [
        "реализация", "имплементация", "алгоритм", "программн",
        "архитектура системы", "архитектура решения",
        "implementation", "algorithm", "software", "system design",
        "proposed system", "proposed approach",
    ],
    SectionType.METHODOLOGY: [
        "методолог", "методик", "метод валид", "метод исслед",
        "метод оценк", "подход",
        "methodology", "methods", "approach", "method",
    ],
    SectionType.DATA_DESCRIPTION: [
        "данны", "датасет", "корпус", "набор данн",
        "data", "dataset", "corpus", "data description",
        "описание данн",
    ],
    SectionType.EXPERIMENTS: [
        "эксперимент", "экспериментальн", "апробация",
        "experiments", "experimental", "evaluation",
    ],
    SectionType.RESULTS: [
        "результат", "results", "findings", "outcomes",
    ],
    SectionType.DISCUSSION: [
        "обсуждение", "дискуссия", "discussion", "analysis",
        "интерпретация", "interpretation",
    ],
    SectionType.CONCLUSION: [
        "заключение", "выводы", "итоги", "conclusion",
        "conclusions", "summary", "future work",
    ],
    SectionType.APPENDIX: [
        "приложение", "appendix", "appendices", "дополнительн",
    ],
}


def infer_section_type(chapter_name: str) -> Optional[SectionType]:
    """Infer semantic section type from a chapter/section name.

    Uses keyword matching against SECTION_TYPE_KEYWORDS (ru/en).
    Returns None if no match — caller should treat as CUSTOM or skip.

    >>> infer_section_type("Обзор существующих решений")
    <SectionType.LITERATURE_REVIEW: 'literature_review'>
    >>> infer_section_type("Methodology")
    <SectionType.METHODOLOGY: 'methodology'>
    """
    if not chapter_name:
        return None
    name_lower = chapter_name.lower().strip()
    for section_type, keywords in SECTION_TYPE_KEYWORDS.items():
        for keyword in keywords:
            if keyword in name_lower:
                return section_type
    return None


def resolve_section_identifier(
    input_value: str,
    config: Optional[ProjectConfig] = None,
) -> tuple[Optional[str], Optional[SectionType]]:
    """Parse CLI input as either numeric section ID or semantic type.

    Returns (section, section_type) where exactly one is set:
    - Numeric "2.3" → (section="2.3", type=None)
    - Semantic "methodology" → (section=None, type=SectionType.METHODOLOGY)

    If config has section_type_map, a semantic type also resolves to its
    first matching numeric section (returned as section).

    >>> resolve_section_identifier("2.3")
    ('2.3', None)
    >>> resolve_section_identifier("methodology")
    (None, <SectionType.METHODOLOGY: 'methodology'>)
    """
    if not input_value:
        return (None, None)

    stripped = input_value.strip()

    # Check if it looks like a numeric section: starts with digit
    if stripped[0].isdigit():
        return (stripped, None)

    # Try to match as SectionType enum value
    try:
        st = SectionType(stripped.lower())
        # If config has section_type_map, resolve to first numeric section
        section = None
        if config and config.section_type_map:
            for sec_id, type_name in config.section_type_map.items():
                if type_name == st.value:
                    section = sec_id
                    break
        return (section, st)
    except ValueError:
        pass

    # Try keyword-based inference (e.g. "обзор" → literature_review)
    inferred = infer_section_type(stripped)
    if inferred:
        section = None
        if config and config.section_type_map:
            for sec_id, type_name in config.section_type_map.items():
                if type_name == inferred.value:
                    section = sec_id
                    break
        return (section, inferred)

    # Unrecognized — treat as literal section ID
    return (stripped, None)
