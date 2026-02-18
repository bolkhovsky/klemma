"""Исследовательский брифинг — анализ раздела перед написанием."""

import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from ..ai import ClaudeClient
from ..config import KlemmaConfig
from ..literature.models import ArgumentBlock, CitationEntry, ResearchResult, ZoteroEntry
from ..literature.pdf import PDFExtractor
from ..state import StateManager
from ..vault import VaultAdapter
from .extractor import extract_fragments, save_fragments_to_vault
from .planner import DISSERTATION_CONTEXT

logger = logging.getLogger(__name__)


def _load_chapter_draft(
    chapter: int, config: KlemmaConfig, vault: VaultAdapter
) -> Optional[str]:
    """Прочитать черновик главы из vault."""
    pattern = config.dissertation.chapter_draft_pattern
    note_name = pattern.format(chapter=chapter)
    content = vault.read_note(note_name)
    if not content:
        logger.warning("Черновик главы %d не найден (%s)", chapter, note_name)
    return content


def _extract_section(content: str, section_id: str) -> Optional[str]:
    """Извлечь текст конкретного раздела из markdown главы.

    Ищет заголовок с номером раздела и возвращает текст до
    следующего заголовка того же или более высокого уровня.
    """
    escaped = re.escape(section_id)
    pattern = rf"^(#{{1,6}})\s+{escaped}[\.\s]"

    lines = content.split("\n")
    start_idx = None
    heading_level = None

    for i, line in enumerate(lines):
        m = re.match(pattern, line)
        if m:
            start_idx = i
            heading_level = len(m.group(1))
            break

    if start_idx is None:
        return None

    end_idx = len(lines)
    for i in range(start_idx + 1, len(lines)):
        heading_match = re.match(r"^(#{1,6})\s+\d+\.", lines[i])
        if heading_match:
            level = len(heading_match.group(1))
            if level <= heading_level:
                end_idx = i
                break

    return "\n".join(lines[start_idx:end_idx]).strip()


def _load_section_sources(
    section: str,
    chapter: int,
    state: StateManager,
    vault: VaultAdapter,
    max_sources: int = 25,
) -> list[dict]:
    """Загрузить метаданные и аннотации из vault для источников раздела."""
    sources = state.get_by_section(section)

    if len(sources) < 5:
        chapter_sources = state.get_by_chapter(chapter)
        existing_ids = {s["id"] for s in sources}
        for cs in chapter_sources:
            if cs["id"] not in existing_ids:
                sources.append(cs)
            if len(sources) >= max_sources:
                break

    sources = sources[:max_sources]

    enriched = []
    for src in sources:
        citekey = src["id"]
        note_content = vault.read_note(f"@{citekey}")

        vault_summary = ""
        if note_content:
            # Извлечь AI Summary
            summary_start = note_content.find("## 📝 AI Summary")
            if summary_start != -1:
                summary_end = note_content.find("---", summary_start + 20)
                if summary_end != -1:
                    vault_summary = note_content[summary_start:summary_end].strip()
                else:
                    vault_summary = note_content[
                        summary_start : summary_start + 800
                    ].strip()

            # Извлечь Key Findings если есть место
            if len(vault_summary) < 600:
                findings_start = note_content.find("## 🎯 Key Findings")
                if findings_start != -1:
                    findings_end = note_content.find("---", findings_start + 20)
                    if findings_end != -1:
                        vault_summary += (
                            "\n\n"
                            + note_content[findings_start:findings_end].strip()
                        )

            # Если нет AI Summary — попробовать Methodology
            if not vault_summary:
                meth_start = note_content.find("## 🔬 Methodology")
                if meth_start != -1:
                    meth_end = note_content.find("---", meth_start + 20)
                    if meth_end != -1:
                        vault_summary = note_content[meth_start:meth_end].strip()

        enriched.append(
            {
                **src,
                "vault_summary": vault_summary[:1200],
            }
        )

    return enriched


