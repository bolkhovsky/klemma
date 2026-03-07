"""Shared context-loading helpers for skills (ADR-008).

Extracted from researcher.py — used by researcher, drafter, and future skills.
"""

import json
import logging
import re
from pathlib import Path
from typing import Optional

from ..config import KlemmaConfig, ProjectConfig
from ..state import StateManager
from ..vault import VaultAdapter

logger = logging.getLogger(__name__)


def load_chapter_draft(
    chapter: int,
    config: KlemmaConfig,
    vault: VaultAdapter,
    project: Optional[ProjectConfig] = None,
    project_root: Optional[Path] = None,
) -> Optional[str]:
    """Read chapter draft — project_root first (md > tex > bare), vault fallback.

    When project_root is provided (child/standalone project), only look in
    project_root. Vault fallback is used only for legacy projects without
    project_root, to avoid loading parent's drafts from a shared vault.
    """
    if project:
        pattern = project.chapter_draft_pattern
    else:
        pattern = config.dissertation.chapter_draft_pattern
    note_name = pattern.format(chapter=chapter)

    # Try project_root first (prefer .md > .tex > bare)
    if project_root:
        for ext in (".md", ".tex", ""):
            candidate = project_root / f"{note_name}{ext}"
            if candidate.exists():
                try:
                    return candidate.read_text(encoding="utf-8")
                except OSError:
                    logger.warning("Cannot read %s", candidate)
        # project_root provided but no draft found — don't fall back to vault
        # (avoids loading parent's draft from shared vault in child projects)
        logger.info("Chapter %d draft not found in %s", chapter, project_root)
        return None

    # Vault fallback — only for legacy projects without project_root
    content = vault.read_note(note_name)
    if not content:
        logger.warning("Черновик главы %d не найден (%s)", chapter, note_name)
    return content


