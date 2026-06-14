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

    # Filter out ghost sources with no metadata (#114)
    sources = [s for s in sources if s.get("title") and s.get("authors")]

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
                            "\n\n" + note_content[findings_start:findings_end].strip()
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
    rag_fragments: Optional[list[dict]] = None,
) -> tuple[str, list[dict], list[dict], Optional[list[dict]]]:
    """Progressively reduce prompt content to fit within token budget.

    Budget of 80K chars ~ 20K tokens — leaves room for template
    overhead, system prompt, and 4K output tokens within 30K TPM.

    RAG fragments (per-block) are prioritized over section-level
    fragments.  When budget is tight, section-level fragments are
    trimmed first; RAG fragments are trimmed only as a last resort.

    Reduction order (least to most aggressive):
    1. Trim chapter_draft to 12K chars
    2. Trim vault_summary per source to 400 chars
    3. Trim section-level fragment text to 150 chars
    4. Reduce sources to 15
    5. Drop section-level fragments to 20
    6. Reduce sources to 10
    7. Drop section-level fragments to 10
    8. Trim RAG fragment text to 150 chars
    9. Reduce RAG fragments to 3 per block
    """
    overhead = 20_000

    def _rag_size():
        if not rag_fragments:
            return 0
        return sum(len(json.dumps(b, ensure_ascii=False)) for b in rag_fragments)

    def _estimate():
        return (
            len(chapter_draft)
            + sum(len(json.dumps(s, ensure_ascii=False)) for s in formatted_sources)
            + sum(len(json.dumps(f, ensure_ascii=False)) for f in formatted_fragments)
            + _rag_size()
            + overhead
        )

    if _estimate() <= max_chars:
        return chapter_draft, formatted_sources, formatted_fragments, rag_fragments

    logger.debug(
        "Prompt budget exceeded (%d > %d), trimming chapter_draft",
        _estimate(),
        max_chars,
    )
    chapter_draft = chapter_draft[:12_000]

    if _estimate() <= max_chars:
        return chapter_draft, formatted_sources, formatted_fragments, rag_fragments

    logger.debug(
        "Still over budget (%d), trimming source summaries to 400 chars", _estimate()
    )
    for s in formatted_sources:
        if len(s.get("summary", "")) > 400:
            s["summary"] = s["summary"][:400]

    if _estimate() <= max_chars:
        return chapter_draft, formatted_sources, formatted_fragments, rag_fragments

    logger.debug(
        "Still over budget (%d), trimming fragment text to 150 chars", _estimate()
    )
    for f in formatted_fragments:
        if len(f.get("text", "")) > 150:
            f["text"] = f["text"][:150]

    if _estimate() <= max_chars:
        return chapter_draft, formatted_sources, formatted_fragments, rag_fragments

    logger.debug("Still over budget (%d), reducing sources to 15", _estimate())
    formatted_sources = formatted_sources[:15]

    if _estimate() <= max_chars:
        return chapter_draft, formatted_sources, formatted_fragments, rag_fragments

    logger.debug("Still over budget (%d), reducing fragments to 20", _estimate())
    formatted_fragments = formatted_fragments[:20]

    if _estimate() <= max_chars:
        return chapter_draft, formatted_sources, formatted_fragments, rag_fragments

    logger.debug("Still over budget (%d), reducing sources to 10", _estimate())
    formatted_sources = formatted_sources[:10]

    if _estimate() <= max_chars:
        return chapter_draft, formatted_sources, formatted_fragments, rag_fragments

    logger.debug("Still over budget (%d), reducing fragments to 10", _estimate())
    formatted_fragments = formatted_fragments[:10]

    if _estimate() <= max_chars:
        return chapter_draft, formatted_sources, formatted_fragments, rag_fragments

    # RAG fragments trimming (last resort — higher relevance signal)
    if rag_fragments:
        logger.debug(
            "Still over budget (%d), trimming RAG fragment text to 150 chars",
            _estimate(),
        )
        for block in rag_fragments:
            for f in block.get("fragments", []):
                if len(f.get("text", "")) > 150:
                    f["text"] = f["text"][:150]

        if _estimate() <= max_chars:
            return chapter_draft, formatted_sources, formatted_fragments, rag_fragments

        logger.debug("Still over budget (%d), reducing RAG to 3 per block", _estimate())
        for block in rag_fragments:
            block["fragments"] = block.get("fragments", [])[:3]

    return chapter_draft, formatted_sources, formatted_fragments, rag_fragments


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