def _format_research(section: str, data: dict) -> str:
    """Форматировать JSON-ответ Claude в русский markdown для vault."""
    lines = [
        f"# Исследовательский брифинг: Раздел {section}",
        f"*Сгенерировано: {datetime.now().strftime('%Y-%m-%d %H:%M')}*",
        "",
    ]

    status = data.get("section_status", "")
    if status:
        lines.append(f"**Статус раздела:** {status}")
    current_wc = data.get("current_word_count", 0)
    target_wc = data.get("target_word_count", 0)
    readiness = data.get("readiness_pct", 0)
    if target_wc:
        lines.append(f"**Объём:** {current_wc}/{target_wc} слов ({readiness}%)")
    lines.append("")

    # Доступные материалы
    lines.append("## Доступные материалы")
    lines.append(f"- Источников: {data.get('available_sources', 0)}")
    lines.append(f"- Фрагментов: {data.get('available_fragments', 0)}")
    dist = data.get("fragment_distribution", {})
    if dist:
        parts = [f"{t}: {c}" for t, c in dist.items() if c > 0]
        if parts:
            lines.append(f"- Типы: {', '.join(parts)}")
    lines.append("")

    # Структура аргументации
    blocks = data.get("argument_blocks", [])
    if blocks:
        lines.append("## Структура аргументации")
        for block in blocks:
            order = block.get("order", "")
            title = block.get("title", "")
            desc = block.get("description", "")
            citations = block.get("citations", [])
            words = block.get("estimated_words", "")
            lines.append(f"### {order}. {title}")
            lines.append(desc)
            if citations:
                lines.append(
                    f"**Источники:** {', '.join(f'@{c}' for c in citations)}"
                )
            if words:
                lines.append(f"*~{words} слов*")
            lines.append("")

    # План цитирования
    citations = data.get("citation_plan", [])
    if citations:
        lines.append("## План цитирования")
        for c in citations:
            citekey = c.get("citekey", "?")
            usage = c.get("usage", "")
            position = c.get("position", "")
            relevance = c.get("relevance", 3)
            lines.append(f"- **@{citekey}** ({usage}, рел. {relevance}): {position}")
            fragment = c.get("fragment_text", "")
            if fragment:
                lines.append(f"  > {fragment[:200]}")
        lines.append("")

    # Пробелы
    missing = data.get("missing_coverage", [])
    if missing:
        lines.append("## Пробелы в покрытии")
        for m in missing:
            lines.append(f"- {m}")
        lines.append("")

    # Рекомендации
    suggestions = data.get("writing_suggestions", [])
    if suggestions:
        lines.append("## Рекомендации по написанию")
        for s in suggestions:
            lines.append(f"- {s}")
        lines.append("")

    # Секция для пользовательских заметок (заполняется между запусками)
    lines.append("---")
    lines.append("")
    lines.append("## ✏️ Что нового")
    lines.append("")
    lines.append(
        "_Запишите здесь наблюдения, добавленные источники, новые цитаты — "
        "всё, что учесть при следующем запуске `klemma research`._"
    )
    lines.append("")

    # История изменений (заполняется автоматически при re-run)
    lines.append("## 📋 История изменений")
    lines.append("")

    return "\n".join(lines)