def extract_section(content: str, section_id: str) -> Optional[str]:
    """Extract section text from markdown chapter by section number.

    Finds heading with section_id and returns text up to next heading
    of same or higher level.
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


def load_section_sources(
    section: str,
    chapter: int,
    state: StateManager,
    vault: VaultAdapter,
    max_sources: int = 25,
    citekey_filter: Optional[set[str]] = None,
) -> list[dict]:
    """Load source metadata and vault summaries for a section.

    When citekey_filter is provided (e.g., from RAG results), only load
    summaries for those specific sources instead of section-based lookup.
    This avoids parent section namespace collision in child projects.
    """
    if citekey_filter:
        # Use specific citekeys instead of section-based lookup
        all_sources = state.get_all_sources()
        sources = [s for s in all_sources if s["id"] in citekey_filter]
        sources = sources[:max_sources]
    else:
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
            # Extract AI Summary
            summary_start = note_content.find("## 📝 AI Summary")
            if summary_start != -1:
                summary_end = note_content.find("---", summary_start + 20)
                if summary_end != -1:
                    vault_summary = note_content[summary_start:summary_end].strip()
                else:
                    vault_summary = note_content[
                        summary_start : summary_start + 800
                    ].strip()

            # Extract Key Findings if space permits
            if len(vault_summary) < 600:
                findings_start = note_content.find("## 🎯 Key Findings")
                if findings_start != -1:
                    findings_end = note_content.find("---", findings_start + 20)
                    if findings_end != -1:
                        vault_summary += (
                            "\n\n"
                            + note_content[findings_start:findings_end].strip()
                        )

            # If no AI Summary — try Methodology
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


def fit_prompt_budget(
    chapter_draft: str,
    formatted_sources: list[dict],
    formatted_fragments: list[dict],
    max_chars: int = 80_000,
) -> tuple[str, list[dict], list[dict]]:
    """Progressively reduce prompt content to fit within token budget.

    Budget of 80K chars ~ 20K tokens — leaves room for template
    overhead, system prompt, and 4K output tokens within 30K TPM.

    Reduction order (least to most aggressive):
    1. Trim chapter_draft to 12K chars
    2. Trim vault_summary per source to 400 chars
    3. Trim fragment text to 150 chars
    4. Reduce sources to 15
    5. Reduce fragments to 20
    6. Reduce sources to 10
    7. Reduce fragments to 10
    """
    overhead = 20_000

    def _estimate():
        return (
            len(chapter_draft)
            + sum(len(json.dumps(s, ensure_ascii=False)) for s in formatted_sources)
            + sum(len(json.dumps(f, ensure_ascii=False)) for f in formatted_fragments)
            + overhead
        )

    if _estimate() <= max_chars:
        return chapter_draft, formatted_sources, formatted_fragments

    logger.debug("Prompt budget exceeded (%d > %d), trimming chapter_draft", _estimate(), max_chars)
    chapter_draft = chapter_draft[:12_000]

    if _estimate() <= max_chars:
        return chapter_draft, formatted_sources, formatted_fragments

    logger.debug("Still over budget (%d), trimming source summaries to 400 chars", _estimate())
    for s in formatted_sources:
        if len(s.get("summary", "")) > 400:
            s["summary"] = s["summary"][:400]

    if _estimate() <= max_chars:
        return chapter_draft, formatted_sources, formatted_fragments

    logger.debug("Still over budget (%d), trimming fragment text to 150 chars", _estimate())
    for f in formatted_fragments:
        if len(f.get("text", "")) > 150:
            f["text"] = f["text"][:150]

    if _estimate() <= max_chars:
        return chapter_draft, formatted_sources, formatted_fragments

    logger.debug("Still over budget (%d), reducing sources to 15", _estimate())
    formatted_sources = formatted_sources[:15]

    if _estimate() <= max_chars:
        return chapter_draft, formatted_sources, formatted_fragments

    logger.debug("Still over budget (%d), reducing fragments to 20", _estimate())
    formatted_fragments = formatted_fragments[:20]

    if _estimate() <= max_chars:
        return chapter_draft, formatted_sources, formatted_fragments

    logger.debug("Still over budget (%d), reducing sources to 10", _estimate())
    formatted_sources = formatted_sources[:10]

    if _estimate() <= max_chars:
        return chapter_draft, formatted_sources, formatted_fragments

    logger.debug("Still over budget (%d), reducing fragments to 10", _estimate())
    formatted_fragments = formatted_fragments[:10]

    return chapter_draft, formatted_sources, formatted_fragments


def validate_citekeys(data: dict, valid_citekeys: set[str]) -> tuple[dict, list[str]]:
    """Strip hallucinated citekeys from AI response.

    Returns (cleaned_data, list_of_removed_citekeys).
    """
    hallucinated: list[str] = []

    clean_citations = []
    for item in data.get("citation_plan", []):
        ck = item.get("citekey", "")
        if ck in valid_citekeys:
            clean_citations.append(item)
        else:
            hallucinated.append(ck)
    data["citation_plan"] = clean_citations

    for block in data.get("argument_blocks", []):
        original = block.get("citations", [])
        valid = [ck for ck in original if ck in valid_citekeys]
        removed = set(original) - set(valid)
        hallucinated.extend(removed)
        block["citations"] = valid

    filtered = sorted(set(hallucinated))
    if filtered:
        logger.warning(
            "Removed %d hallucinated citekeys (not in library): %s",
            len(filtered),
            filtered,
        )
    return data, filtered


def load_research_report(
    section: str, project_root: Path,
) -> Optional[str]:
    """Read research report for a section from project_root/notes/research/.

    Returns full text content, or None if report not found.
    """
    report_path = project_root / "notes" / "research" / f"Research_{section}.md"
    if not report_path.exists():
        # Legacy flat path
        report_path = project_root / f"Research_{section}.md"
    if not report_path.exists():
        return None

    try:
        text = report_path.read_text(encoding="utf-8")
        return text if text.strip() else None
    except OSError:
        return None