def parse_argument_blocks(research_text: str) -> list[dict]:
    """Extract argument blocks from a formatted research report.

    Parses the '## Структура аргументации' section produced by
    ``researcher._format_research()``.  Each block has the format::

        ### N. Title
        Description text
        **Источники:** @citekey1, @citekey2
        *~300 слов*

    Returns a list of dicts with keys: order, title, description, citations.
    """
    if not research_text:
        return []

    blocks: list[dict] = []
    # Match ### N. Title headings inside the argumentation section
    heading_re = re.compile(r"^###\s+(\d+)\.\s+(.+)$", re.MULTILINE)
    section_start_re = re.compile(r"^##\s+Структура аргументации", re.MULTILINE)

    start_match = section_start_re.search(research_text)
    if not start_match:
        return []

    # Find the end of the argumentation section (next ## heading)
    section_text = research_text[start_match.end() :]
    next_section = re.search(r"^##\s+", section_text, re.MULTILINE)
    if next_section:
        section_text = section_text[: next_section.start()]

    headings = list(heading_re.finditer(section_text))
    for i, m in enumerate(headings):
        order = int(m.group(1))
        title = m.group(2).strip()

        # Block body = text between this heading and the next (or section end)
        body_start = m.end()
        body_end = (
            headings[i + 1].start() if i + 1 < len(headings) else len(section_text)
        )
        body = section_text[body_start:body_end].strip()

        # Extract description (everything before **Источники:** or *~ line)
        description_lines = []
        citations: list[str] = []
        for line in body.split("\n"):
            line_stripped = line.strip()
            if line_stripped.startswith("**Источники:**"):
                # Parse @citekey references
                cites_text = line_stripped.replace("**Источники:**", "").strip()
                citations = [
                    c.strip().lstrip("@") for c in cites_text.split(",") if c.strip()
                ]
            elif line_stripped.startswith("*~") and line_stripped.endswith("слов*"):
                continue  # skip word estimate line
            elif line_stripped:
                description_lines.append(line_stripped)

        description = " ".join(description_lines)
        if description:
            blocks.append(
                {
                    "order": order,
                    "title": title,
                    "description": description,
                    "citations": citations,
                }
            )

    return blocks


def retrieve_rag_fragments_per_block(
    blocks: list[dict],
    embeddings: object,
    state: StateManager,
    top_k: int = 5,
) -> list[dict]:
    """Embed each argument block description and retrieve top-K fragments.

    For each block, embeds the description text and calls
    ``state.retrieve_similar_fragments()`` to find the most relevant
    fragments.  Returns a list of block dicts enriched with a
    ``fragments`` key containing the retrieved fragments (formatted
    for the prompt template).

    Gracefully handles embedding failures per block (skips that block).
    """
    if not blocks or not embeddings or not state:
        return []

    model_name = getattr(embeddings, "model_name", None)
    rag_blocks: list[dict] = []
    seen_fragment_ids: set[int] = set()

    for block in blocks:
        description = block.get("description", "")
        if not description:
            continue

        try:
            query_vec = embeddings.embed(description)
            if not query_vec:
                continue
        except Exception:
            logger.debug(
                "Failed to embed block '%s', skipping RAG",
                block.get("title", "?"),
                exc_info=True,
            )
            continue

        raw_fragments = state.retrieve_similar_fragments(
            query_vec,
            top_k=top_k,
            model=model_name,
        )

        # Deduplicate across blocks
        block_fragments = []
        for f in raw_fragments:
            fid = f.get("id")
            if fid in seen_fragment_ids:
                continue
            seen_fragment_ids.add(fid)
            block_fragments.append(
                {
                    "source": f.get("citekey", f.get("source_id", "?")),
                    "text": f.get("fragment_text", "")[:300],
                    "type": f.get("fragment_type", "?"),
                    "relevance": f.get("relevance_score", 3),
                    "similarity": f.get("similarity", 0),
                    "page": f.get("page_number"),
                    "intent": f.get("citation_intent"),
                    "verbatim": bool(f.get("verbatim", 0)),
                }
            )

        if block_fragments:
            rag_blocks.append(
                {
                    "block_order": block.get("order", 0),
                    "block_title": block.get("title", ""),
                    "fragments": block_fragments,
                }
            )

    return rag_blocks