def _load_previous_research(
    section: str,
    chapter: int,
    state: StateManager,
    vault: VaultAdapter,
) -> Optional[dict]:
    """Прочитать предыдущий Research-брифинг и извлечь контекст для инкрементального обновления.

    Возвращает dict с ключами:
    - previous_text: полный текст предыдущей заметки
    - user_notes: текст из '## ✏️ Что нового' (что пользователь написал)
    - history: текст из '## 📋 История изменений'
    - previous_citekeys: set citekeys упомянутых в предыдущем брифинге
    - previous_fragment_count: кол-во фрагментов на момент прошлого запуска
    Возвращает None если предыдущего брифинга нет.
    """
    note_name = f"Research_{section}"
    text = vault.read_note(note_name)
    if not text:
        return None

    # Извлечь секцию '## ✏️ Что нового'
    user_notes = ""
    whats_new_marker = "## ✏️ Что нового"
    wn_idx = text.find(whats_new_marker)
    if wn_idx != -1:
        after_wn = wn_idx + len(whats_new_marker)
        # Найти конец секции: следующий ## заголовок
        next_heading = text.find("\n## ", after_wn)
        if next_heading != -1:
            raw = text[after_wn:next_heading].strip()
        else:
            raw = text[after_wn:].strip()
        # Убрать дефолтный placeholder
        if raw and not raw.startswith("_Запишите здесь"):
            user_notes = raw

    # Извлечь секцию '## 📋 История изменений'
    history = ""
    history_marker = "## 📋 История изменений"
    hist_idx = text.find(history_marker)
    if hist_idx != -1:
        after_hist = hist_idx + len(history_marker)
        next_heading = text.find("\n## ", after_hist)
        if next_heading != -1:
            history = text[after_hist:next_heading].strip()
        else:
            history = text[after_hist:].strip()

    # Извлечь citekeys упомянутые в предыдущем брифинге (@citekey)
    previous_citekeys = set(re.findall(r"@([\w\-]+)", text))

    # Извлечь кол-во фрагментов из строки "- Фрагментов: N"
    frag_match = re.search(r"Фрагментов:\s*(\d+)", text)
    previous_fragment_count = int(frag_match.group(1)) if frag_match else 0

    # Извлечь дату предыдущего запуска
    date_match = re.search(r"\*Сгенерировано:\s*(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2})\*", text)
    previous_date = date_match.group(1) if date_match else ""

    return {
        "previous_text": text,
        "user_notes": user_notes,
        "history": history,
        "previous_citekeys": previous_citekeys,
        "previous_fragment_count": previous_fragment_count,
        "previous_date": previous_date,
    }


def pre_extract_sources(
    section: str,
    chapter: int,
    config: KlemmaConfig,
    state: StateManager,
    vault: VaultAdapter,
    ai: ClaudeClient,
    force: bool = False,
    on_progress: Optional[Callable] = None,
    max_sources: int = 50,
    library=None,
) -> dict:
    """Извлечь фрагменты из источников раздела, если ещё не извлечены.

    Собирает источники секции + главы, для каждого с fragment_count == 0
    запускает полный пайплайн: PDF → текст → Claude → фрагменты → vault.
    """
    # 1. Собрать уникальные citekeys
    section_sources = state.get_by_section(section)
    chapter_sources = state.get_by_chapter(chapter)
    seen = set()
    citekeys = []
    for src in section_sources + chapter_sources:
        ck = src["id"]
        if ck not in seen:
            seen.add(ck)
            citekeys.append(ck)
        if len(citekeys) >= max_sources:
            break

    # 2. Отфильтровать: пропустить уже извлечённые (если не force)
    to_extract = []
    skipped = 0
    for ck in citekeys:
        source = state.get_source(ck)
        if not force and source and source.get("fragment_count", 0) > 0:
            skipped += 1
            if on_progress:
                on_progress(ck, "уже извлечены", skipped, len(citekeys))
            continue
        to_extract.append(ck)

    if not to_extract:
        return {"extracted": 0, "skipped": skipped, "failed": [], "no_pdf": []}

    # 3. Use library provider for metadata + PDF paths (single cached load)
    if library:
        entry_lookup = library.entries
        pdf_lookup = library.pdf_paths
    else:
        entry_lookup = {}
        pdf_lookup = {}

    pdf_extractor = PDFExtractor(max_chars=config.ai.max_pdf_chars)
    search_paths = [Path("/Users/ilya/Zotero/storage")]

    # 4. Извлечь фрагменты
    extracted = 0
    failed = []
    no_pdf = []

    for i, ck in enumerate(to_extract, 1):
        # Найти PDF
        source = state.get_source(ck)
        entry = entry_lookup.get(ck) or ZoteroEntry(id=ck, title=ck)
        pdf_path = pdf_extractor.find_pdf(
            ck, search_paths,
            entry_title=entry.title or "",
            direct_path=source.get("pdf_path") if source else entry.pdf_path,
            pdf_lookup=pdf_lookup,
        )

        if not pdf_path:
            no_pdf.append(ck)
            if on_progress:
                on_progress(ck, "PDF не найден", i, len(to_extract))
            continue

        # Извлечь текст
        pdf_text = pdf_extractor.extract(pdf_path)
        if not pdf_text or len(pdf_text) < config.processing.min_pdf_length:
            failed.append(ck)
            if on_progress:
                on_progress(ck, "текст слишком короткий", i, len(to_extract))
            continue

        # Claude анализ
        result = extract_fragments(entry, pdf_text, config, state, ai)

        if result and result.fragments:
            save_fragments_to_vault(
                ck, result.fragments, vault,
                entry=entry, config=config, state=state,
                pdf_text=pdf_text, ai=ai, entry_lookup=entry_lookup,
            )
            extracted += 1
            if on_progress:
                on_progress(ck, f"извлечено {len(result.fragments)} фрагментов", i, len(to_extract))
        else:
            failed.append(ck)
            if on_progress:
                on_progress(ck, "не удалось извлечь", i, len(to_extract))

    return {
        "extracted": extracted,
        "skipped": skipped,
        "failed": failed,
        "no_pdf": no_pdf,
    }


