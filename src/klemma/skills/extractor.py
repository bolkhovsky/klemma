"""Fragment extraction skill — extracts citation fragments from PDFs."""

import difflib
import logging
import unicodedata
from pathlib import Path
from typing import Optional

from ..ai import AIProvider
from ..config import KlemmaConfig, resolve_prompt
from ..literature.models import DowngradeStats, ExtractionResult, Fragment, ZoteroEntry
from ..literature.pdf import PDFExtractor
from ..state import StateManager
from ..text_normalize import normalize, normalize_with_map
from ..vault import VaultAdapter
from .extract_engine import (  # noqa: F401  (re-exported)
    VERBATIM_VALIDATION_CAP_LARGE,
    VERBATIM_VALIDATION_CAP_SMALL,
    ExtractedFragment,
    ExtractionOutcome,
)

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

    Caller must pass the full normalized PDF text. Under chunked extraction,
    `process_source` / `reprocess_paper` build it from `extract_pages()` and
    cap it via ``VERBATIM_VALIDATION_CAP_LARGE`` before passing here.
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


def _raw_span(source_text: str, idx_map: list[int], a: int, b: int) -> tuple[int, int]:
    """Translate a normalized-space half-open span [a, b) into raw coordinates.

    The end is the raw index right after the last matched char's combining
    sequence, so spans never cut a base char away from its combining marks.
    """
    start = idx_map[a]
    end = idx_map[b - 1] + 1
    while end < len(source_text) and unicodedata.combining(source_text[end]):
        end += 1
    return start, end


def locate_fragment_span(
    fragment_text: str,
    source_text: str,
) -> tuple[int, int] | None:
    """Locate a fragment inside the raw source text; return its span or None.

    Match happens in normalized space (same pipeline as
    ``validate_verbatim_fragments``: exact substring first, then the difflib
    window rescue at ``_FUZZY_RESCUE_THRESHOLD``), and the hit is mapped back
    into raw ``source_text`` coordinates via ``normalize_with_map`` — so the
    returned span indexes directly into the sidecar canonical text.
    """
    norm_frag = normalize(fragment_text)
    norm_src, idx_map = normalize_with_map(source_text)
    if not norm_frag or not norm_src:
        return None

    pos = norm_src.find(norm_frag)
    if pos >= 0:
        return _raw_span(source_text, idx_map, pos, pos + len(norm_frag))

    # Fuzzy rescue — mirrors the window logic in validate_verbatim_fragments.
    matcher = difflib.SequenceMatcher(None, norm_frag, norm_src, autojunk=False)
    match = matcher.find_longest_match(0, len(norm_frag), 0, len(norm_src))
    if match.size == 0:
        return None
    window_start = max(0, match.b - match.a)
    window_end = min(window_start + len(norm_frag), len(norm_src))
    window = norm_src[window_start:window_end]
    ratio = difflib.SequenceMatcher(None, norm_frag, window, autojunk=False).ratio()
    if ratio < _FUZZY_RESCUE_THRESHOLD:
        return None
    return _raw_span(source_text, idx_map, window_start, window_end)


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
    *,
    pages: Optional[list[str]] = None,
    outline_digest: str = "",
    mode: str = "standard",
) -> Optional[ExtractionResult]:
    """Extract citation fragments from a paper and persist them (CLI path).

    Thin wrapper over the pure engine (``extract_engine.extract_from_pages``):
    when ``pages`` is given the full text is chunked and every chunk goes to
    the model — ``config.ai.max_pdf_chars`` no longer limits extraction.
    Without ``pages`` (online sources, legacy callers) ``pdf_text`` is sent as a
    single chunk. Persistence stays here: fragments are saved to the project
    state exactly as before; run lifecycle/publication arrives with plan C2.
    """
    from .extract_engine import Budget, extract_from_pages

    prompt_path = (
        resolve_prompt("extract.md", klemma_home)
        if klemma_home
        else Path(__file__).parent.parent.parent.parent / "prompts" / "extract.md"
    )
    prompt_vars = {
        "dissertation_context": dissertation_context,
        "available_tags": ", ".join(available_tags) if available_tags else "",
        "language": config.ai.language,
        "project_type": project_type,
        "outline_digest": outline_digest,
    }

    from klemma.ai import resolve_task_model

    ai_cfg = config.ai
    outcome = extract_from_pages(
        pages,
        entry,
        prompt_path,
        prompt_vars,
        ai,
        text=None if pages else pdf_text,
        chunk_size=getattr(ai_cfg, "chunk_size", 25_000),
        overlap=getattr(ai_cfg, "chunk_overlap", 2_000),
        min_chunk_chars=getattr(ai_cfg, "min_chunk_chars", 4_000),
        max_tokens_cap=getattr(ai_cfg, "max_tokens_cap", 8_192),
        mode=mode,
        budget=Budget(
            max_input_tokens=int(getattr(ai_cfg, "budget_max_input_tokens", 0) or 0),
            max_output_tokens=int(getattr(ai_cfg, "budget_max_output_tokens", 0) or 0),
            max_cost_usd=getattr(ai_cfg, "budget_max_cost_usd", None),
        ),
        model_override=resolve_task_model("extract", ai_cfg),
        pricing=getattr(ai_cfg, "pricing", None) or None,
    )

    if outcome.error:
        logger.error("Extraction aborted for %s: %s", entry.id, outcome.error)
        return None
    if not outcome.fragments:
        logger.warning("No valid fragments extracted for %s", entry.id)
        return None
    if outcome.failed_chunks:
        logger.warning(
            "%s: %d/%d chunk(s) failed — coverage %.1f%%",
            entry.id, outcome.failed_chunks, outcome.leaf_chunks, outcome.coverage.ratio * 100,
        )

    fragments = outcome.plain_fragments

    # Compute content hashes for future content-addressable storage (ADR-014)
    from ..hashing import compute_content_hash

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
        summary=outcome.summary,
        downgrade_stats=outcome.downgrade_stats,
        chunk_total=outcome.leaf_chunks,
        failed_chunks=outcome.failed_chunks,
        coverage_ratio=outcome.coverage.ratio,
        validation_incomplete=outcome.validation_incomplete,
        prompt_hash=outcome.prompt_hash,
        model=outcome.model,
        tokens_in=outcome.tokens_in,
        tokens_out=outcome.tokens_out,
        cost_usd=outcome.cost_usd,
        key_references=outcome.key_refs,
        spans=[
            (ef.char_start, ef.char_end) if ef.char_start is not None else None
            for ef in outcome.fragments
        ],
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
