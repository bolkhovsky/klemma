"""Librarian skill — AI-powered library health analysis and recommendations."""

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from pathlib import Path
from typing import Optional

from ..ai import AIProvider
from ..config import KlemmaConfig, ProjectConfig, resolve_prompt
from ..literature.models import LibraryReport
from ..state import StateManager
from ..vault import VaultAdapter
from .planner import _get_current_deadline

logger = logging.getLogger(__name__)

LIBRARY_TIMEOUT = 300  # 5 min — library analysis needs more time than other skills


def analyze_library(
    config: KlemmaConfig,
    state: StateManager,
    vault: VaultAdapter,
    ai: AIProvider,
    entry_lookup: dict,
    mode: str = "status",
    focus_section: Optional[str] = None,
    project: Optional[ProjectConfig] = None,
    dissertation_context: str = "",
    klemma_home: Optional[Path] = None,
) -> Optional[LibraryReport]:
    """Run AI library analysis and return structured report.

    Two-stage pipeline:
      1. Main analysis (filtered sources, no prune)
      2. Prune pass (separate call, only when needed)
    """
    # Gather context with mode-aware source filtering
    all_sources = state.get_all_sources()
    drop_ids = state.get_prune_drop_ids()
    active_sources = [s for s in all_sources if s["id"] not in drop_ids]

    context = _gather_library_context(
        config, state, vault, entry_lookup, active_sources, mode, focus_section,
        project=project, dissertation_context=dissertation_context,
    )

    prompt_path = resolve_prompt("librarian.md", klemma_home) if klemma_home else Path(__file__).parent.parent.parent.parent / "prompts" / "librarian.md"
    context["language"] = config.ai.language
    user_prompt = ai.render_prompt(prompt_path, **context)

    system = (
        "You are a library analyst for a PhD dissertation. "
        "Output only valid JSON."
    )

    data = ai.call_json(system, user_prompt, max_tokens=8192, timeout=LIBRARY_TIMEOUT)
    if not data:
        logger.error("Failed to get library analysis from AI")
        return None

    report = LibraryReport(
        mode=mode,
        overall_health=data.get("overall_health", ""),
        chapter_assessments=data.get("chapter_assessments", []),
        critical_issues=data.get("critical_issues", []),
        recommendations=data.get("recommendations", []),
        section_detail=data.get("section_detail", {}),
        audit_findings=data.get("audit_findings", []),
        prune=None,
        report_text=data.get("report_text", ""),
    )

    # Stage 2: separate prune pass if library is oversaturated
    if len(active_sources) > 100:
        logger.info("Running prune analysis (%d active sources)...", len(active_sources))
        prune_result = _run_prune_analysis(active_sources, entry_lookup, config, ai, klemma_home=klemma_home)
        if prune_result:
            report.prune = prune_result
            state.save_prune_verdicts(
                drop=prune_result.get("drop", []),
                maybe=prune_result.get("maybe", []),
            )

    # Save to vault
    _save_report_to_vault(report, vault, mode, focus_section)

    return report


# ---------------------------------------------------------------------------
# Source filtering
# ---------------------------------------------------------------------------

