"""Утренний брифинг — генерация плана дня (философия Second Brain)."""

import json
import logging
from datetime import date, datetime
from pathlib import Path
from typing import Optional

from ..ai import AIProvider
from ..config import KlemmaConfig, ProjectConfig, resolve_prompt
from ..literature.models import DailyPlan
from ..state import StateManager
from ..vault import VaultAdapter
from .work_context import build_work_context, get_current_deadline

logger = logging.getLogger(__name__)


# Legacy constant — kept for backward compatibility with external imports.
# Internal code should use build_work_context(project) or dissertation_context parameter.
DISSERTATION_CONTEXT = ""


def _get_dissertation_context(config: KlemmaConfig, project: Optional[ProjectConfig] = None) -> str:
    """Get work context string — dynamic from project, or legacy fallback."""
    if project:
        return build_work_context(project, language=config.ai.language)
    return DISSERTATION_CONTEXT


def _get_current_deadline(config: KlemmaConfig, project: Optional[ProjectConfig] = None) -> tuple[str, int]:
    """Deadline for current chapter and days remaining."""
    if project:
        return get_current_deadline(project, language=config.ai.language)

    chapter_str = str(config.dissertation.current_chapter)
    today = date.today()

    for dl in config.dissertation.deadlines:
        if dl.chapter == chapter_str:
            deadline_date = datetime.strptime(dl.deadline, "%Y-%m-%d").date()
            days_remaining = (deadline_date - today).days
            return dl.deadline, days_remaining

    return "не указан", -1


def _read_chapter_plan(config: KlemmaConfig, vault: VaultAdapter,
                       project: Optional[ProjectConfig] = None) -> Optional[str]:
    """Прочитать план главы из vault (например, План_Глава1)."""
    if project:
        pattern = project.chapter_plan_pattern
        note_name = pattern.format(chapter=project.current_chapter)
    else:
        pattern = config.dissertation.chapter_plan_pattern
        note_name = pattern.format(chapter=config.dissertation.current_chapter)

    content = vault.read_note(note_name)
    if not content:
        return None

    # Извлечь секцию с планом сессий (самая полезная часть)
    marker = "## План работы по сессиям"
    idx = content.find(marker)
    if idx != -1:
        end_marker = "## Сводная таблица"
        end_idx = content.find(end_marker, idx)
        if end_idx != -1:
            return content[idx:end_idx].strip()
        return content[idx:].strip()

    # Если маркер не найден — вернуть начало контента
    return content[:4000]


def _format_briefing(data: dict) -> str:
    """Форматировать JSON-ответ в русский брифинг для daily note."""
    lines = []

    status = data.get("status_line", "")
    if status:
        lines.append(f"**Статус:** {status}")

    intervention = data.get("intervention", "NONE")
    intervention_msg = data.get("intervention_message", "")
    if intervention != "NONE" and intervention_msg:
        lines.append(f"**Интервенция ({intervention}):** {intervention_msg}")

    lines.append("")

    focus = data.get("focus", "")
    if focus:
        lines.append("### Фокус сегодня")
        lines.append(focus)

    why = data.get("why", "")
    if why:
        lines.append(f"\n**Почему:** {why}")

    sources = data.get("sources_needed", [])
    if sources:
        lines.append(f"\n**Источники:** {', '.join(sources)}")

    reading = data.get("reading_target", "")
    if reading:
        lines.append(f"\n**Чтение:** {reading}")

    assistant = data.get("assistant_task", "")
    if assistant:
        lines.append(f"\n**Задача ассистента:** {assistant}")

    suggestions = data.get("strategy_suggestions", [])
    if suggestions:
        lines.append("\n### Предложения по стратегии")
        for s in suggestions:
            lines.append(f"- {s}")

    progress = data.get("progress_summary", "")
    if progress:
        lines.append(f"\n**Прогресс:** {progress}")

    return "\n".join(lines)