def _save_to_vault(section: str, content: str, vault: VaultAdapter) -> Path:
    """Сохранить исследовательский брифинг в vault."""
    note_name = f"Research_{section}"
    folder = "6 - Main Notes/Диссертация"
    return vault.create_note(note_name, content, folder=folder)


def _archive_user_notes(
    section: str,
    user_notes: str,
    existing_history: str,
    vault: VaultAdapter,
) -> None:
    """Перенести заметки пользователя из '✏️ Что нового' в '📋 История изменений'."""
    if not user_notes:
        return

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    archived_entry = f"### {timestamp}\n\n{user_notes}"

    # Собрать новую историю: новая запись сверху + старая история
    if existing_history:
        new_history = f"{archived_entry}\n\n{existing_history}"
    else:
        new_history = archived_entry

    note_name = f"Research_{section}"
    vault.update_section(note_name, "## 📋 История изменений", new_history)

    # Очистить секцию «Что нового» — вернуть placeholder
    placeholder = (
        "_Запишите здесь наблюдения, добавленные источники, новые цитаты — "
        "всё, что учесть при следующем запуске `klemma research`._"
    )
    vault.update_section(note_name, "## ✏️ Что нового", placeholder)


def _format_research_with_history(
    section: str, data: dict, history: str = ""
) -> str:
    """Форматировать JSON-ответ Claude в markdown, сохраняя историю изменений."""
    base = _format_research(section, data)

    # Если есть история, вставить её в секцию 📋
    if history:
        base = base.replace(
            "## 📋 История изменений\n",
            f"## 📋 История изменений\n\n{history}\n",
        )

    # Если есть update_summary, добавить перед секцией «Что нового»
    update_summary = data.get("update_summary", "")
    if update_summary:
        summary_block = f"> **Обновление:** {update_summary}\n"
        base = base.replace(
            "## ✏️ Что нового",
            f"{summary_block}\n## ✏️ Что нового",
        )

    return base