def extract_previous_section_ending(
    content: str,
    section_id: str,
    max_chars: int = 500,
) -> str:
    """Extract last paragraph of the section preceding section_id.

    For section 1.3 → looks for 1.2; for 2.1 → looks for the last section
    of chapter 1 in the content. Returns empty string if nothing found.
    """
    if not content or not section_id:
        return ""

    parts = section_id.split(".")
    try:
        chapter = int(parts[0])
        sub = int(parts[1]) if len(parts) > 1 else 1
    except (ValueError, IndexError):
        return ""

    # Determine previous section ID
    if sub > 1:
        prev_id = f"{chapter}.{sub - 1}"
    elif chapter > 1:
        # Cross-chapter: find last section of previous chapter
        # Find all ### sections whose id starts with chapter-1
        all_sections = re.findall(
            r"^#{1,6}\s+(\d+\.\d+[\.\d]*)\b",
            content,
            re.MULTILINE,
        )
        prev_chapter_sections = [s for s in all_sections if s.startswith(f"{chapter - 1}.")]
        if prev_chapter_sections:
            prev_id = prev_chapter_sections[-1]
        else:
            return ""
    else:
        return ""  # First section of first chapter — no previous

    prev_text = extract_section(content, prev_id)
    if not prev_text:
        return ""

    # Return last non-empty paragraph
    paragraphs = [p.strip() for p in prev_text.split("\n\n") if p.strip()]
    if not paragraphs:
        return ""
    return paragraphs[-1][:max_chars]


def _parse_word_target(text: str) -> Optional[int]:
    """Extract a numeric word-count target from a section description string.

    Recognises the following patterns (Russian and English):
    - ``*~200 слов*``
    - ``(~300 words)``
    - ``≈400 слов``
    - bare ``~500 слов`` or ``~500 words``

    Returns the integer value, or None if no recognisable pattern is found.
    """
    if not text:
        return None
    patterns = [
        r"\*~(\d+)\s+(?:слов|words)\*",       # *~200 слов*  or  *~200 words*
        r"\(~(\d+)\s+(?:слов|words)\)",        # (~300 words) or (~300 слов)
        r"≈\s*(\d+)\s+(?:слов|words)",         # ≈400 слов
        r"~(\d+)\s+(?:слов|words)",            # ~500 слов (bare, no parens/asterisks)
    ]
    for pattern in patterns:
        m = re.search(pattern, text)
        if m:
            return int(m.group(1))
    return None


