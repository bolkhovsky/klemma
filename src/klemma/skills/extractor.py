"""Fragment extraction skill — extracts citation fragments from PDFs."""

import logging
from pathlib import Path
from typing import Optional

from ..ai import AIProvider
from ..config import KlemmaConfig, resolve_prompt
from ..literature.models import ExtractionResult, Fragment, ZoteroEntry
from ..literature.pdf import PDFExtractor
from ..state import StateManager
from ..vault import VaultAdapter
from .extract_engine import (  # noqa: F401  (re-exported: public API lives in the engine)
    _FUZZY_RESCUE_THRESHOLD,
    VERBATIM_VALIDATION_CAP_LARGE,
    VERBATIM_VALIDATION_CAP_SMALL,
    ExtractedFragment,
    ExtractionOutcome,
    locate_fragment_span,
    validate_verbatim_fragments,
)

logger = logging.getLogger(__name__)

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
    replace_existing: bool = False,
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
    # Destructive replacement only when the new extraction is complete: a
    # partial result (failed chunk / incomplete coverage) is merged on top of
    # the old corpus instead of replacing it (Codex P1 on PR-A).
    if replace_existing:
        if outcome.failed_chunks == 0 and outcome.coverage.complete and not outcome.validation_incomplete:
            state.delete_fragments(entry.id)
        else:
            logger.warning(
                "%s: reprocess is partial (%d failed chunk(s), coverage %.1f%%) — "
                "existing fragments preserved, new ones merged",
                entry.id, outcome.failed_chunks, outcome.coverage.ratio * 100,
            )
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
        rendered_prompt_hash=outcome.rendered_prompt_hash,
        model=outcome.model or (resolve_task_model("extract", ai_cfg) or ai_cfg.model or ""),
        tokens_in=outcome.tokens_in,
        tokens_out=outcome.tokens_out,
        cost_usd=outcome.cost_usd,
        key_references=outcome.key_refs,
        spans=[
            (ef.char_start, ef.char_end) if ef.char_start is not None else None
            for ef in outcome.fragments
        ],
        verbatim_statuses=[ef.verbatim_status for ef in outcome.fragments],
        source_locators=[ef.source_locator for ef in outcome.fragments],
    )


def fragments_from_rows(rows: list[dict]) -> list[Fragment]:
    """Rebuild ``Fragment`` models from ``state.get_fragments()`` rows.

    Used to render the *merged* stored corpus into the vault after a partial
    ``--force`` reprocess, so the note never shows a lossy subset.
    """
    out: list[Fragment] = []
    for r in rows:
        text = (r.get("fragment_text") or "").strip()
        if not text:
            continue
        try:
            relevance = int(r.get("relevance_score") or 3)
        except (TypeError, ValueError):
            relevance = 3
        out.append(Fragment(
            text=text,
            type=r.get("fragment_type") or "key_idea",
            chapter=r.get("chapter") if isinstance(r.get("chapter"), int) else None,
            section=r.get("section") or None,
            relevance=max(1, min(5, relevance)),
            usage_hint=r.get("usage_hint") or "",
            page=r.get("page_number") if isinstance(r.get("page_number"), int) else None,
            verbatim=bool(r.get("verbatim", False)),
        ))
    return out


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

    pages = pdf_extractor.extract_pages(pdf_path)
    pdf_text = pdf_extractor.format_for_ai(pages) if pages else None
    if not pdf_text or len(pdf_text) < config.processing.min_pdf_length:
        logger.error("PDF text too short or extraction failed for %s", citekey)
        return None

    return extract_fragments(
        entry, pdf_text, config, state, ai,
        dissertation_context=dissertation_context,
        available_tags=available_tags,
        klemma_home=klemma_home,
        project_type=project_type,
        pages=pages,
    )