def research_section(
    section: str,
    config: KlemmaConfig,
    state: StateManager,
    vault: VaultAdapter,
    ai: ClaudeClient,
    save_to_vault: bool = True,
) -> ResearchResult:
    """Сгенерировать исследовательский брифинг для раздела диссертации.

    Собирает контекст (черновик главы, фрагменты, аннотации источников,
    покрытие, план сессий), отправляет Claude для анализа и возвращает
    структурированный результат.

    При повторном запуске автоматически определяет дельту (новые источники,
    фрагменты, заметки пользователя) и обновляет брифинг инкрементально.
    """
    # Определить главу
    chapter = int(section.split(".")[0])
    chapter_name = config.dissertation.chapters.get(chapter, f"Глава {chapter}")

    # 0. Проверить предыдущий брифинг (инкрементальный режим)
    prev = _load_previous_research(section, chapter, state, vault)
    is_incremental = prev is not None and prev["previous_text"]

    # 1. Черновик главы + текст раздела
    draft_content = _load_chapter_draft(chapter, config, vault)
    section_text = None
    if draft_content:
        section_text = _extract_section(draft_content, section)

    # 2. План сессий из vault
    plan_pattern = config.dissertation.chapter_plan_pattern
    plan_name = plan_pattern.format(chapter=chapter)
    chapter_plan = vault.read_note(plan_name)

    if chapter_plan:
        marker = "## План работы по сессиям"
        idx = chapter_plan.find(marker)
        if idx != -1:
            end_marker = "## Сводная таблица"
            end_idx = chapter_plan.find(end_marker, idx)
            if end_idx != -1:
                chapter_plan = chapter_plan[idx:end_idx].strip()
            else:
                chapter_plan = chapter_plan[idx:].strip()
        else:
            chapter_plan = chapter_plan[:6000]

    # 3. Фрагменты для раздела + главы
    section_fragments = state.get_fragments(section=section, limit=50)
    chapter_fragments = state.get_fragments(chapter=chapter, limit=30)

    seen_ids = {f["id"] for f in section_fragments}
    for cf in chapter_fragments:
        if cf["id"] not in seen_ids:
            section_fragments.append(cf)
            seen_ids.add(cf["id"])

    # 4. Аннотации источников из vault
    source_summaries = _load_section_sources(section, chapter, state, vault)

    # 5. Покрытие и пробелы
    coverage = state.get_coverage_stats()
    gaps = state.get_gaps(min_sources=config.dissertation.min_sources_per_section)
    fragment_stats = state.get_fragment_stats()

    # 6. Подготовить фрагменты для промпта
    formatted_fragments = []
    for f in section_fragments[:40]:
        formatted_fragments.append(
            {
                "source": f.get("citekey", f.get("source_id", "?")),
                "text": f.get("fragment_text", "")[:300],
                "type": f.get("fragment_type", "?"),
                "section": f.get("section", "?"),
                "relevance": f.get("relevance_score", 3),
                "usage_hint": f.get("usage_hint", ""),
            }
        )

    # 7. Подготовить аннотации источников для промпта
    formatted_sources = []
    for src in source_summaries:
        formatted_sources.append(
            {
                "citekey": src["id"],
                "quality": src.get("quality_score", 0),
                "priority": src.get("citation_priority", "medium"),
                "nr1": src.get("relevance_nr1", 0),
                "nr2": src.get("relevance_nr2", 0),
                "summary": src.get("vault_summary", ""),
            }
        )

    # 8. Рендер промпта (полный или инкрементальный)
    if is_incremental:
        # Архивировать заметки пользователя перед перезаписью
        _archive_user_notes(
            section, prev["user_notes"], prev["history"], vault
        )

        # Вычислить дельту: новые citekeys
        current_citekeys = {src["id"] for src in source_summaries}
        new_citekeys = sorted(current_citekeys - prev["previous_citekeys"])

        prompt_path = (
            Path(__file__).parent.parent.parent.parent
            / "prompts"
            / "research_incremental.md"
        )
        user_prompt = ai.render_prompt(
            prompt_path,
            dissertation_context=DISSERTATION_CONTEXT,
            target_section=section,
            chapter_num=chapter,
            chapter_name=chapter_name,
            previous_text=prev["previous_text"],
            previous_date=prev["previous_date"],
            user_notes=prev["user_notes"],
            new_citekeys=new_citekeys,
            previous_fragment_count=prev["previous_fragment_count"],
            current_fragment_count=len(section_fragments),
            section_text=section_text or "Раздел ещё не написан.",
            full_chapter_draft=(
                draft_content[:30000]
                if draft_content
                else "Черновик главы не найден."
            ),
            fragments=json.dumps(
                formatted_fragments, ensure_ascii=False, indent=2
            ),
            source_summaries=json.dumps(
                formatted_sources, ensure_ascii=False, indent=2
            ),
            coverage=coverage,
            gaps=gaps,
            min_sources=config.dissertation.min_sources_per_section,
            range=range,
        )

        system = (
            "Ты — исследовательский аналитик для кандидатской диссертации. "
            "Это ПОВТОРНЫЙ анализ — обнови предыдущий брифинг, не переписывая с нуля. "
            "Отвечай на русском. Верни только JSON."
        )

        logger.info(
            "Инкрементальный режим: +%d фрагментов, +%d источников, заметки: %s",
            len(section_fragments) - prev["previous_fragment_count"],
            len(new_citekeys),
            "да" if prev["user_notes"] else "нет",
        )
    else:
        prompt_path = (
            Path(__file__).parent.parent.parent.parent
            / "prompts"
            / "research.md"
        )
        user_prompt = ai.render_prompt(
            prompt_path,
            dissertation_context=DISSERTATION_CONTEXT,
            target_section=section,
            chapter_num=chapter,
            chapter_name=chapter_name,
            section_text=section_text or "Раздел ещё не написан.",
            full_chapter_draft=(
                draft_content[:30000]
                if draft_content
                else "Черновик главы не найден."
            ),
            chapter_plan=chapter_plan or "План сессий не найден.",
            fragments=json.dumps(
                formatted_fragments, ensure_ascii=False, indent=2
            ),
            source_summaries=json.dumps(
                formatted_sources, ensure_ascii=False, indent=2
            ),
            coverage=coverage,
            gaps=gaps,
            fragment_stats=fragment_stats,
            min_sources=config.dissertation.min_sources_per_section,
            range=range,
        )

        system = (
            "Ты — исследовательский аналитик для кандидатской диссертации. "
            "Анализируй готовность раздела и предложи структуру аргументации. "
            "Отвечай на русском. Верни только JSON."
        )

    # 9. Вызов Claude
    data = ai.call_json(system, user_prompt, max_tokens=4096)

    if not data:
        logger.error(
            "Не удалось сгенерировать исследовательский брифинг для %s", section
        )
        return ResearchResult(
            section=section,
            chapter=chapter,
            section_status="Ошибка генерации — проверь подключение к Claude",
        )

    # 10. Построить результат
    data["available_sources"] = len(source_summaries)
    data["available_fragments"] = len(section_fragments)

    # Сохранить историю при инкрементальном режиме
    history = ""
    if is_incremental:
        # Собрать обновлённую историю (после архивации)
        if prev["user_notes"]:
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
            new_entry = f"### {timestamp}\n\n{prev['user_notes']}"
            history = (
                f"{new_entry}\n\n{prev['history']}"
                if prev["history"]
                else new_entry
            )
        else:
            history = prev["history"]

    research_text = _format_research_with_history(section, data, history)

    result = ResearchResult(
        section=section,
        chapter=chapter,
        section_title=data.get("section_title", ""),
        section_status=data.get("section_status", ""),
        current_word_count=data.get("current_word_count", 0),
        target_word_count=data.get("target_word_count", 0),
        readiness_pct=data.get("readiness_pct", 0),
        available_sources=len(source_summaries),
        available_fragments=len(section_fragments),
        fragment_distribution=data.get("fragment_distribution", {}),
        argument_blocks=[
            ArgumentBlock(**b) for b in data.get("argument_blocks", [])
        ],
        citation_plan=[
            CitationEntry(**c) for c in data.get("citation_plan", [])
        ],
        missing_coverage=data.get("missing_coverage", []),
        writing_suggestions=data.get("writing_suggestions", []),
        research_text=research_text,
    )

    # 11. Сохранить в vault
    if save_to_vault:
        saved_path = _save_to_vault(section, research_text, vault)
        logger.info("Исследовательский брифинг сохранён: %s", saved_path)

    return result