def generate_morning_plan(
    config: KlemmaConfig,
    state: StateManager,
    vault: VaultAdapter,
    ai: AIProvider,
    project: Optional[ProjectConfig] = None,
    dissertation_context: str = "",
    klemma_home: Optional[Path] = None,
) -> DailyPlan:
    """Сгенерировать утренний брифинг через Claude."""

    # Контекст из базы
    min_sources = (project.min_sources_per_section if project
                   else config.dissertation.min_sources_per_section)
    yesterday = state.get_yesterday_plan()
    coverage = state.get_coverage_stats()
    gaps = state.get_gaps(min_sources=min_sources)
    fragment_stats = state.get_fragment_stats()
    next_reading = state.get_next_reading()

    # Дедлайн
    current_deadline, days_until_deadline = _get_current_deadline(config, project=project)

    # План главы из vault
    chapter_plan = _read_chapter_plan(config, vault, project=project)

    # Streak
    writing_streak = state.get_writing_streak()

    # Название главы
    if project:
        current_chapter = project.current_chapter
        chapter_name = project.chapters.get(current_chapter, "Unknown")
        current_section = project.current_section
        writing_constraints = project.writing_constraints
    else:
        current_chapter = config.dissertation.current_chapter
        chapter_name = config.dissertation.chapters.get(current_chapter, "Unknown")
        current_section = config.dissertation.current_section
        writing_constraints = config.dissertation.writing_constraints

    # Library summary for context
    library_summary = state.get_library_summary()
    lib_digest = (
        f"Библиотека: {library_summary['completed']} обработано, "
        f"{library_summary.get('pending', 0)} в ожидании. "
        f"Среднее качество: {library_summary.get('avg_quality', 0)}/5. "
        f"Ref-gaps: {library_summary.get('ref_gaps_open', 0)} открыто."
    )
    if library_summary.get("zero_sections"):
        lib_digest += f" Разделы без источников: {', '.join(library_summary['zero_sections'][:5])}."

    # Рендер промпта
    prompt_path = resolve_prompt("morning.md", klemma_home) if klemma_home else Path(__file__).parent.parent.parent.parent / "prompts" / "morning.md"
    user_prompt = ai.render_prompt(
        prompt_path,
        dissertation_context=dissertation_context or _get_dissertation_context(config, project),
        current_chapter=current_chapter,
        current_section=current_section,
        chapter_name=chapter_name,
        current_deadline=current_deadline,
        days_until_deadline=days_until_deadline,
        days_without_progress=writing_streak["days_without_progress"],
        streak=writing_streak["streak"],
        yesterday_plan=yesterday,
        chapter_plan=chapter_plan or "Session plan not found.",
        coverage=coverage,
        gaps=gaps,
        fragment_stats=fragment_stats,
        next_reading=next_reading,
        min_sources=min_sources,
        writing_constraints=writing_constraints,
        library_summary=lib_digest,
        language=config.ai.language,
        project_type=project.type if project else "dissertation",
        range=range,
    )

    project_type = project.type if project else "dissertation"
    system = (
        f"You are an academic writing assistant for a {project_type}. "
        "Generate a morning briefing following the 'one focus per day' principle. "
        "Output only valid JSON."
    )

    from klemma.ai import resolve_task_model

    data = ai.call_json(
        system, user_prompt, max_tokens=2048,
        model_override=resolve_task_model("planner", config.ai),
    )

    if not data:
        logger.error("Не удалось сгенерировать утренний план")
        return DailyPlan(
            date="",
            focus="Не удалось сгенерировать план — проверь покрытие и пробелы вручную",
            dissertation_task="Не удалось сгенерировать план",
            assistant_task="Повторить генерацию",
        )

    focus = data.get("focus", "")
    plan = DailyPlan(
        date="",
        # Брифинг
        focus=focus,
        why=data.get("why", ""),
        intervention=data.get("intervention", "NONE"),
        status_line=data.get("status_line", ""),
        sources_needed=data.get("sources_needed", []),
        strategy_suggestions=data.get("strategy_suggestions", []),
        briefing_text=_format_briefing(data),
        # Legacy plan fields (CLI output + DB)
        dissertation_task=focus,
        assistant_task=data.get("assistant_task", ""),
        reading_target=data.get("reading_target", ""),
        progress_summary=data.get("progress_summary", ""),
    )

    # Сохранить в базу
    state.save_plan(
        dissertation_task=plan.dissertation_task,
        assistant_task=plan.assistant_task,
        reading_target=plan.reading_target,
        plan_json=json.dumps(data, ensure_ascii=False),
        progress_summary=plan.progress_summary,
    )

    return plan
