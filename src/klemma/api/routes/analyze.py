"""Analyze endpoints: status, coverage, gaps, health (ADR-009, #99, #224)."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from klemma.models import UserRecord

from ..auth.deps import get_current_user
from ..deps import get_paper_store, get_project_store, get_user_library

router = APIRouter()

_MIN_SOURCES_COVERED = 3


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class SourceStats(BaseModel):
    """Summary statistics for the user's sources."""

    total: int
    completed: int
    pending: int
    failed: int


class SectionCoverage(BaseModel):
    """Coverage data for a single section."""

    section: str
    source_count: int


class StatusResponse(BaseModel):
    """Full project status — the SaaS equivalent of `klemma status`."""

    sources: SourceStats
    coverage: list[SectionCoverage]
    total_fragments: int


class ChapterHealth(BaseModel):
    """Health assessment for a single chapter."""

    number: str
    source_count: int
    quality: float | None = None
    verdict: str  # "empty" | "low" | "covered" | "well_covered"
    verdict_text: str


class Recommendation(BaseModel):
    """Prioritized action recommendation."""

    priority: str  # "critical" | "high" | "medium"
    title: str
    description: str
    action: str  # "search" | "add_gaps" | "prune" | "process"
    section_id: str | None = None


class HealthStats(BaseModel):
    """Aggregate statistics for the health endpoint."""

    total_sources: int
    total_fragments: int
    ref_gaps_open: int
    pruned_drop: int
    pruned_maybe: int


class HealthResponse(BaseModel):
    """Library health assessment — the SaaS equivalent of `klemma library`.

    No AI calls. All metrics computed from DB data.
    """

    score: int
    diagnosis: str
    chapters: list[ChapterHealth]
    recommendations: list[Recommendation]
    stats: HealthStats


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/status", response_model=StatusResponse)
async def get_status(
    user: UserRecord = Depends(get_current_user),
) -> StatusResponse:
    """Get project status: source counts, coverage by section, fragment count.

    This is the SaaS equivalent of `klemma status` — the most-used CLI command.
    Composes data from UserLibrary (source counts) and ProjectStore (coverage).
    """
    library = get_user_library()
    project_store = get_project_store()
    paper_store = get_paper_store()

    # Source counts by status — scoped to the authenticated user
    all_sources = library.get_all_sources(user_id=user.user_id)
    completed = sum(1 for s in all_sources if s.status == "completed")
    pending = sum(1 for s in all_sources if s.status == "pending")
    failed = sum(1 for s in all_sources if s.status == "failed")

    # Coverage by section from ProjectStore — scoped to the authenticated user
    stats = project_store.get_coverage_stats(user_id=user.user_id)
    sections = stats.get("sections", {})
    coverage = [
        SectionCoverage(section=str(sec), source_count=cnt)
        for sec, cnt in sorted(sections.items())
    ]

    # Total fragments across all sources
    total_fragments = 0
    for src in all_sources:
        frags = paper_store.get_fragments(src.paper_id)
        total_fragments += len(frags)

    return StatusResponse(
        sources=SourceStats(
            total=len(all_sources),
            completed=completed,
            pending=pending,
            failed=failed,
        ),
        coverage=coverage,
        total_fragments=total_fragments,
    )


