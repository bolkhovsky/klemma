"""Morning planning skill — generates daily plans."""

import logging
from pathlib import Path

from ..ai import ClaudeClient
from ..config import KlemmaConfig
from ..literature.models import DailyPlan
from ..state import StateManager
from ..vault import VaultAdapter

logger = logging.getLogger(__name__)

# Full dissertation context for prompts (from zobsidian-processor)
DISSERTATION_CONTEXT = """\
Тема: «Геоинформационная методология представления и анализа оперативной НГГМИ
для оценки ледовой обстановки в арктических акваториях с использованием нейронных сетей»

НР1: Геоинформационная модель валидации прогнозов ледовой обстановки (AMSR2, Баренцево море)
НР2: Геоинформационная методика оценки качества прогнозов (IIEE-декомпозиция: AEE + ME)

Главы:
1. Анализ предметной области прогнозирования ледовой обстановки
2. Геоинформационная модель оценки качества прогнозов
3. Методика валидации прогнозов ледовой обстановки
4. Алгоритм и программная реализация валидации

Ключевые понятия: SIC, IIEE, AEE, ME, IceNet, AMSR2, ДЗЗ, РСА, НГГМИ, НГО, АЗРФ, СМП\
"""


def generate_morning_plan(
    config: KlemmaConfig,
    state: StateManager,
    vault: VaultAdapter,
    ai: ClaudeClient,
) -> DailyPlan:
    """Generate a daily plan using Claude."""

    # Gather context
    yesterday = state.get_yesterday_plan()
    coverage = state.get_coverage_stats()
    gaps = state.get_gaps(min_sources=config.dissertation.min_sources_per_section)
    fragment_stats = state.get_fragment_stats()
    next_reading = state.get_next_reading()
    stats = state.get_stats()

    # Check recent vault changes
    recent_notes = vault.list_notes(config.obsidian.notes_folder)[-5:]

    # Chapter name
    chapter_name = config.dissertation.chapters.get(
        config.dissertation.current_chapter, "Unknown"
    )

    # Render prompt
    prompt_path = Path(__file__).parent.parent.parent.parent / "prompts" / "morning.md"
    user_prompt = ai.render_prompt(
        prompt_path,
        dissertation_context=DISSERTATION_CONTEXT,
        current_chapter=config.dissertation.current_chapter,
        current_section=config.dissertation.current_section,
        chapter_name=chapter_name,
        yesterday_plan=yesterday,
        coverage=coverage,
        gaps=gaps,
        fragment_stats=fragment_stats,
        next_reading=next_reading,
        recent_notes=recent_notes,
        min_sources=config.dissertation.min_sources_per_section,
        stats=stats,
        range=range,
    )

    system = (
        "You are a PhD academic planning assistant. "
        "Generate a focused, actionable daily plan. Respond in Russian. Output only JSON."
    )

    data = ai.call_json(system, user_prompt, max_tokens=2048)

    if not data:
        logger.error("Failed to generate morning plan")
        return DailyPlan(
            date="",
            dissertation_task="Plan generation failed — review coverage gaps manually",
            assistant_task="Retry planning",
        )

    plan = DailyPlan(
        date="",
        dissertation_task=data.get("dissertation_task", ""),
        assistant_task=data.get("assistant_task", ""),
        reading_target=data.get("reading_target", ""),
        reading_snippet=data.get("reading_snippet", ""),
        progress_summary=data.get("progress_summary", ""),
        coverage_gaps=data.get("coverage_gaps", []),
    )

    # Save to state
    import json

    state.save_plan(
        dissertation_task=plan.dissertation_task,
        assistant_task=plan.assistant_task,
        reading_target=plan.reading_target,
        reading_snippet=plan.reading_snippet,
        plan_json=json.dumps(data, ensure_ascii=False),
    )

    return plan
