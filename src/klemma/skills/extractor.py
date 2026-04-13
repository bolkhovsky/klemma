"""Fragment extraction skill — extracts citation fragments from PDFs."""

import difflib
import logging
from pathlib import Path
from typing import Optional

from ..ai import AIProvider
from ..config import KlemmaConfig, resolve_prompt
from ..literature.models import DowngradeStats, ExtractionResult, Fragment, ZoteroEntry
from ..literature.pdf import PDFExtractor
from ..state import StateManager
from ..text_normalize import normalize
from ..vault import VaultAdapter

logger = logging.getLogger(__name__)

# Fuzzy-match rescue threshold. Fragments whose AI-claimed verbatim text fails
# an exact substring check but matches a window of the paper at this ratio or
# above keep `verbatim=true` with a logged warning — this covers PDF extraction
# noise (OCR char swaps, dropped diacritics) without giving cover to
# fabrication. Below this ratio, the fragment is downgraded to
# `verbatim=false`. Revisit after dogfooding the rescue count distribution.
_FUZZY_RESCUE_THRESHOLD = 0.95


def validate_verbatim_fragments(
    fragments: list[Fragment],
    pdf_text: str,
    source_id: str,
) -> DowngradeStats:
    """Enforce the `verbatim=true` claim against the paper text.

    Two-stage match: (1) exact substring after NFKC + PDF-noise normalization;
    (2) difflib ratio fallback for OCR/extractor artifacts. Below the fuzzy
    threshold, flip the flag to `false` instead of dropping the fragment —
    a paraphrase is still useful, we just don't let it masquerade as a quote.

    NOTE: searches `pdf_text` directly because the current pipeline passes the
    whole (50K-truncated) extraction into the AI prompt. If chunking is
    introduced upstream, this validator must move to the full cached
    ``papers.raw_text`` rather than a per-chunk slice.
    """
    stats = DowngradeStats()
    if not fragments:
        return stats

    norm_pdf = normalize(pdf_text)
    if not norm_pdf:
        # Nothing to validate against — leave flags as-is and warn once.
        logger.warning(
            "verbatim validator: empty normalized pdf_text for %s; skipping",
            source_id,
        )
        return stats

    for frag in fragments:
        if not frag.verbatim:
            continue  # paraphrases are unverifiable by substring — out of scope
        stats.verbatim_claimed += 1

        norm_frag = normalize(frag.text)
        if not norm_frag:
            frag.verbatim = False
            stats.downgraded += 1
            logger.warning(
                "verbatim downgrade (%s): empty normalized fragment", source_id,
            )
            continue

        if norm_frag in norm_pdf:
            stats.verbatim_confirmed += 1
            continue

        # Stage 2: fuzzy rescue against a sliding window sized to the fragment.
        # SequenceMatcher.find_longest_match on the full text is O(n) and fast
        # enough at 50K chars × a handful of fragments; cheaper than chopping
        # windows manually and avoids boundary-miss edge cases.
        matcher = difflib.SequenceMatcher(None, norm_frag, norm_pdf, autojunk=False)
        match = matcher.find_longest_match(0, len(norm_frag), 0, len(norm_pdf))
        if match.size == 0:
            frag.verbatim = False
            stats.downgraded += 1
            logger.warning(
                "verbatim downgrade (%s, substring_match_failed): %s…",
                source_id, norm_frag[:80],
            )
            continue

        # Align the window so the fragment-start (position 0) lines up with
        # the best-match anchor in the PDF. Without this, noise near the
        # fragment's start pushes the anchor forward and the window
        # mis-aligns, under-reporting the true similarity.
        window_start = max(0, match.b - match.a)
        window = norm_pdf[window_start : window_start + len(norm_frag)]
        ratio = difflib.SequenceMatcher(None, norm_frag, window, autojunk=False).ratio()
        if ratio >= _FUZZY_RESCUE_THRESHOLD:
            stats.fuzzy_rescued += 1
            logger.info(
                "verbatim fuzzy-rescue (%s, ratio=%.3f): %s… ↔ %s…",
                source_id, ratio, norm_frag[:60], window[:60],
            )
        else:
            frag.verbatim = False
            stats.downgraded += 1
            logger.warning(
                "verbatim downgrade (%s, fuzzy_match_below_threshold:%.3f): %s…",
                source_id, ratio, norm_frag[:80],
            )

    return stats


