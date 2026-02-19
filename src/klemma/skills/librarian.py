"""Librarian skill — AI-powered library health analysis and recommendations."""

import logging
from datetime import date
from pathlib import Path
from typing import Optional

from ..ai import ClaudeClient
from ..config import KlemmaConfig, ProjectConfig
from ..literature.models import LibraryReport
from ..state import StateManager
from ..vault import VaultAdapter
from .planner import _get_current_deadline, _get_dissertation_context

logger = logging.getLogger(__name__)


def analyze_library(
    config: KlemmaConfig,
    state: StateManager,
    vault: VaultAdapter,
    ai: ClaudeClient,
    entry_lookup: dict,
    mode: str = "status",
    focus_section: Optional[str] = None,
    project: Optional[ProjectConfig] = None,
) -> Optional[LibraryReport]:
    """Run AI library analysis and return structured report.

    Modes:
        status: overall library health assessment
        recommend: reading recommendations for a section
        audit: deep quality audit
    """
    context = _gather_library_context(
        config, state, vault, entry_lookup, mode, focus_section, project=project
    )

    prompt_path = Path(__file__).parent.parent.parent.parent / "prompts" / "librarian.md"
    user_prompt = ai.render_prompt(prompt_path, **context)

    system = (
        "Ты — библиотекарь-аналитик PhD-библиотеки. "
        "Отвечай только валидным JSON."
    )

    data = ai.call_json(system, user_prompt, max_tokens=16384)
    if not data:
        logger.error("Failed to get library analysis from Claude")
        return None

    report = LibraryReport(
        mode=mode,
        overall_health=data.get("overall_health", ""),
        chapter_assessments=data.get("chapter_assessments", []),
        critical_issues=data.get("critical_issues", []),
        recommendations=data.get("recommendations", []),
        section_detail=data.get("section_detail", {}),
        audit_findings=data.get("audit_findings", []),
        prune=data.get("prune"),
        report_text=data.get("report_text", ""),
    )

    # Persist prune verdicts (with hard protection)
    if report.prune:
        state.save_prune_verdicts(
            drop=report.prune.get("drop", []),
            maybe=report.prune.get("maybe", []),
        )

    # Save to vault
    _save_report_to_vault(report, vault, mode, focus_section)

    return report


def _gather_library_context(
    config: KlemmaConfig,
    state: StateManager,
    vault: VaultAdapter,
    entry_lookup: dict,
    mode: str,
    focus_section: Optional[str],
    project: Optional[ProjectConfig] = None,
) -> dict:
    """Collect all data needed for the librarian prompt."""
    deadline, days_remaining = _get_current_deadline(config, project=project)
    summary = state.get_library_summary()
    quality_data = state.get_sources_by_quality()
    ref_gaps = state.get_reference_gaps(limit=15)

    # Compact sources list for prompt
    all_sources = state.get_all_sources()
    sources_compact = _format_sources_compact(all_sources, entry_lookup)

    if project:
        current_chapter = project.current_chapter
        chapter_name = project.chapters.get(current_chapter, "")
    else:
        current_chapter = config.dissertation.current_chapter
        chapter_name = config.dissertation.chapters.get(current_chapter, "")

    context = {
        "dissertation_context": _get_dissertation_context(config, project),
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
        "prune_needed": summary.get("total", 0) > 100,
        "target_range": "100-120",
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

    if report.prune:
        content += "## Prune Recommendations\n\n"
        drop = report.prune.get("drop", [])
        maybe = report.prune.get("maybe", [])
        if drop:
            content += f"### Drop ({len(drop)} sources)\n\n"
            content += "| Citekey | Reason |\n|---------|--------|\n"
            for item in drop:
                content += f"| @{item.get('citekey', '?')} | {item.get('reason', '')} |\n"
            content += "\n"
        if maybe:
            content += f"### Maybe ({len(maybe)} sources)\n\n"
            content += "| Citekey | Reason |\n|---------|--------|\n"
            for item in maybe:
                content += f"| @{item.get('citekey', '?')} | {item.get('reason', '')} |\n"
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