def load_outline_context(
    section: str,
    project_root: Path,
) -> dict:
    """Load structured outline context for the given section from KLEMMA.md.

    Reads KLEMMA.md body ## Outline section (preferred) or Outline_*.md (fallback).
    Extracts: section title, section description, chapter description,
    scientific contributions, project title, project description.

    Returns dict with keys (empty string if not found):
        section_title, current_section_desc, current_chapter_desc,
        scientific_contributions, title, description
    """
    from ..config import parse_klemma_md

    result: dict = {
        "section_title": "",
        "current_section_desc": "",
        "current_chapter_desc": "",
        "scientific_contributions": "",
        "title": "",
        "description": "",
        "word_target": None,
    }

    if not section or not project_root:
        return result

    parts = section.split(".")
    try:
        chapter_num = int(parts[0])
    except (ValueError, IndexError):
        return result

    outline_text = ""

    # 1. Try KLEMMA.md ## Outline section
    klemma_md_path = project_root / "KLEMMA.md"
    if klemma_md_path.exists():
        fm, body = parse_klemma_md(klemma_md_path)
        # Populate title/description from frontmatter
        result["title"] = fm.get("title", "")
        result["description"] = fm.get("description", "")
        if fm.get("scientific_results"):
            nrs = fm["scientific_results"]
            result["scientific_contributions"] = "\n".join(
                f"- {k.upper()}: {v}" for k, v in nrs.items()
            )

        ol_idx = body.find("## Outline")
        if ol_idx != -1:
            after_ol = ol_idx + len("## Outline")
            # Find the next KLEMMA.md-level meta-section (Notes/History).
            # The outline body may itself contain ## headings (## Scientific
            # Contributions, ## Глава N., etc.) which must NOT terminate extraction.
            # Only ## Notes / ## History (canonical save_outline() siblings) terminate.
            next_h2_match = re.search(
                r"\n## (?:Notes|History|✏️|📋)",
                body[after_ol:],
            )
            if next_h2_match:
                outline_text = body[after_ol:after_ol + next_h2_match.start()].strip()
            else:
                outline_text = body[after_ol:].strip()

    # 2. Fallback: Outline_*.md
    if not outline_text:
        for p in sorted(project_root.glob("Outline_*.md")):
            try:
                outline_text = p.read_text(encoding="utf-8")
            except OSError:
                continue
            break

    if not outline_text:
        return result

    # Extract section title: ### X.X. Title or ### X.X Title
    sec_re = re.compile(
        r"^###\s+" + re.escape(section) + r"\.?\s+(.+)",
        re.MULTILINE,
    )
    m = sec_re.search(outline_text)
    if m:
        result["section_title"] = m.group(1).strip()

    # Extract section description: text between ### X.X and next ###
    sec_block_re = re.compile(
        r"^###\s+" + re.escape(section) + r"[\.\s].+?\n(.*?)(?=^###|\Z)",
        re.MULTILINE | re.DOTALL,
    )
    mb = sec_block_re.search(outline_text)
    if mb:
        sec_desc_raw = mb.group(1).strip()
        result["current_section_desc"] = sec_desc_raw[:600]
        result["word_target"] = _parse_word_target(sec_desc_raw)

    # Extract chapter description: text after ## N. Title or ## Глава N. Title
    ch_block_re = re.compile(
        r"^##\s+(?:\S+\s+)?" + re.escape(str(chapter_num)) + r"[\.\s].+?\n(.*?)(?=^###|\Z)",
        re.MULTILINE | re.DOTALL,
    )
    mc = ch_block_re.search(outline_text)
    if mc:
        result["current_chapter_desc"] = mc.group(1).strip()[:400]

    return result


def load_research_report(
    section: str,
    project_root: Path,
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


def supplement_fragments_from_library(
    section_fragments: list[dict],
    seen_ids: set[str],
    section_sources: list[dict],
    paper_store: object,
    user_library: object,
    section: str,
) -> int:
    """Add fragments from library.db for sources that have none in local state.

    Called when local fragment count is low (< 10) — supplements with library
    corpus fragments so that cross-project fragment sharing works without a
    full StateManager refactor.

    Library fragments are converted to the same dict shape as
    ``state.get_fragments()`` results. ``section`` and ``relevance_score``
    default to the requested section and 3 (neutral) respectively, since
    those fields are project-specific and not stored in library.db.

    Returns the number of fragments added.
    """
    _max_per_source = 10  # cap per-source to avoid flooding the fragment list
    existing_texts = {f.get("fragment_text", "") for f in section_fragments}
    added = 0
    for src in section_sources:
        citekey = src.get("id") or src.get("citekey")
        if not citekey:
            continue
        try:
            paper_id = user_library.resolve_paper_id(citekey)  # type: ignore[attr-defined]
            if not paper_id:
                continue
            lib_frags = paper_store.get_fragments(paper_id)  # type: ignore[attr-defined]
        except Exception:
            logger.debug(
                "Library supplement lookup failed for %s", citekey, exc_info=True
            )
            continue
        source_added = 0
        for frag in lib_frags:
            if source_added >= _max_per_source:
                break
            fid = frag.fragment_id
            ftext = frag.fragment_text
            if fid not in seen_ids and ftext not in existing_texts:
                section_fragments.append(
                    {
                        "id": fid,
                        "citekey": citekey,
                        "source_id": citekey,
                        "fragment_text": ftext,
                        "fragment_type": frag.fragment_type or "key_idea",
                        "section": section,
                        "relevance_score": 3,
                        "usage_hint": "",
                        "citation_intent": frag.citation_intent or "",
                        "similarity": 0.0,  # library frags have no query similarity
                    }
                )
                seen_ids.add(fid)
                existing_texts.add(ftext)
                added += 1
                source_added += 1
    return added