def extract_fragments(
    entry: ZoteroEntry,
    pdf_text: str,
    config: KlemmaConfig,
    state: StateManager,
    ai: AIProvider,
    dissertation_context: str = "",
    available_tags: list[str] | None = None,
    klemma_home: Optional[Path] = None,
    project_type: str = "dissertation",
) -> Optional[ExtractionResult]:
    """Extract citation fragments from a paper's PDF text."""

    prompt_path = resolve_prompt("extract.md", klemma_home) if klemma_home else Path(__file__).parent.parent.parent.parent / "prompts" / "extract.md"
    user_prompt = ai.render_prompt(
        prompt_path,
        title=entry.title or "Unknown",
        authors=entry.authors_str,
        year=entry.year or "Unknown",
        journal=entry.container_title or "N/A",
        doi=entry.DOI or "N/A",
        abstract=entry.abstract or "Not available",
        pdf_text=pdf_text,
        dissertation_context=dissertation_context,
        available_tags=", ".join(available_tags) if available_tags else "",
        language=config.ai.language,
        project_type=project_type,
    )

    system = (
        "You are a research assistant extracting citation-worthy fragments from scientific papers. "
        "Output only valid JSON with fragments array."
    )

    from klemma.ai import resolve_task_model

    data = ai.call_json(
        system, user_prompt, max_tokens=4096,
        model_override=resolve_task_model("extract", config.ai),
    )
    if not data:
        logger.error("Failed to extract fragments for %s", entry.id)
        return None

    # Parse fragments
    fragments = []
    for f_data in data.get("fragments", []):
        try:
            fragment = Fragment(
                text=f_data.get("text", ""),
                type=f_data.get("type", "key_idea"),
                chapter=f_data.get("chapter"),
                section=f_data.get("section"),
                relevance=max(1, min(5, f_data.get("relevance", 3))),
                usage_hint=f_data.get("usage_hint", ""),
                page=f_data.get("page"),
                citation_intent=f_data.get("citation_intent"),
                verbatim=bool(f_data.get("verbatim", False)),
            )
            if fragment.text:
                fragments.append(fragment)
        except Exception as e:
            logger.warning("Invalid fragment: %s", e)

    if not fragments:
        logger.warning("No valid fragments extracted for %s", entry.id)
        return None

    # Post-AI integrity check: confirm every `verbatim=true` claim against the
    # paper text and downgrade the ones that don't hold up. Mutates fragments
    # in place; stats surface via ExtractionResult → CLI warning and SaaS
    # job metadata.
    downgrade_stats = validate_verbatim_fragments(fragments, pdf_text, entry.id)

    # Compute content hashes for future content-addressable storage (ADR-014)
    from ..hashing import compute_content_hash

    # Save to database
    fragment_dicts = [
        {
            "text": f.text,
            "type": f.type,
            "chapter": f.chapter,
            "section": f.section,
            "relevance": f.relevance,
            "usage_hint": f.usage_hint,
            "page": f.page,
            "citation_intent": f.citation_intent,
            "verbatim": f.verbatim,
            "content_hash": compute_content_hash(entry.id, f.text, f.page),
        }
        for f in fragments
    ]
    saved = state.save_fragments(entry.id, fragment_dicts)
    logger.info("Saved %d fragments for %s", saved, entry.id)

    return ExtractionResult(
        source_id=entry.id,
        fragments=fragments,
        summary=data.get("summary", ""),
        downgrade_stats=downgrade_stats,
    )