def _select_sources_for_mode(
    active_sources: list[dict], mode: str, focus_section: Optional[str]
) -> tuple[list[dict], dict]:
    """Select relevant sources for the given mode.

    Returns (selected_sources, omit_info) where omit_info has keys for
    the prompt template to display truncation notes.
    """
    total = len(active_sources)

    if mode == "recommend" and focus_section:
        chapter = int(focus_section.split(".")[0])
        selected = [s for s in active_sources if s.get("primary_chapter") == chapter]
        other_count = total - len(selected)
        detail = f"{other_count} sources from other chapters omitted"
        return selected, {
            "sources_omitted": True,
            "sources_shown": len(selected),
            "sources_total": total,
            "sources_omitted_detail": detail,
        }

    if mode == "status":
        # Top 30 per chapter by quality + fragment count
        by_chapter: dict[int, list] = {}
        for s in active_sources:
            ch = s.get("primary_chapter") or 0
            by_chapter.setdefault(ch, []).append(s)

        selected = []
        omitted_parts = []
        for ch in sorted(by_chapter):
            sources = by_chapter[ch]
            sources.sort(
                key=lambda x: (x.get("quality_score") or 0, x.get("fragment_count") or 0),
                reverse=True,
            )
            selected.extend(sources[:30])
            if len(sources) > 30:
                omitted_parts.append(f"ch{ch}: {len(sources) - 30} omitted")

        if omitted_parts:
            return selected, {
                "sources_omitted": True,
                "sources_shown": len(selected),
                "sources_total": total,
                "sources_omitted_detail": ", ".join(omitted_parts),
            }
        return selected, {}

    if mode == "audit":
        # All low-quality + top from each chapter, cap ~150
        low_q = [s for s in active_sources if (s.get("quality_score") or 0) <= 2]
        rest = [s for s in active_sources if (s.get("quality_score") or 0) > 2]

        by_chapter: dict[int, list] = {}
        for s in rest:
            ch = s.get("primary_chapter") or 0
            by_chapter.setdefault(ch, []).append(s)

        selected = list(low_q)
        cap_per_ch = max(5, (150 - len(selected)) // max(len(by_chapter), 1))
        for ch in sorted(by_chapter):
            sources = by_chapter[ch]
            sources.sort(
                key=lambda x: (x.get("quality_score") or 0, x.get("fragment_count") or 0),
                reverse=True,
            )
            selected.extend(sources[:cap_per_ch])

        selected = selected[:150]
        if len(selected) < total:
            return selected, {
                "sources_omitted": True,
                "sources_shown": len(selected),
                "sources_total": total,
                "sources_omitted_detail": f"{total - len(selected)} high-quality sources omitted",
            }
        return selected, {}

    # Fallback: cap at 120
    active_sources_sorted = sorted(
        active_sources,
        key=lambda x: (x.get("quality_score") or 0, x.get("fragment_count") or 0),
        reverse=True,
    )
    selected = active_sources_sorted[:120]
    if len(selected) < total:
        return selected, {
            "sources_omitted": True,
            "sources_shown": len(selected),
            "sources_total": total,
            "sources_omitted_detail": "",
        }
    return selected, {}


# ---------------------------------------------------------------------------
# Context gathering
# ---------------------------------------------------------------------------

def _gather_library_context(
    config: KlemmaConfig,
    state: StateManager,
    vault: VaultAdapter,
    entry_lookup: dict,
    active_sources: list[dict],
    mode: str,
    focus_section: Optional[str],
    project: Optional[ProjectConfig] = None,
    dissertation_context: str = "",
) -> dict:
    """Collect all data needed for the librarian prompt."""
    deadline, days_remaining = _get_current_deadline(config, project=project)
    summary = state.get_library_summary()
    quality_data = state.get_sources_by_quality()
    ref_gaps = state.get_reference_gaps(limit=15)

    # Mode-aware source filtering
    selected, omit_info = _select_sources_for_mode(active_sources, mode, focus_section)
    sources_compact = _format_sources_compact(selected, entry_lookup)

    if project:
        current_chapter = project.current_chapter
        chapter_name = project.chapters.get(current_chapter, "")
    else:
        current_chapter = config.dissertation.current_chapter
        chapter_name = config.dissertation.chapters.get(current_chapter, "")

    context = {
        "dissertation_context": dissertation_context,
        "current_chapter": current_chapter,
        "chapter_name": chapter_name,
        "deadline": deadline,
        "days_remaining": days_remaining,
        "mode": mode,
        "summary": summary,
        "chapters": summary.get("chapters", {}),
        "quality_data": quality_data,
        "ref_gaps": ref_gaps,
        "sources_compact": sources_compact,
        "section": focus_section or "",
        "section_title": "",
        "section_summaries": "",
        # Omission info for prompt template
        **omit_info,
    }

    # For recommend mode: load vault summaries for the section
    if mode == "recommend" and focus_section:
        chapter = int(focus_section.split(".")[0])
        context["section_title"] = (project.chapters.get(chapter, "") if project
                                    else config.dissertation.chapters.get(chapter, ""))
        context["section_summaries"] = _load_section_summaries(
            focus_section, chapter, state, vault
        )

    return context


def _format_sources_compact(sources: list[dict], entry_lookup: dict) -> str:
    """Format all sources as compact list for prompt."""
    lines = []
    for src in sources:
        citekey = src["id"]
        entry = entry_lookup.get(citekey)
        if entry:
            author = entry.authors_str
            year = entry.year or "?"
            title = (entry.title or "")[:50]
        else:
            author = citekey
            year = "?"
            title = ""

        q = src.get("quality_score") or 0
        ch = src.get("primary_chapter") or "?"
        sec = src.get("primary_section") or ""
        frags = src.get("fragment_count") or 0
        lines.append(f"@{citekey}: {author} ({year}). {title} | q={q} ch={ch} s={sec} f={frags}")

    return "\n".join(lines)


def _load_section_summaries(
    section: str, chapter: int, state: StateManager, vault: VaultAdapter
) -> str:
    """Load vault AI summaries for sources in a section."""
    sources = state.get_by_section(section)

    # Supplement with chapter sources if too few
    if len(sources) < 3:
        chapter_sources = state.get_by_chapter(chapter)
        existing_ids = {s["id"] for s in sources}
        for cs in chapter_sources:
            if cs["id"] not in existing_ids:
                sources.append(cs)
            if len(sources) >= 10:
                break

    lines = []
    for src in sources[:10]:
        citekey = src["id"]
        note_content = vault.read_note(f"@{citekey}")
        if not note_content:
            continue

        summary = ""
        for heading in ["## 📝 AI Summary", "## 🎯 Key Findings"]:
            start = note_content.find(heading)
            if start != -1:
                end = note_content.find("---", start + 20)
                chunk = note_content[start : end if end != -1 else start + 600].strip()
                if summary:
                    summary += "\n"
                summary += chunk

        if summary:
            lines.append(f"### @{citekey}\n{summary[:800]}\n")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Prune (separate AI call)
# ---------------------------------------------------------------------------

def _run_prune_analysis(
    active_sources: list[dict],
    entry_lookup: dict,
    config: KlemmaConfig,
    ai: AIProvider,
    klemma_home: Optional[Path] = None,
) -> Optional[dict]:
    """Run focused prune analysis as a separate AI call.

    For >300 sources, batches per-chapter in parallel.
    """
    if len(active_sources) <= 300:
        lang = config.ai.language
        return _prune_batch(active_sources, entry_lookup, ai, klemma_home=klemma_home, language=lang)

    # Per-chapter parallel batching
    lang = config.ai.language
    by_chapter: dict[int, list] = {}
    for s in active_sources:
        ch = s.get("primary_chapter") or 0
        by_chapter.setdefault(ch, []).append(s)

    all_drop: list[dict] = []
    all_maybe: list[dict] = []

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = {
            pool.submit(_prune_batch, sources, entry_lookup, ai, klemma_home=klemma_home, language=lang): ch
            for ch, sources in by_chapter.items()
        }
        for future in as_completed(futures):
            ch = futures[future]
            try:
                result = future.result()
                if result:
                    all_drop.extend(result.get("drop", []))
                    all_maybe.extend(result.get("maybe", []))
            except Exception as e:
                logger.warning("Prune batch for chapter %s failed: %s", ch, e)

    if not all_drop and not all_maybe:
        return None
    return {"drop": all_drop, "maybe": all_maybe}


def _prune_batch(
    sources: list[dict], entry_lookup: dict, ai: AIProvider,
    klemma_home: Optional[Path] = None,
    language: str = "en",
) -> Optional[dict]:
    """Run prune on a batch of sources."""
    sources_compact = _format_sources_compact(sources, entry_lookup)

    prompt_path = resolve_prompt("librarian_prune.md", klemma_home) if klemma_home else Path(__file__).parent.parent.parent.parent / "prompts" / "librarian_prune.md"
    user_prompt = ai.render_prompt(
        prompt_path,
        sources_compact=sources_compact,
        source_count=len(sources),
        language=language,
    )

    system = (
        "You are a library analyst. "
        "Task: identify irrelevant sources for removal. "
        "Output only valid JSON."
    )

    return ai.call_json(system, user_prompt, max_tokens=4096, timeout=LIBRARY_TIMEOUT)


# ---------------------------------------------------------------------------
# Vault report
# ---------------------------------------------------------------------------

def _save_report_to_vault(
    report: LibraryReport,
    vault: VaultAdapter,
    mode: str,
    section: Optional[str],
) -> Optional[str]:
    """Save report to vault as Library/Library_{mode}_{date}.md."""
    today = date.today().isoformat()
    suffix = f"_{section}" if section else ""
    note_name = f"Library_{mode}{suffix}_{today}"

    content = f"---\ntype: library-report\nmode: {mode}\ndate: {today}\n---\n\n"
    content += f"# Library Report: {mode.title()}\n\n"

    if report.overall_health:
        content += f"## Overall Health\n\n{report.overall_health}\n\n"

    if report.chapter_assessments:
        content += "## Chapter Assessments\n\n"
        for ch in report.chapter_assessments:
            ch_num = ch.get("chapter", "?")
            sources = ch.get("sources", "?")
            q_avg = ch.get("quality_avg", "?")
            verdict = ch.get("verdict", "")
            content += f"### Chapter {ch_num} ({sources} sources, avg quality {q_avg})\n\n"
            content += f"{verdict}\n\n"

    if report.critical_issues:
        content += "## Critical Issues\n\n"
        for issue in report.critical_issues:
            content += f"- {issue}\n"
        content += "\n"

    if report.recommendations:
        content += "## Recommendations\n\n"
        for rec in report.recommendations:
            priority = rec.get("priority", "medium")
            marker = {"high": "🔴", "medium": "🟡", "low": "🟢"}.get(priority, "")
            content += f"- {marker} **{rec.get('action', '')}** — {rec.get('reason', '')}\n"
        content += "\n"

    if report.section_detail:
        detail = report.section_detail
        if detail.get("current_sources_assessment"):
            content += f"## Section Assessment\n\n{detail['current_sources_assessment']}\n\n"
        if detail.get("missing_types"):
            content += "### Missing Source Types\n\n"
            for t in detail["missing_types"]:
                content += f"- {t}\n"
            content += "\n"
        if detail.get("reading_order"):
            content += "### Reading Order\n\n"
            for i, item in enumerate(detail["reading_order"], 1):
                ck = item.get("citekey_or_ref", "?")
                reason = item.get("reason", "")
                content += f"{i}. **{ck}** — {reason}\n"
            content += "\n"

    if report.audit_findings:
        content += "## Audit Findings\n\n"
        for finding in report.audit_findings:
            sev = finding.get("severity", "medium")
            marker = {"high": "🔴", "medium": "🟡", "low": "🟢"}.get(sev, "")
            ftype = finding.get("type", "")
            details = finding.get("details", "")
            content += f"- {marker} **{ftype}**: {details}\n"
        content += "\n"

    if report.prune:
        content += "## Prune Recommendations\n\n"
        drop = report.prune.get("drop", [])
        maybe = report.prune.get("maybe", [])
        if drop:
            content += f"### Drop ({len(drop)} sources)\n\n"
            content += "| Citekey | Reason |\n|---------|--------|\n"
            for item in drop:
                ck = item.get('citekey', '?').lstrip('@')
                content += f"| @{ck} | {item.get('reason', '')} |\n"
            content += "\n"
        if maybe:
            content += f"### Maybe ({len(maybe)} sources)\n\n"
            content += "| Citekey | Reason |\n|---------|--------|\n"
            for item in maybe:
                ck = item.get('citekey', '?').lstrip('@')
                content += f"| @{ck} | {item.get('reason', '')} |\n"
            content += "\n"

    if report.report_text:
        content += "---\n\n"
        content += report.report_text

    try:
        path = vault.create_note(note_name, content, folder="Library")
        logger.info("Library report saved: %s", path)
        return str(path)
    except Exception as e:
        logger.warning("Failed to save library report: %s", e)
        return None
