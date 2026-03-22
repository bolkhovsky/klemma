"""Исследовательский брифинг — анализ раздела перед написанием."""

import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from ..ai import AIProvider
from ..config import KlemmaConfig, ProjectConfig, resolve_prompt
from ..literature.models import (
    ArgumentBlock,
    CitationEntry,
    ResearchResult,
    ZoteroEntry,
)
from ..literature.pdf import PDFExtractor
from ..state import StateManager
from ..vault import VaultAdapter
from .context_loader import (
    extract_section as _extract_section,
)
from .context_loader import (
    fit_prompt_budget as _fit_prompt_budget,
)
from .context_loader import (
    load_chapter_draft as _load_chapter_draft,
)
from .context_loader import (
    load_section_sources as _load_section_sources,
)
from .context_loader import (
    validate_citekeys as _validate_citekeys,
)
from .extractor import extract_fragments, save_fragments_to_vault
from .planner import _get_dissertation_context

logger = logging.getLogger(__name__)


def _format_research(section: str, data: dict) -> str:
    """Форматировать JSON-ответ Claude в русский markdown для vault."""
    lines = [
        f"# Исследовательский брифинг: Раздел {section}",
        f"*Сгенерировано: {datetime.now().strftime('%Y-%m-%d %H:%M')}*",
        "",
        "> **⚠️ LLM-GENERATED**: Этот брифинг сгенерирован языковой моделью на основе "
        "фрагментов из библиотеки. Структура и рекомендации — анализ модели, "
        "а не первичный источник. Проверяйте цифры и факты по оригинальным статьям.",
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
                lines.append(f"**Источники:** {', '.join(f'@{c}' for c in citations)}")
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
    project_root: Path,
) -> Optional[dict]:
    """Прочитать предыдущий Research-брифинг из project_root.

    Возвращает dict с ключами:
    - previous_text: полный текст предыдущей заметки
    - user_notes: текст из '## ✏️ Что нового' (что пользователь написал)
    - history: текст из '## 📋 История изменений'
    - previous_citekeys: set citekeys упомянутых в предыдущем брифинге
    - previous_fragment_count: кол-во фрагментов на момент прошлого запуска
    Возвращает None если предыдущего брифинга нет.
    """
    # Check new notes/research/ path first, fall back to legacy flat location
    report_path = project_root / "notes" / "research" / f"Research_{section}.md"
    if not report_path.exists():
        report_path = project_root / f"Research_{section}.md"
    if not report_path.exists():
        return None

    try:
        text = report_path.read_text(encoding="utf-8")
    except OSError:
        return None

    if not text.strip():
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
    date_match = re.search(
        r"\*Сгенерировано:\s*(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2})\*", text
    )
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
    ai: AIProvider,
    force: bool = False,
    on_progress: Optional[Callable] = None,
    max_sources: int = 50,
    library=None,
    dissertation_context: str = "",
    available_tags: list[str] | None = None,
    klemma_home: Optional[Path] = None,
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

    # 2. Отфильтровать: пропустить уже извлечённые и pruned (если не force)
    prune_drops = state.get_prune_drop_ids()
    to_extract = []
    skipped = 0
    for ck in citekeys:
        if not force and ck in prune_drops:
            skipped += 1
            if on_progress:
                on_progress(ck, "pruned (drop)", skipped, len(citekeys))
            continue
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
    search_paths = [Path(config.zotero.storage_path)]

    # 4. Извлечь фрагменты
    extracted = 0
    failed = []
    no_pdf = []

    for i, ck in enumerate(to_extract, 1):
        # Найти PDF
        source = state.get_source(ck)
        entry = entry_lookup.get(ck) or ZoteroEntry(id=ck, title=ck)
        pdf_path = pdf_extractor.find_pdf(
            ck,
            search_paths,
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
        result = extract_fragments(
            entry,
            pdf_text,
            config,
            state,
            ai,
            dissertation_context=dissertation_context,
            available_tags=available_tags,
            klemma_home=klemma_home,
        )

        if result and result.fragments:
            save_fragments_to_vault(
                ck,
                result.fragments,
                vault,
                entry=entry,
                config=config,
                state=state,
                pdf_text=pdf_text,
                ai=ai,
                entry_lookup=entry_lookup,
                dissertation_context=dissertation_context,
                available_tags=available_tags,
                klemma_home=klemma_home,
            )
            extracted += 1
            if on_progress:
                on_progress(
                    ck,
                    f"извлечено {len(result.fragments)} фрагментов",
                    i,
                    len(to_extract),
                )
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


def _save_report(
    section: str,
    content: str,
    project_root: Path,
) -> Path:
    """Сохранить исследовательский брифинг в project_root/notes/research/."""
    notes_dir = project_root / "notes" / "research"
    notes_dir.mkdir(parents=True, exist_ok=True)
    path = notes_dir / f"Research_{section}.md"
    path.write_text(content, encoding="utf-8")
    return path


def _format_research_with_history(section: str, data: dict, history: str = "") -> str:
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


def _get_required_fragments(
    required_citekeys: list[str],
    state: StateManager,
    section: str,
    chapter: Optional[int],
) -> tuple[list[dict], list[str]]:
    """Fetch fragments for required citekeys, returning (fragments, missing_citekeys).

    For each citekey, fetches top-10 fragments assigned to the target section.
    Citekeys with no section fragments are returned in the missing list.
    """
    if not required_citekeys:
        return [], []

    all_fragments: list[dict] = []
    seen_ids: set[str] = set()
    missing: list[str] = []

    for citekey in required_citekeys:
        frags = state.get_fragments(source_id=citekey, section=section, limit=10)
        if frags:
            for f in frags:
                fid = f.get("id", "")
                if fid not in seen_ids:
                    all_fragments.append(f)
                    seen_ids.add(fid)
        else:
            missing.append(citekey)

    return all_fragments, missing


def research_section(
    section: str,
    config: KlemmaConfig,
    state: StateManager,
    vault: VaultAdapter,
    ai: AIProvider,
    save_to_vault: bool = True,
    project: Optional[ProjectConfig] = None,
    dissertation_context: str = "",
    klemma_home: Optional[Path] = None,
    project_root: Optional[Path] = None,
    embeddings=None,
    paper_store=None,
    user_library=None,
    required_citekeys: Optional[list[str]] = None,
) -> ResearchResult:
    """Сгенерировать исследовательский брифинг для раздела диссертации.

    Собирает контекст (черновик главы, фрагменты, аннотации источников,
    покрытие, план сессий), отправляет Claude для анализа и возвращает
    структурированный результат.

    При повторном запуске автоматически определяет дельту (новые источники,
    фрагменты, заметки пользователя) и обновляет брифинг инкрементально.
    """
    # Определить главу
    from ..config import parse_chapter_from_section

    chapter = parse_chapter_from_section(section)
    if chapter:
        chapter_name = (
            project.chapters.get(chapter, f"Chapter {chapter}")
            if project
            else config.dissertation.chapters.get(chapter, f"Chapter {chapter}")
        )
    else:
        chapter_name = section  # topic-based section for papers

    # 0. Проверить предыдущий брифинг (инкрементальный режим)
    prev = None
    if project_root:
        prev = _load_previous_research(section, chapter, state, project_root)
    is_incremental = prev is not None and prev["previous_text"]

    # 1. Черновик главы + текст раздела
    draft_content = _load_chapter_draft(chapter, config, vault, project=project)
    section_text = None
    if draft_content:
        section_text = _extract_section(draft_content, section)

    # 2. План сессий из vault
    plan_pattern = (
        project.chapter_plan_pattern
        if project
        else config.dissertation.chapter_plan_pattern
    )
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

    # 3. Фрагменты: RAG-first, затем section-based fallback
    section_fragments = []
    # RAG query: prefer section text, fall back to chapter name for papers
    # without drafts (avoids pulling parent's dissertation fragments by section ID)
    rag_query = (section_text or "")[:500]
    if not rag_query and chapter_name:
        rag_query = chapter_name

    if embeddings and rag_query:
        try:
            query_vec = embeddings.embed(rag_query)
            if query_vec:
                section_fragments = state.retrieve_similar_fragments(
                    query_vec, top_k=40, model=embeddings.model_name
                )
                logger.debug(
                    "RAG: retrieved %d fragments for section '%s' (query: %s...)",
                    len(section_fragments),
                    section,
                    rag_query[:60],
                )
        except Exception:
            logger.debug(
                "Fragment RAG failed, falling back to section-based", exc_info=True
            )

    # Fallback: section-based lookup if RAG yielded <10 results or unavailable
    if len(section_fragments) < 10:
        fallback = state.get_fragments(section=section, limit=50)
        chapter_fallback = state.get_fragments(chapter=chapter, limit=30)
        seen_ids = {f["id"] for f in section_fragments}
        for ff in fallback + chapter_fallback:
            if ff["id"] not in seen_ids:
                section_fragments.append(ff)
                seen_ids.add(ff["id"])
        logger.debug(
            "Fallback: total %d fragments after section-based supplement",
            len(section_fragments),
        )

    # Library supplement: when local state still has few fragments, pull from library.db
    # Covers the cross-project scenario: project B has no local fragments but library.db
    # has them from project A's processing run.
    if paper_store and user_library and len(section_fragments) < 10:
        from .context_loader import supplement_fragments_from_library

        section_srcs = state.get_by_section(section)
        _seen = {f["id"] for f in section_fragments}
        _added = supplement_fragments_from_library(
            section_fragments, _seen, section_srcs, paper_store, user_library, section
        )
        if _added:
            logger.debug("Library supplement: added %d fragments from library.db", _added)

    # 3b. Inject required citekey fragments (before RAG results, dedup by id)
    _req_missing: list[str] = []
    _req_frag_count = 0  # track injected required count to avoid cap dropping them
    if required_citekeys:
        req_frags, _req_missing = _get_required_fragments(
            required_citekeys, state, section, chapter
        )
        for ck in _req_missing:
            logger.warning(
                "Required citekey @%s has no fragments in section '%s' — "
                "run 'klemma process %s' first",
                ck,
                section,
                ck,
            )
        # Prepend required fragments, dedup against existing by fragment id
        existing_ids = {f["id"] for f in section_fragments}
        new_req = [f for f in req_frags if f["id"] not in existing_ids]
        section_fragments = new_req + section_fragments
        _req_frag_count = len(new_req)
        logger.debug(
            "Required: injected %d fragments (%d already present, %d missing citekeys)",
            len(new_req),
            len(req_frags) - len(new_req),
            len(_req_missing),
        )

    # 4. Аннотации источников из vault
    # For fragments retrieved via RAG, derive sources from fragment citekeys
    # instead of section-based lookup (avoids parent's section namespace collision)
    rag_citekeys = {f.get("citekey", f.get("source_id")) for f in section_fragments}
    if rag_citekeys and len(rag_citekeys) > 3:
        # RAG gave us good sources — load summaries for those specifically
        source_summaries = _load_section_sources(
            section, chapter, state, vault, citekey_filter=rag_citekeys
        )
    else:
        source_summaries = _load_section_sources(section, chapter, state, vault)

    # 5. Покрытие и пробелы
    coverage = state.get_coverage_stats()
    min_sources = (
        project.min_sources_per_section
        if project
        else config.dissertation.min_sources_per_section
    )
    gaps = state.get_gaps(min_sources=min_sources)
    fragment_stats = state.get_fragment_stats()

    # 6. Подготовить фрагменты для промпта
    # Always include required fragments; fill remaining budget with RAG results
    _frag_cap = max(40, _req_frag_count)
    formatted_fragments = []
    for f in section_fragments[:_frag_cap]:
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

    # 7b. Budget-aware prompt reduction
    chapter_draft_trimmed = draft_content[:30000] if draft_content else ""
    chapter_draft_trimmed, formatted_sources, formatted_fragments, _ = (
        _fit_prompt_budget(
            chapter_draft_trimmed,
            formatted_sources,
            formatted_fragments,
        )
    )

    # 8. Рендер промпта (полный или инкрементальный)
    if is_incremental:
        # Вычислить дельту: новые citekeys
        current_citekeys = {src["id"] for src in source_summaries}
        new_citekeys = sorted(current_citekeys - prev["previous_citekeys"])

        prompt_path = (
            resolve_prompt("research_incremental.md", klemma_home)
            if klemma_home
            else (
                Path(__file__).parent.parent.parent.parent
                / "prompts"
                / "research_incremental.md"
            )
        )
        user_prompt = ai.render_prompt(
            prompt_path,
            dissertation_context=dissertation_context
            or _get_dissertation_context(config, project),
            target_section=section,
            chapter_num=chapter,
            chapter_name=chapter_name,
            previous_text=prev["previous_text"],
            previous_date=prev["previous_date"],
            user_notes=prev["user_notes"],
            new_citekeys=new_citekeys,
            previous_fragment_count=prev["previous_fragment_count"],
            current_fragment_count=len(section_fragments),
            section_text=section_text or "Section not written yet.",
            full_chapter_draft=(chapter_draft_trimmed or "Chapter draft not found."),
            fragments=json.dumps(formatted_fragments, ensure_ascii=False, indent=2),
            source_summaries=json.dumps(
                formatted_sources, ensure_ascii=False, indent=2
            ),
            coverage=coverage,
            gaps=gaps,
            min_sources=min_sources,
            language=config.ai.language,
            project_type=project.type if project else "dissertation",
            range=range,
        )

        project_type = project.type if project else "dissertation"
        lang = config.ai.language
        system = (
            f"You are a research analyst for a {project_type}. "
            f"Respond entirely in {lang}. "
            "This is a REPEAT analysis — update the previous briefing, do not rewrite from scratch. "
            "CRITICAL: Use ONLY citekeys from the source_summaries JSON. "
            f"Output only valid JSON in {lang}."
        )

        logger.info(
            "Инкрементальный режим: +%d фрагментов, +%d источников, заметки: %s",
            len(section_fragments) - prev["previous_fragment_count"],
            len(new_citekeys),
            "да" if prev["user_notes"] else "нет",
        )
    else:
        prompt_path = (
            resolve_prompt("research.md", klemma_home)
            if klemma_home
            else (
                Path(__file__).parent.parent.parent.parent / "prompts" / "research.md"
            )
        )
        user_prompt = ai.render_prompt(
            prompt_path,
            dissertation_context=dissertation_context
            or _get_dissertation_context(config, project),
            target_section=section,
            chapter_num=chapter,
            chapter_name=chapter_name,
            section_text=section_text or "Section not written yet.",
            full_chapter_draft=(chapter_draft_trimmed or "Chapter draft not found."),
            chapter_plan=chapter_plan or "Session plan not found.",
            fragments=json.dumps(formatted_fragments, ensure_ascii=False, indent=2),
            source_summaries=json.dumps(
                formatted_sources, ensure_ascii=False, indent=2
            ),
            coverage=coverage,
            gaps=gaps,
            fragment_stats=fragment_stats,
            min_sources=min_sources,
            language=config.ai.language,
            project_type=project.type if project else "dissertation",
            range=range,
        )

        project_type = project.type if project else "dissertation"
        lang = config.ai.language
        system = (
            f"You are a research analyst for a {project_type}. "
            f"Respond entirely in {lang}. "
            "Analyze section readiness and suggest an argumentation structure. "
            "CRITICAL: Use ONLY citekeys from the source_summaries JSON. "
            f"Output only valid JSON in {lang}."
        )

    # 9. Вызов Claude
    from klemma.ai import resolve_task_model

    data = ai.call_json(
        system,
        user_prompt,
        max_tokens=4096,
        model_override=resolve_task_model("research", config.ai),
    )

    if not data:
        logger.error(
            "Не удалось сгенерировать исследовательский брифинг для %s", section
        )
        return ResearchResult(
            section=section,
            chapter=chapter,
            section_status="Ошибка генерации — проверь подключение к Claude",
            required_missing=_req_missing,
        )

    # 10. Валидировать citekeys — удалить галлюцинации
    # Use full library, not just section sources: AI sees fragments from
    # across the library (via RAG or cross-section references) and may
    # legitimately cite sources outside the current section.
    valid_citekeys = state.get_existing_source_ids()
    data, filtered_citekeys = _validate_citekeys(data, valid_citekeys)

    # 11. Построить результат
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
                f"{new_entry}\n\n{prev['history']}" if prev["history"] else new_entry
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
        argument_blocks=[ArgumentBlock(**b) for b in data.get("argument_blocks", [])],
        citation_plan=[CitationEntry(**c) for c in data.get("citation_plan", [])],
        missing_coverage=data.get("missing_coverage", []),
        writing_suggestions=data.get("writing_suggestions", []),
        filtered_citekeys=filtered_citekeys,
        required_missing=_req_missing,
        research_text=research_text,
    )

    # 11. Сохранить в project_root
    if save_to_vault and project_root:
        saved_path = _save_report(section, research_text, project_root)
        logger.info("Исследовательский брифинг сохранён: %s", saved_path)

    return result