def _format_fragments_for_vault(fragments: list[Fragment]) -> str:
    """Format fragments as Obsidian callouts matching zobsidian note style."""
    lines = []
    for f in sorted(fragments, key=lambda x: (-x.relevance, x.section or "")):
        page_str = f"Стр. {f.page}" if f.page else "—"
        section_str = f"Раздел {f.section}" if f.section else f"Глава {f.chapter}" if f.chapter else "—"
        stars = "\u2b50" * f.relevance
        lines.append(f"> [!quote] {page_str} | {section_str} | {f.type} | {stars}")
        lines.append(f"> \u00ab {f.text} \u00bb")
        if f.usage_hint:
            lines.append(f"> *{f.usage_hint}*")
        lines.append("")
    return "\n".join(lines).rstrip()


def save_fragments_to_vault(
    citekey: str,
    fragments: list[Fragment],
    vault: VaultAdapter,
    entry: Optional[ZoteroEntry] = None,
    config: Optional[KlemmaConfig] = None,
    state: Optional[StateManager] = None,
    pdf_text: Optional[str] = None,
    ai: Optional["AIProvider"] = None,
    entry_lookup: Optional[dict] = None,
    dissertation_context: str = "",
    available_tags: list[str] | None = None,
    klemma_home: Optional[Path] = None,
) -> Optional[str]:
    """Save extracted fragments to the @citekey note in vault.

    Updates the '## 💬 Цитаты для диссертации' section.
    If note doesn't exist and entry/config provided, auto-creates it
    (with AI annotation and reference analysis if pdf_text, ai, entry_lookup given).
    """
    note_name = f"@{citekey}"
    content = _format_fragments_for_vault(fragments)
    section_heading = "## \U0001f4ac Цитаты для диссертации"

    path = vault.update_section(note_name, section_heading, content)
    if path:
        logger.info("Фрагменты сохранены в vault: %s", path)
        return str(path)

    # Auto-create note if entry metadata available
    if entry and config:
        from ..literature.note_factory import create_vault_note
        logger.info("Заметка %s не найдена — создаю из метаданных", note_name)
        create_vault_note(
            citekey, entry, config, vault,
            state=state, pdf_text=pdf_text, ai=ai,
            entry_lookup=entry_lookup,
            dissertation_context=dissertation_context,
            available_tags=available_tags,
            klemma_home=klemma_home,
        )
        path = vault.update_section(note_name, section_heading, content)
        if path:
            logger.info("Фрагменты сохранены в новую заметку: %s", path)
            return str(path)

    logger.warning("Заметка %s не найдена в vault — фрагменты не сохранены", note_name)
    return None


def extract_from_citekey(
    citekey: str,
    config: KlemmaConfig,
    state: StateManager,
    ai: AIProvider,
    pdf_extractor: PDFExtractor,
    pdf_search_paths: list[Path],
    pdf_lookup: Optional[dict[str, str]] = None,
    entry_lookup: Optional[dict[str, ZoteroEntry]] = None,
    dissertation_context: str = "",
    available_tags: list[str] | None = None,
    klemma_home: Optional[Path] = None,
    project_type: str = "dissertation",
) -> Optional[ExtractionResult]:
    """Full extraction pipeline: find source, get PDF, extract fragments."""

    # Check if source exists in state
    source = state.get_source(citekey)
    if not source:
        logger.error("Source %s not found in database", citekey)
        return None

    # Get rich entry from lookup or build minimal one
    entry = (entry_lookup or {}).get(citekey) or ZoteroEntry(id=citekey, title=citekey)

    # Try to find PDF
    pdf_path = pdf_extractor.find_pdf(
        citekey,
        pdf_search_paths,
        entry_title=entry.title or "",
        direct_path=source.get("pdf_path") or entry.pdf_path,
        pdf_lookup=pdf_lookup,
    )

    if not pdf_path:
        logger.error("PDF not found for %s", citekey)
        return None

    pdf_text = pdf_extractor.extract(pdf_path)
    if not pdf_text or len(pdf_text) < config.processing.min_pdf_length:
        logger.error("PDF text too short or extraction failed for %s", citekey)
        return None

    return extract_fragments(
        entry, pdf_text, config, state, ai,
        dissertation_context=dissertation_context,
        available_tags=available_tags,
        klemma_home=klemma_home,
        project_type=project_type,
    )