@router.get("/health", response_model=HealthResponse)
async def get_health(
    user: UserRecord = Depends(get_current_user),
) -> HealthResponse:
    """Library health assessment with chapter verdicts and recommendations.

    Pure DB computation — no AI calls. Computes quality scores from existing
    source quality_score fields and coverage data. Returns prioritized
    recommendations based on deterministic rules.

    This is the SaaS equivalent of `klemma library` (health mode).
    """
    library = get_user_library()
    project_store = get_project_store()
    paper_store = get_paper_store()

    all_sources = library.get_all_sources()
    coverage_stats = project_store.get_coverage_stats()
    sections = coverage_stats.get("sections", {})
    chapters_data = coverage_stats.get("chapters", {})

    # --- Build quality map: chapter → list of quality scores ---
    chapter_quality: dict[str, list[float]] = {}
    for src in all_sources:
        for ch in src.chapters:
            ch_str = str(ch)
            if ch_str not in chapter_quality:
                chapter_quality[ch_str] = []
            if src.quality_score is not None:
                chapter_quality[ch_str].append(float(src.quality_score))

    # --- Chapter health assessment ---
    chapter_list: list[ChapterHealth] = []
    for ch_num, src_count in sorted(chapters_data.items(), key=lambda x: float(str(x[0]))):
        ch_str = str(ch_num)
        qualities = chapter_quality.get(ch_str, [])
        avg_quality = sum(qualities) / len(qualities) if qualities else None

        if src_count == 0:
            verdict = "empty"
            verdict_text = "Нет источников."
        elif src_count < _MIN_SOURCES_COVERED:
            verdict = "low"
            verdict_text = f"Недостаточно источников ({src_count} из {_MIN_SOURCES_COVERED} рекомендуемых)."
        elif avg_quality is not None and avg_quality < 2.0:
            verdict = "low_quality"
            verdict_text = f"Качество низкое (средний балл {avg_quality:.1f}/5). Источники есть, но слабые."
        else:
            verdict = "well_covered"
            q_text = f" Средний балл {avg_quality:.1f}/5." if avg_quality is not None else ""
            verdict_text = f"Хорошо покрыта ({src_count} источников).{q_text}"

        chapter_list.append(ChapterHealth(
            number=ch_str,
            source_count=int(src_count),
            quality=round(avg_quality, 1) if avg_quality is not None else None,
            verdict=verdict,
            verdict_text=verdict_text,
        ))

    # --- Health score (% of chapters adequately covered) ---
    if chapter_list:
        covered = sum(1 for ch in chapter_list if ch.verdict == "well_covered")
        score = round((covered / len(chapter_list)) * 100)
    else:
        # No chapters but has sources — partial score based on source count
        score = min(50, len(all_sources) * 10) if all_sources else 0

    # --- Diagnosis text ---
    empty_chapters = [ch for ch in chapter_list if ch.verdict == "empty"]
    low_chapters = [ch for ch in chapter_list if ch.verdict in ("low", "low_quality")]

    if not chapter_list and not all_sources:
        diagnosis = "Загрузите источники и создайте структуру проекта."
    elif not chapter_list:
        diagnosis = f"{len(all_sources)} источников загружено, но структура проекта не создана."
    elif empty_chapters:
        names = ", ".join(f"Гл. {ch.number}" for ch in empty_chapters)
        diagnosis = f"{names} — нет источников. Критическая проблема."
    elif low_chapters:
        names = ", ".join(f"Гл. {ch.number}" for ch in low_chapters)
        diagnosis = f"{names} — недостаточно источников или низкое качество."
    else:
        diagnosis = "Библиотека хорошо сбалансирована по всем главам."

    # --- Recommendations ---
    recommendations: list[Recommendation] = []

    # Empty sections (critical)
    empty_sections = [
        (sec, cnt) for sec, cnt in sections.items() if cnt == 0
    ]
    if empty_sections:
        sec_ids = ", ".join(s for s, _ in sorted(empty_sections)[:5])
        recommendations.append(Recommendation(
            priority="critical",
            title=f"{len(empty_sections)} разделов без источников",
            description=f"Разделы {sec_ids} не имеют назначенных источников.",
            action="search",
            section_id=empty_sections[0][0] if len(empty_sections) == 1 else None,
        ))

    # Pending processing
    pending_count = sum(1 for s in all_sources if s.status == "pending")
    if pending_count > 0:
        recommendations.append(Recommendation(
            priority="high",
            title=f"{pending_count} источников ожидают обработки",
            description="Запустите обработку для извлечения фрагментов.",
            action="process",
        ))

    # Low quality chapters
    for ch in chapter_list:
        if ch.verdict == "low_quality":
            recommendations.append(Recommendation(
                priority="high",
                title=f"Глава {ch.number} — низкое качество источников",
                description=ch.verdict_text,
                action="search",
            ))

    # Prune pending
    prune_summary = project_store.get_prune_summary()
    drop_count = prune_summary.get("drop", 0)
    maybe_count = prune_summary.get("maybe", 0)
    if drop_count > 0 or maybe_count > 0:
        recommendations.append(Recommendation(
            priority="medium",
            title=f"{drop_count} к удалению, {maybe_count} к проверке",
            description="Есть результаты аудита библиотеки, ожидающие решения.",
            action="prune",
        ))

    # --- Total fragments ---
    total_fragments = 0
    for src in all_sources:
        frags = paper_store.get_fragments(src.paper_id)
        total_fragments += len(frags)

    # --- Ref gaps count (best-effort) ---
    # Count entries in citation_graph not marked as in_library.
    ref_gaps_open = 0
    try:
        ref_gaps_open = paper_store.count_citation_gaps()
    except Exception:
        pass  # method may not exist yet or table empty

    return HealthResponse(
        score=score,
        diagnosis=diagnosis,
        chapters=chapter_list,
        recommendations=sorted(
            recommendations,
            key=lambda r: {"critical": 0, "high": 1, "medium": 2}.get(r.priority, 3),
        ),
        stats=HealthStats(
            total_sources=len(all_sources),
            total_fragments=total_fragments,
            ref_gaps_open=ref_gaps_open,
            pruned_drop=drop_count,
            pruned_maybe=maybe_count,
        ),
    )
