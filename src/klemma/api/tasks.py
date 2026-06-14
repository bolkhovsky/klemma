"""Async task definitions for rq worker (ADR-009, #186).

Tasks are enqueued by API endpoints and executed by the rq worker process.
Each task receives primitive arguments (strings, dicts) — no store objects
or connections, since the worker runs in a separate process.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


def _create_ai_provider(*, json_mode: bool = False):
    """Create an AI provider from environment variables.

    Supports: ANTHROPIC_API_KEY, OPENAI_API_KEY via litellm backend.
    Model: KLEMMA_AI_MODEL env var (default: anthropic/claude-sonnet-4-20250514).

    Args:
        json_mode: When True, enables structured JSON output via
            ``response_format={"type": "json_object"}`` for LiteLLM. Use for
            tasks that parse the response as JSON (chunked extraction,
            curation, outline generation). Free-form text tasks (draft,
            research) should keep the default False.
    """
    from klemma.ai import create_ai
    from klemma.config import AIConfig

    model = os.getenv("KLEMMA_AI_MODEL", "anthropic/claude-sonnet-4-20250514")
    api_keys = {}
    if os.getenv("ANTHROPIC_API_KEY"):
        api_keys["anthropic"] = os.environ["ANTHROPIC_API_KEY"]
    if os.getenv("OPENAI_API_KEY"):
        api_keys["openai"] = os.environ["OPENAI_API_KEY"]

    config = AIConfig(backend="litellm", model=model, json_mode=json_mode)
    config._resolved_api_keys = api_keys
    return create_ai(config), config


def _validate_embeddings_config() -> None:
    """Assert that embeddings are configured for local-only (Ollama) use.

    Called at startup (app.py lifespan) and at worker module import time so
    the process fails fast before accepting any jobs.

    Set ``KLEMMA_EMBEDDINGS_ALLOW_REMOTE=1`` in CI/test environments to bypass.
    """
    if os.getenv("KLEMMA_EMBEDDINGS_ALLOW_REMOTE", "").strip() == "1":
        return

    from klemma.api.constants import (
        EMBEDDINGS_REQUIRED_BACKEND,
        EMBEDDINGS_REQUIRED_MODEL_PREFIX,
    )

    backend = os.getenv("KLEMMA_EMBEDDINGS_BACKEND", "").strip()
    model = os.getenv("KLEMMA_EMBEDDINGS_MODEL", "").strip()
    base_url = os.getenv("KLEMMA_EMBEDDINGS_BASE_URL", "").strip()

    errors = []
    if not backend:
        errors.append("KLEMMA_EMBEDDINGS_BACKEND is not set")
    elif backend != EMBEDDINGS_REQUIRED_BACKEND:
        errors.append(f"KLEMMA_EMBEDDINGS_BACKEND must be '{EMBEDDINGS_REQUIRED_BACKEND}', got '{backend}'")

    if not model:
        errors.append("KLEMMA_EMBEDDINGS_MODEL is not set")
    elif not model.startswith(EMBEDDINGS_REQUIRED_MODEL_PREFIX):
        errors.append(f"KLEMMA_EMBEDDINGS_MODEL must start with '{EMBEDDINGS_REQUIRED_MODEL_PREFIX}', got '{model}'")

    if not base_url:
        errors.append("KLEMMA_EMBEDDINGS_BASE_URL is not set")

    if errors:
        raise RuntimeError(
            "SaaS embeddings must be local (litellm + ollama/*). "
            f"Errors: {'; '.join(errors)}. "
            "Set KLEMMA_EMBEDDINGS_ALLOW_REMOTE=1 to bypass in CI/test environments."
        )


def _create_embeddings_provider():
    """Create an embedding provider from environment variables.

    Enforces local-only (Ollama) embeddings in SaaS — see _validate_embeddings_config().
    Returns None only when validation was bypassed via KLEMMA_EMBEDDINGS_ALLOW_REMOTE=1
    and KLEMMA_EMBEDDINGS_BACKEND is empty (i.e. embeddings explicitly disabled in tests).
    """
    backend = os.getenv("KLEMMA_EMBEDDINGS_BACKEND", "").strip()
    allow_remote = os.getenv("KLEMMA_EMBEDDINGS_ALLOW_REMOTE", "").strip() == "1"

    if not backend:
        if allow_remote:
            # CI/test with embeddings explicitly disabled — allow
            return None
        # Should have been caught at startup; raise here as backstop
        raise RuntimeError(
            "KLEMMA_EMBEDDINGS_BACKEND is not set. "
            "SaaS requires local Ollama embeddings. "
            "Set KLEMMA_EMBEDDINGS_ALLOW_REMOTE=1 in tests to disable."
        )

    # Re-run validation as backstop (startup may have been bypassed in tests)
    _validate_embeddings_config()

    from klemma.embeddings import create_embeddings

    config = {
        "backend": backend,
        "model": os.getenv("KLEMMA_EMBEDDINGS_MODEL", "ollama/bge-m3"),
        "base_url": os.getenv("KLEMMA_EMBEDDINGS_BASE_URL"),
        "timeout": int(os.getenv("KLEMMA_EMBEDDINGS_TIMEOUT", "60")),
    }
    dim = os.getenv("KLEMMA_EMBEDDINGS_DIM")
    if dim:
        config["dim"] = int(dim)
    return create_embeddings(config)


def _run_chunked_extraction(
    ai,
    ai_config,
    entry,
    chunks,
    paper_id: str,
    source_label: str,
    prompt_path,
    *,
    dissertation_context: str = "",
    available_tags: str = "",
    user_store=None,
    user_id: str = "",
    full_text: str = "",
) -> dict | None:
    """Run chunked AI extraction over a list of ChunkRecords.

    Renders the extraction prompt once per chunk, calls the AI, accumulates
    fragments across all chunks, then validates verbatim claims once against
    the full document text (capped via VERBATIM_VALIDATION_CAP_*).

    Args:
        ai: AI provider instance (must implement render_prompt + call_with_meta).
        ai_config: AIConfig — used for model name when recording token usage.
        entry: ZoteroEntry built from the paper metadata.
        chunks: list[ChunkRecord] — pre-built overlapping chunks from build_chunks_from_pages.
        paper_id: Global paper id (used as prefix for content hash).
        source_label: Citekey or paper_id — used only in log messages.
        prompt_path: Path to the extract.md prompt template.
        dissertation_context: Section list rendered for the AI (empty if no project).
        available_tags: Comma-separated section ids (empty if no project).
        user_store: LocalUserStore instance for recording per-chunk token usage, or None.
        user_id: User id string for token recording (ignored when user_store is None).
        full_text: Concatenated page text used for verbatim validation. Empty
            string disables validation (older callers / tests).

    Returns:
        dict with keys: fragments, key_refs, fragment_ai_sections, predicted_sections,
        predicted_chapters, chunks_processed, failed_chunks, downgrade_stats
        — or None if every chunk produced zero fragments.
    """
    from klemma.ai import extract_json
    from klemma.hashing import compute_content_hash
    from klemma.literature.models import DowngradeStats, Fragment
    from klemma.models import FragmentRecord
    from klemma.skills.extractor import validate_verbatim_fragments

    from .constants import VERBATIM_VALIDATION_CAP_LARGE, VERBATIM_VALIDATION_CAP_SMALL

    system = (
        "You are a research assistant extracting citation-worthy fragments from scientific papers. "
        "Output only valid JSON with fragments array and key_references array."
    )

    fragments: list[FragmentRecord] = []
    all_pydantic: list[Fragment] = []
    fragment_ai_sections: dict[str, str | None] = {}
    predicted_sections: set[str] = set()
    predicted_chapters: set[int] = set()
    all_key_refs: list[dict] = []
    chunk_total = len(chunks)
    failed_chunks = 0

    for chunk in chunks:
        user_prompt = ai.render_prompt(
            prompt_path,
            title=entry.title or "Unknown",
            authors=entry.authors_str,
            year=entry.year or "Unknown",
            journal=entry.container_title or "N/A",
            doi=entry.DOI or "N/A",
            abstract=entry.abstract or "Not available",
            pdf_text=chunk.text,
            chunk_index=chunk.index,
            chunk_total=chunk_total,
            char_start=chunk.char_start,
            char_end=chunk.char_end,
            dissertation_context=dissertation_context,
            available_tags=available_tags,
            language="ru",
            project_type="research",
        )
        chunk_chars = len(chunk.text)
        adaptive_max_tokens = max(2048, min(8192, chunk_chars // 4))
        chunk_result = ai.call_with_meta(system, user_prompt, max_tokens=adaptive_max_tokens)

        if not chunk_result or not chunk_result.text:
            logger.warning(
                "Chunk %d/%d returned no AI response for %s — skipping",
                chunk.index + 1, chunk_total, source_label,
            )
            failed_chunks += 1
            continue

        # Record token usage per chunk
        if user_store and user_id:
            user_store.record_usage(
                user_id=user_id,
                operation="process_source",
                model=ai_config.model,
                input_tokens=chunk_result.input_tokens or 0,
                output_tokens=chunk_result.output_tokens or 0,
                citekey=source_label,
            )

        data = extract_json(chunk_result.text)
        if not data:
            # Repair retry (#381): ask the AI to repair its own malformed JSON.
            # Tracks tokens separately as `process_source_repair` for visibility.
            repair_system = (
                "You receive malformed JSON. Output ONLY a valid JSON object that "
                "preserves every field and value exactly. Do not add commentary, "
                "do not change content, do not drop fragments. Fix only the syntax."
            )
            repair_user = f"Repair this malformed JSON:\n\n{chunk_result.text}"
            repair_result = ai.call_with_meta(
                repair_system, repair_user,
                max_tokens=min(8192, adaptive_max_tokens * 2),
            )
            if repair_result and repair_result.text:
                if user_store and user_id:
                    user_store.record_usage(
                        user_id=user_id,
                        operation="process_source_repair",
                        model=ai_config.model,
                        input_tokens=repair_result.input_tokens or 0,
                        output_tokens=repair_result.output_tokens or 0,
                        citekey=source_label,
                    )
                data = extract_json(repair_result.text)
                if data:
                    logger.info(
                        "Chunk %d/%d: AI repair retry recovered JSON for %s",
                        chunk.index + 1, chunk_total, source_label,
                    )
            if not data:
                logger.warning(
                    "Chunk %d/%d: failed to parse AI JSON for %s "
                    "(repair retry also failed) — skipping",
                    chunk.index + 1, chunk_total, source_label,
                )
                failed_chunks += 1
                continue

        # Collect key_references (bibliography; last chunk usually has the most)
        all_key_refs.extend(data.get("key_references", []))

        # Parse fragments from this chunk into parallel record/pydantic arrays.
        # Validation is deferred until after all chunks are processed so the
        # validator can match against the full document text (#379).
        for f_data in data.get("fragments", []):
            text = f_data.get("text", "").strip()
            if not text:
                continue
            fragment_id = compute_content_hash(paper_id, text, f_data.get("page"))
            claimed_verbatim = bool(f_data.get("verbatim", False))
            all_pydantic.append(Fragment(text=text, verbatim=claimed_verbatim))
            fragments.append(FragmentRecord(
                fragment_id=fragment_id,
                paper_id=paper_id,
                fragment_text=text,
                fragment_type=f_data.get("type", "key_idea"),
                page_number=f_data.get("page"),
                citation_intent=f_data.get("citation_intent"),
                verbatim=claimed_verbatim,
                content_hash=fragment_id,
            ))
            sec = str(f_data.get("section", "")).strip()
            fragment_ai_sections[fragment_id] = sec or None
            if sec:
                predicted_sections.add(sec)
            chap = f_data.get("chapter")
            if isinstance(chap, int):
                predicted_chapters.add(chap)

    if not fragments:
        return None

    if failed_chunks > 0:
        logger.warning(
            "%s: %d/%d chunk(s) failed AI extraction — result covers only part of the PDF",
            source_label, failed_chunks, chunk_total,
        )

    # Single full-text verbatim validation (#379). Per-chunk slices cause
    # false-negative downgrades when the AI quotes text from outside its
    # own chunk window (boundary text, cross-chunk quotes, prompt context).
    downgrade_stats = DowngradeStats()
    if all_pydantic:
        # Production callers (process_source, reprocess_paper) pass the same
        # full_text they cache in papers.raw_text. Direct callers (helper-level
        # tests, future callers) that pass full_text="" fall back to the
        # joined chunk text — broader than any single chunk, so cross-chunk
        # quotes still validate cleanly even without an explicit full_text.
        source_text = full_text or "\n\n".join(chunk.text for chunk in chunks)
        if len(source_text) >= VERBATIM_VALIDATION_CAP_SMALL:
            validation_text = source_text[:VERBATIM_VALIDATION_CAP_LARGE]
            if len(source_text) > VERBATIM_VALIDATION_CAP_LARGE:
                logger.warning(
                    "verbatim validator (%s): source text %d chars truncated to %d for "
                    "validation; fragments quoting beyond the cap may be downgraded",
                    source_label, len(source_text), VERBATIM_VALIDATION_CAP_LARGE,
                )
        else:
            validation_text = source_text
        downgrade_stats = validate_verbatim_fragments(
            all_pydantic, validation_text, source_label,
        )
        for record, pyd in zip(fragments, all_pydantic):
            record.verbatim = pyd.verbatim
        if downgrade_stats.downgraded:
            logger.warning(
                "verbatim validator (%s): %d/%d downgraded across %d chunk(s)",
                source_label, downgrade_stats.downgraded,
                downgrade_stats.verbatim_claimed, chunk_total,
            )

    fragments = dedup_fragments_by_prefix(fragments, min_prefix=100)

    return {
        "fragments": fragments,
        "key_refs": all_key_refs,
        "fragment_ai_sections": fragment_ai_sections,
        "predicted_sections": predicted_sections,
        "predicted_chapters": predicted_chapters,
        "chunks_processed": chunk_total - failed_chunks,
        "failed_chunks": failed_chunks,
        "downgrade_stats": downgrade_stats,
    }


def _mirror_research_report(data_path: Path, project_id: str, section: str, text: str, model: str) -> None:
    """Write research report to MD file for klemma-cli sync pull.

    SQLite is the primary store; this file is a read-only mirror.
    Path: {data_dir}/research/{project_id}/{section}.md
    """
    from datetime import datetime, timezone

    try:
        path = data_path / "research" / project_id / f"{section}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        created_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        content = (
            f"---\nsection: {section}\nproject_id: {project_id}\n"
            f"model: {model}\ncreated_at: {created_at}\n---\n\n{text}"
        )
        path.write_text(content, encoding="utf-8")
    except Exception as exc:
        logger.warning("Failed to mirror research report to MD for %s/%s: %s", project_id, section, exc)


def process_source(paper_id: str, citekey: str, data_dir: str, user_id: str = "", project_id: str | None = None, force: bool = False) -> dict:
    """Extract fragments from a paper's PDF.

    This is the rq task equivalent of `klemma process <citekey>`.
    Runs in the worker process — initializes its own stores.

    Pipeline: FileStore → PDFExtractor → AI extraction → PaperStore.

    Returns a dict with status and fragment count.
    """
    from klemma.stores.file_store import LocalFileStore
    from klemma.stores.paper_store import LocalPaperStore
    from klemma.stores.user_library import LocalUserLibrary
    from klemma.stores.user_store import LocalUserStore

    data_path = Path(data_dir)
    library_db = data_path / "library.db"
    paper_store = LocalPaperStore(library_db)
    user_library = LocalUserLibrary(library_db)
    file_store = LocalFileStore(data_path / "files")
    user_store = LocalUserStore(data_path / "users.db") if user_id else None

    # Check token limit
    if user_store and user_id and not user_store.check_token_limit(user_id):
        user_library.update_status(citekey, "pending", user_id=user_id or None)
        return {"status": "error", "detail": "Token limit exhausted"}

    # Check paper exists
    paper = paper_store.get_paper_by_id(paper_id)
    if paper is None:
        user_library.update_status(citekey, "failed", user_id=user_id or None)
        return {"status": "error", "detail": f"Paper {paper_id} not found"}

    # Check if already processed (has fragments)
    existing = paper_store.get_fragments(paper_id)
    if existing and not force:
        # Backfill suggestions for papers processed before auto-suggest feature
        if project_id and user_store and user_id:
            try:
                _proj = user_store.get_project_by_id(project_id)
                if not _proj or _proj.get("user_id") != user_id:
                    logger.warning("Backfill skipped: project %s not found or not owned by %s", project_id, user_id)
                else:
                    decided_ids = user_store.get_curated_fragment_ids(project_id)
                    existing_suggested = {
                        c["fragment_id"]
                        for c in user_store.get_curated(project_id, verdict="suggested")
                    }
                    uncurated = [
                        f for f in existing
                        if f.fragment_id not in decided_ids
                        and f.fragment_id not in existing_suggested
                    ]
                    if uncurated:
                        from klemma.section_types import auto_assign_section as _auto_assign
                        _outline = _proj.get("outline")
                        suggestions = [{
                            "fragment_id": f.fragment_id,
                            "citekey": citekey,
                            "verdict": "suggested",
                            "assigned_section": _auto_assign(f.citation_intent, _outline),
                        } for f in uncurated]
                        user_store.curate_fragments(project_id, suggestions)
                        logger.info("Backfilled %d suggestions for %s", len(suggestions), citekey)
            except Exception as exc:
                logger.warning("Backfill suggestion failed for %s (non-fatal): %s", citekey, exc)
        user_library.update_status(citekey, "completed", user_id=user_id or None)
        return {
            "status": "already_processed",
            "citekey": citekey,
            "fragment_count": len(existing),
        }
    _force_delete_pending = False
    if existing and force:
        # Safety: don't delete shared global fragments if other users reference this paper
        other_owners = paper_store.count_paper_owners(paper_id)
        if other_owners > 1:
            logger.warning(
                "Force reprocess skipped for %s — paper %s is shared by %d users",
                citekey, paper_id, other_owners,
            )
            user_library.update_status(citekey, "completed", user_id=user_id or None)
            return {
                "status": "already_processed",
                "citekey": citekey,
                "fragment_count": len(existing),
            }
        # Deletion deferred until after extraction succeeds — abort if any chunks fail
        # to preserve the complete existing corpus record (mirrors reprocess_paper guard).
        _force_delete_pending = True
        logger.info("Force reprocess queued for %s — old fragments held until extraction completes", citekey)

    # Mark as processing
    user_library.update_status(citekey, "processing", user_id=user_id or None)

    # Find PDF file in FileStore
    paper_dir = file_store.get_paper_dir(paper_id)
    pdf_files = list(paper_dir.glob("*.pdf")) if paper_dir.is_dir() else []
    if not pdf_files:
        user_library.update_status(citekey, "failed", user_id=user_id or None)
        return {"status": "error", "detail": f"PDF not found for paper {paper_id}"}

    # Extract PDF text (page-aware, no truncation at source)
    try:
        from klemma.literature.pdf import PDFExtractor, build_chunks_from_pages

        pdf_path = pdf_files[0]
        extractor = PDFExtractor()
        pages = extractor.extract_pages(pdf_path)
        if not pages or sum(len(p) for p in pages) < 500:
            user_library.update_status(citekey, "failed", user_id=user_id or None)
            return {"status": "error", "detail": "PDF text too short or extraction failed"}

        # Full concatenated text (no truncation) used for caching, abstract/DOI extraction.
        full_text = "\n\n".join(f"[Page {i + 1}]\n{p}" for i, p in enumerate(pages))
        logger.info("Extracted %d chars from %d pages for %s", len(full_text), len(pages), citekey)

        # Cache the full PDF text so verbatim validator and find-in-page UX
        # can search the same string the AI saw.
        try:
            paper_store.update_paper_raw_text(paper_id, full_text)
        except Exception as cache_exc:
            logger.warning("raw_text cache write failed for %s (non-fatal): %s", citekey, cache_exc)

        # Build overlapping chunks for per-chunk AI extraction (removes 50K truncation)
        chunks = build_chunks_from_pages(pages)
        logger.info("Split %s into %d chunk(s) for extraction", citekey, len(chunks))

    except Exception as exc:
        logger.error("PDF extraction failed for %s: %s", citekey, exc)
        user_library.update_status(citekey, "failed", user_id=user_id or None)
        return {"status": "error", "detail": f"PDF extraction failed: {exc}"}

    # Extract abstract directly from PDF text (no network call).
    try:
        from klemma.literature.metadata import _extract_abstract_from_text

        abstract = _extract_abstract_from_text(full_text)
        if abstract:
            paper_store.update_paper_metadata(paper_id, abstract=abstract)
            logger.info("Extracted abstract from PDF text for %s (%d chars)", citekey, len(abstract))
    except Exception as exc:
        logger.warning("Abstract extraction failed for %s (non-fatal): %s", citekey, exc)

    # Auto-enrich metadata via DOI lookup on CrossRef (non-fatal).
    try:
        from klemma.literature.metadata import (
            _extract_doi_from_text,
            lookup_crossref_by_doi,
        )
        doi = _extract_doi_from_text(full_text, max_chars=None)
        if doi:
            meta = lookup_crossref_by_doi(doi, timeout=5)
            if meta:
                update_kwargs = {
                    k: meta[k] for k in ("title", "authors", "year", "doi")
                    if meta.get(k)
                }
                if update_kwargs:
                    paper_store.update_paper_metadata(paper_id, **update_kwargs)
                    logger.info(
                        "CrossRef-enriched metadata for %s via %s: %s",
                        citekey, doi, list(update_kwargs.keys()),
                    )
    except Exception as exc:
        logger.warning("Auto-metadata enrichment failed for %s (non-fatal): %s", citekey, exc)

    # Check AI config
    if not os.getenv("ANTHROPIC_API_KEY") and not os.getenv("OPENAI_API_KEY"):
        user_library.update_status(citekey, "pending", user_id=user_id or None)
        return {
            "status": "pending",
            "citekey": citekey,
            "detail": "No AI API key configured (set ANTHROPIC_API_KEY or OPENAI_API_KEY)",
        }

    # Load project outline for section assignment context
    dissertation_context = ""
    available_tags = ""
    if project_id and user_store:
        try:
            project = user_store.get_project_by_id(project_id)
            if project and project.get("outline"):
                outline = project["outline"]
                dissertation_context = "Dissertation sections:\n" + "\n".join(
                    f"  {s['id']}: {s['name']}" for s in outline
                )
                available_tags = ", ".join(s["id"] for s in outline)
        except Exception as _exc:
            logger.warning("Failed to load project outline for %s: %s", project_id, _exc)

    # Create AI provider and extract fragments. json_mode=True forces
    # response_format="json_object" so chunked extraction calls get strict
    # JSON output instead of free-form text wrapped in JSON (#381).
    try:
        ai, ai_config = _create_ai_provider(json_mode=True)

        from klemma.ai import extract_json
        from klemma.config import _SHIPPED_PROMPTS_DIR
        from klemma.literature.models import ZoteroEntry

        # Build minimal entry from paper metadata
        entry = ZoteroEntry(
            id=citekey,
            title=paper.title or citekey,
            author=[{"family": a.strip()} for a in (paper.authors or "").split(",") if a.strip()],
            issued={"date-parts": [[paper.year]]} if paper.year else None,
            DOI=paper.doi,
            abstract=paper.abstract or "",
        )

        prompt_path = _SHIPPED_PROMPTS_DIR / "extract.md"

        # --- Per-chunk extraction via shared helper ---
        extraction = _run_chunked_extraction(
            ai, ai_config, entry, chunks, paper_id, citekey, prompt_path,
            dissertation_context=dissertation_context,
            available_tags=available_tags,
            user_store=user_store,
            user_id=user_id,
            full_text=full_text,
        )

        if extraction is None:
            if _force_delete_pending:
                # All chunks failed but old fragments were never deleted — corpus is intact.
                # Revert to "completed" so the source stays visible to downstream filters.
                user_library.update_status(citekey, "completed", user_id=user_id or None)
                logger.error(
                    "Force reprocess failed for %s: all chunks produced zero fragments — "
                    "existing fragments preserved, status reverted to completed",
                    citekey,
                )
                return {
                    "status": "error",
                    "citekey": citekey,
                    "detail": "All chunks failed AI extraction; existing fragments preserved",
                }
            user_library.update_status(citekey, "failed", user_id=user_id or None)
            return {"status": "error", "detail": "No fragments extracted from PDF"}

        fragments = extraction["fragments"]
        fragment_ai_sections = extraction["fragment_ai_sections"]
        predicted_sections = extraction["predicted_sections"]
        predicted_chapters = extraction["predicted_chapters"]
        all_key_refs = extraction["key_refs"]
        downgrade_stats = extraction["downgrade_stats"]
        failed_chunks = extraction.get("failed_chunks", 0)
        chunks_processed = extraction.get("chunks_processed", len(chunks))

        if downgrade_stats.downgraded:
            logger.warning(
                "verbatim validator (%s total): %d/%d claimed fragments downgraded "
                "(%d fuzzy-rescued, %d confirmed)",
                citekey,
                downgrade_stats.downgraded,
                downgrade_stats.verbatim_claimed,
                downgrade_stats.fuzzy_rescued,
                downgrade_stats.verbatim_confirmed,
            )

        # Force-reprocess: abort if extraction was partial to preserve existing corpus.
        # Only delete old fragments after confirming all chunks succeeded.
        if _force_delete_pending:
            if failed_chunks > 0:
                user_library.update_status(citekey, "completed", user_id=user_id or None)
                logger.error(
                    "Force reprocess aborted for %s: %d/%d chunks failed — "
                    "existing fragments preserved, status reverted to completed",
                    citekey, failed_chunks, failed_chunks + chunks_processed,
                )
                return {
                    "status": "error",
                    "citekey": citekey,
                    "detail": (
                        f"{failed_chunks}/{failed_chunks + chunks_processed} chunks failed; "
                        "existing fragments preserved — retry without --force or fix the AI issue"
                    ),
                    "failed_chunks": failed_chunks,
                    "chunks_processed": chunks_processed,
                }
            deleted = paper_store.delete_fragments(paper_id)
            logger.info("Force reprocess: deleted %d existing fragments for %s", deleted, citekey)

        # Save to paper store
        prompt_hash = ""
        model_name = ai_config.model
        saved = paper_store.save_fragments(paper_id, fragments, prompt_hash, model_name)

        # Auto-embed paper and fragments (non-fatal — fragments are saved regardless).
        # Fragments go through a single batched call when the backend supports
        # it (LiteLLM/Ollama does); others fall back to per-item embed().
        emb = _create_embeddings_provider()
        if emb:
            try:
                paper_vec = emb.embed(paper.title or citekey, paper.abstract or "")
                if paper_vec:
                    paper_store.save_paper_embedding(paper_id, paper_vec, emb.model_name)

                texts = [frag.fragment_text for frag in fragments]
                batch_fn = getattr(emb, "embed_batch", None)
                if callable(batch_fn):
                    vectors = batch_fn(texts)
                else:
                    from klemma.embeddings import _default_embed_batch
                    vectors = _default_embed_batch(emb, texts)

                frag_count = 0
                for frag, vec in zip(fragments, vectors):
                    if vec:
                        paper_store.save_fragment_embedding(
                            frag.fragment_id, vec, emb.model_name
                        )
                        frag_count += 1
                logger.info("Embedded paper + %d fragments for %s", frag_count, citekey)
            except Exception as exc:
                logger.warning("Embedding failed for %s (non-fatal): %s", citekey, exc)

        # Auto-assign sections based on AI predictions (SaaS only — when project context given)
        if project_id and predicted_sections:
            from klemma.stores.project_store import LocalProjectStore
            project_store = LocalProjectStore(data_path / "project.db")
            project_store.set_source_sections(
                citekey, paper_id,
                sorted(predicted_sections),
                sorted(predicted_chapters),
                user_id=user_id or None,
            )
            logger.info(
                "Auto-assigned sections %s for %s (project %s)",
                sorted(predicted_sections), citekey, project_id,
            )

        # Save citation links from bibliography (for reference gap analysis)
        key_refs = all_key_refs
        if not key_refs:
            # Chunks may not have extracted key_references — do a focused bibliography call
            try:
                import re as _re
                # Find the references/bibliography section by heading
                bib_match = _re.search(
                    r'\n(References|REFERENCES|Bibliography|BIBLIOGRAPHY|Список литературы|Литература)\s*\n',
                    full_text,
                )
                if bib_match:
                    bib_start = bib_match.start()
                    bib_text = full_text[bib_start:bib_start + 10000]
                else:
                    bib_text = full_text[-8000:]
                bib_result = ai.call_with_meta(
                    "Extract references from this bibliography section. Return ONLY valid JSON.",
                    (
                        "Extract 10-20 most important references from the bibliography below.\n"
                        "Return JSON: {\"key_references\": [{\"title\": \"...\", \"authors\": \"First Author et al.\", \"year\": 2020}]}\n\n"
                        f"Bibliography:\n{bib_text}"
                    ),
                    max_tokens=4096,
                )
                if bib_result and bib_result.text:
                    bib_data = extract_json(bib_result.text)
                    if bib_data:
                        key_refs = bib_data.get("key_references", [])
                        # Record token usage for the second call
                        if user_store and user_id:
                            user_store.record_usage(
                                user_id=user_id,
                                operation="extract_bibliography",
                                model=ai_config.model,
                                input_tokens=bib_result.input_tokens or 0,
                                output_tokens=bib_result.output_tokens or 0,
                                citekey=citekey,
                            )
            except Exception as exc:
                logger.warning("Bibliography extraction failed for %s (non-fatal): %s", citekey, exc)
        if key_refs:
            links_saved = paper_store.save_citation_links(paper_id, key_refs)
            logger.info("Saved %d citation links for %s", links_saved, citekey)

        # ── Auto-suggest: enqueue as post-hook job (non-blocking) ────────
        auto_suggest_job_id: str | None = None
        auto_sentences_job_id: str | None = None
        if project_id and user_id:
            auto_suggest_job_id = _enqueue_auto_suggest(
                paper_id=paper_id,
                citekey=citekey,
                user_id=user_id,
                project_id=project_id,
                fragment_ids=[f.fragment_id for f in fragments],
                citation_intents={f.fragment_id: f.citation_intent for f in fragments},
                fragment_ai_sections=fragment_ai_sections,
                data_dir=data_dir,
            )
            # ── Auto-generate sentences (ADR-017): ready-to-cite academic
            # paraphrase per fragment, triggered immediately so the user
            # doesn't have to press "Сгенерировать предложения" manually in
            # FragmentReviewView. Runs in parallel with auto-suggest — the
            # curate_fragments upsert uses COALESCE on suggested_text so
            # whichever finishes second won't wipe the other's work.
            auto_sentences_job_id = _enqueue_auto_sentences(
                project_id=project_id,
                citekey=citekey,
                user_id=user_id,
                data_dir=data_dir,
            )

        persist_status = "partial" if failed_chunks > 0 else "completed"
        user_library.update_status(citekey, persist_status, user_id=user_id or None)
        logger.info("Extracted %d fragments for %s (%s) [status=%s]", saved, citekey, paper_id, persist_status)

        result_status = persist_status
        result_dict: dict = {
            "status": result_status,
            "citekey": citekey,
            "fragment_count": saved,
            "chunks_processed": chunks_processed,
            "downgrade_stats": downgrade_stats.as_dict(),
        }
        if failed_chunks > 0:
            result_dict["failed_chunks"] = failed_chunks
        if auto_suggest_job_id:
            result_dict["auto_suggest_job_id"] = auto_suggest_job_id
        if auto_sentences_job_id:
            result_dict["auto_sentences_job_id"] = auto_sentences_job_id
        return result_dict

    except Exception as exc:
        logger.error("AI extraction failed for %s: %s", citekey, exc, exc_info=True)
        user_library.update_status(citekey, "failed", user_id=user_id or None)
        return {"status": "error", "detail": f"Extraction failed: {type(exc).__name__}: {exc}"}


def dedup_fragments_by_prefix(
    fragments: list,
    min_prefix: int = 100,
) -> list:
    """Remove later fragments whose text prefix (≥ min_prefix chars) duplicates an earlier one.

    content_hash (INSERT OR IGNORE) handles identical texts; this pass removes
    near-duplicates produced by overlapping chunk boundaries where the same
    passage starts identically but ends slightly differently.
    """
    seen: set[str] = set()
    out = []
    for frag in fragments:
        prefix = frag.fragment_text[:min_prefix]
        if len(prefix) < min_prefix:
            out.append(frag)
            continue
        if prefix not in seen:
            seen.add(prefix)
            out.append(frag)
    return out


def reprocess_paper(paper_id: str, data_dir: str) -> dict:
    """Re-extract fragments for a paper at the global corpus level.

    Unlike process_source(), this operates on the global paper record directly,
    not on a user-specific citekey.  Safe for shared papers: old data is only
    deleted after the new extraction succeeds, so a failed re-extract leaves
    the existing fragments intact.

    Returns a dict with status, fragment counts, and chunk statistics.
    """
    from klemma.literature.pdf import PDFExtractor, build_chunks_from_pages
    from klemma.stores.file_store import LocalFileStore
    from klemma.stores.paper_store import LocalPaperStore

    data_path = Path(data_dir)
    library_db = data_path / "library.db"
    paper_store = LocalPaperStore(library_db)
    file_store = LocalFileStore(data_path / "files")

    paper = paper_store.get_paper_by_id(paper_id)
    if paper is None:
        return {"status": "error", "detail": f"Paper {paper_id} not found"}

    paper_dir = file_store.get_paper_dir(paper_id)
    pdf_files = list(paper_dir.glob("*.pdf")) if paper_dir.is_dir() else []
    if not pdf_files:
        return {"status": "error", "detail": f"PDF not found for paper {paper_id}"}

    if not os.getenv("ANTHROPIC_API_KEY") and not os.getenv("OPENAI_API_KEY"):
        return {"status": "error", "detail": "No AI API key configured"}

    try:
        extractor = PDFExtractor()
        pages = extractor.extract_pages(pdf_files[0])
        if not pages or sum(len(p) for p in pages) < 500:
            return {"status": "error", "detail": "PDF text too short"}

        full_text = "\n\n".join(f"[Page {i + 1}]\n{p}" for i, p in enumerate(pages))
        chunks = build_chunks_from_pages(pages)
        logger.info("reprocess_paper %s: %d page(s), %d chunk(s)", paper_id, len(pages), len(chunks))

        # json_mode=True for chunked extraction — see process_source for details (#381).
        ai, ai_config = _create_ai_provider(json_mode=True)
        from klemma.config import _SHIPPED_PROMPTS_DIR
        from klemma.literature.models import ZoteroEntry

        entry = ZoteroEntry(
            id=paper.paper_id,
            title=paper.title or paper.paper_id,
            author=[{"family": a.strip()} for a in (paper.authors or "").split(",") if a.strip()],
            issued={"date-parts": [[paper.year]]} if paper.year else None,
            DOI=paper.doi,
            abstract=paper.abstract or "",
        )

        prompt_path = _SHIPPED_PROMPTS_DIR / "extract.md"

        # --- Per-chunk extraction via shared helper ---
        extraction = _run_chunked_extraction(
            ai, ai_config, entry, chunks, paper.paper_id, paper_id, prompt_path,
            full_text=full_text,
        )

        if extraction is None:
            return {"status": "error", "detail": "No fragments extracted"}

        reprocess_failed_chunks = extraction.get("failed_chunks", 0)
        reprocess_chunk_total = extraction.get("chunks_processed", 0) + reprocess_failed_chunks
        if reprocess_failed_chunks > 0:
            # Refuse to replace a complete corpus record with partial extraction data.
            # Old fragments are preserved; caller can retry.
            logger.error(
                "reprocess_paper %s: %d/%d chunk(s) failed — aborting swap to preserve existing corpus",
                paper_id, reprocess_failed_chunks, reprocess_chunk_total,
            )
            return {
                "status": "partial",
                "paper_id": paper_id,
                "detail": (
                    f"{reprocess_failed_chunks}/{reprocess_chunk_total} chunks failed AI extraction; "
                    "existing fragments preserved"
                ),
                "chunks_processed": reprocess_chunk_total - reprocess_failed_chunks,
                "failed_chunks": reprocess_failed_chunks,
            }

        new_fragments = extraction["fragments"]
        all_key_refs = extraction["key_refs"]
        chunk_total = extraction["chunks_processed"]

        # Atomic swap: only delete old data after new extraction fully succeeded
        deleted = paper_store.delete_fragments(paper.paper_id)
        saved = paper_store.save_fragments(paper.paper_id, new_fragments, "", ai_config.model)
        paper_store.update_paper_raw_text(paper.paper_id, full_text)
        if all_key_refs:
            paper_store.save_citation_links(paper.paper_id, all_key_refs)

        # Re-embed new fragments (non-fatal)
        emb = _create_embeddings_provider()
        frag_embedded = 0
        if emb:
            try:
                texts = [f.fragment_text for f in new_fragments]
                batch_fn = getattr(emb, "embed_batch", None)
                vectors = batch_fn(texts) if callable(batch_fn) else [
                    emb.embed(t, "") for t in texts
                ]
                for frag, vec in zip(new_fragments, vectors):
                    if vec:
                        paper_store.save_fragment_embedding(frag.fragment_id, vec, emb.model_name)
                        frag_embedded += 1
            except Exception as exc:
                logger.warning("reprocess_paper embedding failed for %s: %s", paper_id, exc)

        logger.info(
            "reprocess_paper %s: deleted %d old, saved %d new fragments (%d chunks, %d embedded)",
            paper_id, deleted, saved, chunk_total, frag_embedded,
        )
        return {
            "status": "completed",
            "paper_id": paper_id,
            "chunks_processed": chunk_total,
            "fragments_extracted": saved,
            "fragments_embedded": frag_embedded,
            "old_fragments_deleted": deleted,
        }

    except Exception as exc:
        logger.error("reprocess_paper failed for %s: %s", paper_id, exc, exc_info=True)
        return {"status": "error", "detail": f"Reprocess failed: {type(exc).__name__}: {exc}"}


def _run_auto_suggest(
    paper_id: str,
    citekey: str,
    user_id: str,
    project_id: str,
    fragment_ids: list[str],
    citation_intents: dict[str, str | None],
    fragment_ai_sections: dict[str, str | None],
    data_dir: str,
) -> dict:
    """Write auto-suggest curation entries for all fragments of a processed paper.

    Runs as an async post-hook job so process_source() returns immediately.
    Idempotent: curate_fragments() uses INSERT OR REPLACE so re-runs are safe.
    Errors are logged but never re-raised to avoid crashing the rq worker.
    """
    try:
        from klemma.section_types import auto_assign_section as _auto_assign
        from klemma.stores.user_store import LocalUserStore

        data_path = Path(data_dir)
        user_store = LocalUserStore(data_path / "users.db")

        _proj = user_store.get_project_by_id(project_id)
        if not _proj or _proj.get("user_id") != user_id:
            logger.warning(
                "Auto-suggest skipped: project %s not found or not owned by %s",
                project_id, user_id,
            )
            return {"status": "skipped", "reason": "project_not_found"}

        _outline = _proj.get("outline")
        suggestions = []
        for frag_id in fragment_ids:
            intent = citation_intents.get(frag_id)
            ai_sec = fragment_ai_sections.get(frag_id)
            assigned = _auto_assign(intent, _outline, ai_sec)
            suggestions.append({
                "fragment_id": frag_id,
                "citekey": citekey,
                "verdict": "suggested",
                "assigned_section": assigned,
            })

        if suggestions:
            count = user_store.curate_fragments(project_id, suggestions)
            logger.info("Auto-suggested %d fragments for %s (project %s)", count, citekey, project_id)
            return {"status": "completed", "suggested": count}
        return {"status": "completed", "suggested": 0}

    except Exception as exc:
        logger.warning("Auto-suggestion failed for %s (non-fatal): %s", citekey, exc)
        return {"status": "error", "detail": str(exc)}


def _enqueue_auto_suggest(
    paper_id: str,
    citekey: str,
    user_id: str,
    project_id: str,
    fragment_ids: list[str],
    citation_intents: dict[str, str | None],
    fragment_ai_sections: dict[str, str | None],
    data_dir: str,
) -> str | None:
    """Enqueue _run_auto_suggest as an rq job. Returns job_id or None."""
    try:
        from redis import Redis
        from rq import Queue

        redis_url = os.getenv("REDIS_URL", "redis://localhost:6379")
        q = Queue(connection=Redis.from_url(redis_url))
        job = q.enqueue(
            _run_auto_suggest,
            paper_id, citekey, user_id, project_id,
            fragment_ids, citation_intents, fragment_ai_sections, data_dir,
            job_timeout=60,
        )
        logger.info("Enqueued auto-suggest job %s for %s", job.id, citekey)
        return job.id
    except Exception as exc:
        # Fallback: run synchronously (no Redis available)
        logger.warning("Auto-suggest enqueue failed for %s, running synchronously: %s", citekey, exc)
        _run_auto_suggest(
            paper_id, citekey, user_id, project_id,
            fragment_ids, citation_intents, fragment_ai_sections, data_dir,
        )
        return None


def _enqueue_auto_sentences(
    project_id: str,
    citekey: str,
    user_id: str,
    data_dir: str,
) -> str | None:
    """Enqueue generate_sentences_task in mode='missing' for a just-processed
    source, so the user doesn't have to press "Сгенерировать предложения"
    manually. Returns rq job_id, or None if Redis is unavailable (in which
    case the task runs synchronously — slower but still completes).

    Idempotent: mode='missing' only fills fragments that don't already have
    a suggested_text. Re-running is a no-op.
    """
    try:
        from redis import Redis
        from rq import Queue

        redis_url = os.getenv("REDIS_URL", "redis://localhost:6379")
        q = Queue(connection=Redis.from_url(redis_url))
        job = q.enqueue(
            generate_sentences_task,
            project_id, citekey, data_dir, user_id, "missing",
            job_timeout=300,
        )
        logger.info("Enqueued auto-sentences job %s for %s", job.id, citekey)
        return job.id
    except Exception as exc:
        logger.warning(
            "Auto-sentences enqueue failed for %s, running synchronously: %s",
            citekey, exc,
        )
        try:
            generate_sentences_task(project_id, citekey, data_dir, user_id, "missing")
        except Exception as sync_exc:
            logger.warning("Auto-sentences sync fallback failed for %s: %s", citekey, sync_exc)
        return None


def re_embed_source_task(paper_id: str, citekey: str, data_dir: str) -> dict:
    """Re-compute the source embedding after metadata enrichment.

    Called asynchronously by enrich-metadata endpoint.
    Idempotent: overwrites existing embedding with updated title+abstract.
    """
    try:
        from klemma.stores.paper_store import LocalPaperStore

        data_path = Path(data_dir)
        paper_store = LocalPaperStore(data_path / "library.db")

        paper = paper_store.get_paper_by_id(paper_id)
        if not paper:
            logger.warning("re_embed_source_task: paper %s not found", paper_id)
            return {"status": "error", "detail": "paper_not_found"}

        emb = _create_embeddings_provider()
        if not emb:
            return {"status": "skipped", "reason": "embeddings_disabled"}

        vec = emb.embed(paper.title or citekey, paper.abstract or "")
        if vec:
            paper_store.save_paper_embedding(paper_id, vec, emb.model_name)
            logger.info("Re-embedded source %s after metadata enrichment", citekey)
            return {"status": "completed"}
        return {"status": "skipped", "reason": "no_vector"}
    except Exception as exc:
        logger.warning("Re-embed failed for %s (non-fatal): %s", citekey, exc)
        return {"status": "error", "detail": str(exc)}


def generate_outline_saas(
    project_id: str,
    context_text: str,
    project_type: str,
    data_dir: str,
    user_id: str = "",
) -> dict:
    """Generate a structured outline from user-provided context text (plan-prospekt).

    PM-approved scope (2026-03-16): text input only, no library summary on first run.
    Renders outline.md with context_text as custom_prompt, updates project outline.
    """
    from pathlib import Path

    from klemma.stores.user_store import LocalUserStore

    data_path = Path(data_dir)
    user_store = LocalUserStore(data_path / "users.db")

    if user_id and not user_store.check_token_limit(user_id):
        return {"status": "error", "detail": "Token limit exhausted"}

    if not os.getenv("ANTHROPIC_API_KEY") and not os.getenv("OPENAI_API_KEY"):
        return {"status": "error", "detail": "No AI API key configured"}

    try:
        ai, ai_config = _create_ai_provider()

        from klemma.config import _SHIPPED_PROMPTS_DIR
        prompt_path = _SHIPPED_PROMPTS_DIR / "outline.md"

        user_prompt = ai.render_prompt(
            prompt_path,
            project_type=project_type,
            dissertation_context="",
            project_files=[],
            library_summary=None,
            custom_prompt=context_text,
            language="ru",
        )

        system = (
            "You are an academic writing advisor. Generate a structured outline for an academic work. "
            "Output only valid JSON."
        )

        result = ai.call_with_meta(system, user_prompt, max_tokens=4096)
        if not result or not result.text:
            return {"status": "error", "detail": "AI returned no data"}

        if user_id:
            user_store.record_usage(
                user_id=user_id,
                operation="generate_outline",
                model=ai_config.model,
                input_tokens=result.input_tokens or 0,
                output_tokens=result.output_tokens or 0,
            )

        from klemma.ai import extract_json
        data = extract_json(result.text)
        if not data:
            return {"status": "error", "detail": "Failed to parse AI response as JSON"}

        # Merge chapters + sections into a flat sorted list
        sections: list[dict] = []
        for sec_id, sec_name in (data.get("chapters") or {}).items():
            sections.append({"id": str(sec_id), "name": str(sec_name)})
        for sec_id, sec_name in (data.get("sections") or {}).items():
            sections.append({"id": str(sec_id), "name": str(sec_name)})

        def _sort_key(s: dict) -> list:
            try:
                return [int(p) for p in s["id"].split(".")]
            except ValueError:
                return [0]

        sections.sort(key=_sort_key)

        user_store.update_project_outline(project_id, sections)
        logger.info("Generated %d sections for project %s", len(sections), project_id)

        return {
            "status": "completed",
            "project_id": project_id,
            "sections": sections,
            "title": data.get("title", ""),
        }

    except Exception as exc:
        logger.error("Outline generation failed for %s: %s", project_id, exc, exc_info=True)
        return {"status": "error", "detail": f"Generation failed: {type(exc).__name__}: {exc}"}


def generate_research(section: str, project_id: str, data_dir: str, user_id: str = "") -> dict:
    """Generate a research briefing for a section via researcher.py.

    Headless mode: _NullVault (no Obsidian), _SaaSStateAdapter (three-tier stores),
    no RAG, no incremental. Persists result in research_reports table.
    """
    import json

    from klemma.api.adapters import _NullVault, _SaaSStateAdapter
    from klemma.config import KlemmaConfig
    from klemma.stores.paper_store import LocalPaperStore
    from klemma.stores.project_store import LocalProjectStore
    from klemma.stores.user_library import LocalUserLibrary
    from klemma.stores.user_store import LocalUserStore

    data_path = Path(data_dir)
    library_db = data_path / "library.db"
    paper_store = LocalPaperStore(library_db)
    user_library = LocalUserLibrary(library_db)
    project_store = LocalProjectStore(data_path / "project.db")
    user_store = LocalUserStore(data_path / "users.db")

    if user_id and not user_store.check_token_limit(user_id):
        return {"status": "error", "detail": "Token limit exhausted"}

    if not os.getenv("ANTHROPIC_API_KEY") and not os.getenv("OPENAI_API_KEY"):
        return {"status": "error", "detail": "No AI API key configured"}

    # Load project outline for dissertation_context
    dissertation_context = ""
    if project_id:
        project = user_store.get_project_by_id(project_id)
        if project and project.get("outline"):
            outline = project["outline"]
            dissertation_context = "Dissertation sections:\n" + "\n".join(
                f"  {s['id']}: {s['name']}" for s in outline
            )

    try:
        ai, ai_config = _create_ai_provider()
        state_adapter = _SaaSStateAdapter(paper_store, project_store, user_library, user_id=user_id or None)
        vault = _NullVault()
        config = KlemmaConfig()

        from klemma.config import _SHIPPED_PROMPTS_DIR
        from klemma.skills.researcher import research_section

        # klemma_home enables resolve_prompt() in researcher.py to find prompts
        klemma_home = _SHIPPED_PROMPTS_DIR.parent

        result = research_section(
            section=section,
            config=config,
            state=state_adapter,
            vault=vault,
            ai=ai,
            save_to_vault=False,
            dissertation_context=dissertation_context,
            klemma_home=klemma_home,
            paper_store=paper_store,
            user_library=user_library,
        )

        # Token counts: research_section() uses call_json() internally which
        # doesn't expose token metadata. Recording 0 for now — fix requires
        # refactoring researcher.py to use call_with_meta(). Tracked as known limitation.
        if user_id:
            user_store.record_usage(
                user_id=user_id,
                operation="generate_research",
                model=ai_config.model,
                input_tokens=0,
                output_tokens=0,
                section=section,
            )

        report_json = json.dumps(result.model_dump(), ensure_ascii=False, default=str)
        user_store.save_research_report(
            project_id=project_id,
            section=section,
            report_json=report_json,
            report_text=result.research_text,
            model=ai_config.model,
        )

        # Mirror to MD file so klemma-cli can pull it via sync
        _mirror_research_report(data_path, project_id, section, result.research_text, ai_config.model)

        logger.info("Research report generated for section %s (project %s)", section, project_id)
        return {
            "status": "completed",
            "section": section,
            "report_text": result.research_text,
        }

    except Exception as exc:
        logger.error("Research generation failed for %s: %s", section, exc, exc_info=True)
        return {"status": "error", "detail": f"Research failed: {type(exc).__name__}: {exc}"}


def generate_draft(section: str, data_dir: str, project_id: str = "", user_id: str = "", word_target: int = 0, instruction: str = "") -> dict:
    """Generate a section draft using drafter.py in headless SaaS mode.

    Loads fragments + research report from three-tier stores, calls
    drafter.generate_draft(), returns the prose text.
    """
    import re

    from klemma.config import _SHIPPED_PROMPTS_DIR, KlemmaConfig
    from klemma.stores.paper_store import LocalPaperStore
    from klemma.stores.project_store import LocalProjectStore
    from klemma.stores.user_library import LocalUserLibrary
    from klemma.stores.user_store import (
        LocalUserStore,  # noqa: F401 (already imported above, but needed here for worker isolation)
    )

    data_path = Path(data_dir)
    library_db = data_path / "library.db"
    paper_store = LocalPaperStore(library_db)
    user_library = LocalUserLibrary(library_db)
    project_store = LocalProjectStore(data_path / "project.db")
    user_store = LocalUserStore(data_path / "users.db")

    if user_id and not user_store.check_token_limit(user_id):
        return {"status": "error", "detail": "Token limit exhausted"}

    if not os.getenv("ANTHROPIC_API_KEY") and not os.getenv("OPENAI_API_KEY"):
        return {"status": "error", "detail": "No AI API key configured"}

    # Chapter number from section id (e.g. "1.1" → 1, "2.3" → 2)
    try:
        chapter = int(section.split(".")[0])
    except (ValueError, IndexError):
        chapter = 0

    # Load project outline for context
    dissertation_context = ""
    section_title = ""
    if project_id:
        try:
            project = user_store.get_project_by_id(project_id)
            if project and project.get("outline"):
                outline = project["outline"]
                dissertation_context = "Dissertation sections:\n" + "\n".join(
                    f"  {s['id']}: {s['name']}" for s in outline
                )
                sec = next((s for s in outline if s["id"] == section), None)
                if sec:
                    section_title = sec["name"]
        except Exception as exc:
            logger.warning("Failed to load project outline: %s", exc)

    # Load section citekeys and build source_summaries + fragments. Everything
    # the model sees (valid_citekeys, source_summaries[].citekey,
    # fragments[].source, candidate_sentences[].citekey below) must live in the
    # same citekey-space, otherwise the drafter's hallucination filter
    # (drafter._filter_hallucinated_citations) will reject [@external] in
    # output. Display map maps internal → external_citekey-if-set.
    source_summaries: list[dict] = []
    fragments: list[dict] = []
    valid_citekeys: set[str] = set()

    try:
        section_citekeys = project_store.get_sources_by_section(section, user_id=user_id or None)
        display_map = user_library.get_display_citekeys(
            list(section_citekeys), user_id=user_id or None,
        )
        for citekey in section_citekeys:
            display_ck = display_map.get(citekey, citekey)
            valid_citekeys.add(display_ck)
            src = user_library.get_source_by_citekey(citekey, user_id=user_id or None)
            if not src:
                continue
            paper = paper_store.get_paper_by_id(src.paper_id)
            if paper:
                source_summaries.append({
                    "citekey": display_ck,
                    "quality": "?",
                    "priority": "medium",
                    "summary": (paper.abstract or "")[:300],
                })
            paper_fragments = paper_store.get_fragments(src.paper_id)
            for f in paper_fragments:
                fragments.append({
                    "source": display_ck,
                    "type": f.fragment_type or "key_idea",
                    "relevance": 3,
                    "text": f.fragment_text,
                    "page": f.page_number,
                    "intent": f.citation_intent,
                    "verbatim": f.verbatim,
                })
    except Exception as exc:
        logger.warning("Failed to load section fragments: %s", exc)

    # Load research report as additional context
    research_report_content = ""
    if project_id:
        try:
            report = user_store.get_research_report(project_id, section)
            if report:
                research_report_content = report.get("report_text", "")
        except Exception as exc:
            logger.warning("Failed to load research report: %s", exc)

    # ADR-017: pull user-accepted suggested sentences for this section.
    # Accepted fragments with a stored suggested_text become candidate
    # sentences the drafter may integrate verbatim.
    candidate_sentences: list[dict] = []
    if project_id:
        try:
            accepted_rows = user_store.get_curated(
                project_id, verdict="accepted", section=section
            )
            # Resolve display for all candidate citekeys in one batch (may
            # extend display_map with citekeys that belong to other sections).
            candidate_cks = list({row["citekey"] for row in accepted_rows})
            candidate_display_map = user_library.get_display_citekeys(
                candidate_cks, user_id=user_id or None,
            )
            for row in accepted_rows:
                sentence = (row.get("suggested_text") or "").strip()
                if sentence:
                    internal_ck = row["citekey"]
                    candidate_sentences.append({
                        "citekey": candidate_display_map.get(internal_ck, internal_ck),
                        "sentence": sentence,
                    })
        except Exception as exc:
            logger.warning("Failed to load candidate sentences: %s", exc)

    try:
        ai, ai_config = _create_ai_provider()
        config = KlemmaConfig()
        klemma_home = _SHIPPED_PROMPTS_DIR.parent

        from klemma.skills.drafter import generate_draft as _generate_draft

        outline_context = {"word_target": word_target} if word_target else None

        result = _generate_draft(
            section=section,
            chapter=chapter,
            config=config,
            ai=ai,
            dissertation_context=dissertation_context,
            klemma_home=klemma_home,
            research_report_content=research_report_content,
            source_summaries=source_summaries,
            fragments=fragments,
            valid_citekeys=valid_citekeys or None,
            section_title=section_title,
            outline_context=outline_context,
            custom_prompt=instruction,
            candidate_sentences=candidate_sentences,
        )

        if not result.text:
            return {"status": "error", "detail": "AI returned no draft text"}

        # Convert Obsidian wikilinks [[@citekey]] → [@citekey] for SaaS output.
        # Charset must match drafter._extract_citations (Biber/BibTeX valid:
        # word + : . + -) — otherwise BBT keys with "." / ":" / "+" leak
        # through as raw [[@key]] wikilinks in API draft output.
        text = re.sub(r"\[\[@([\w:.+\-]+)\]\]", r"[@\1]", result.text)

        if user_id:
            user_store.record_usage(
                user_id=user_id,
                operation="generate_draft",
                model=ai_config.model,
                input_tokens=0,
                output_tokens=0,
                section=section,
            )

        logger.info(
            "Draft generated for section %s: %d words, %d citations",
            section, result.word_count, len(result.citations_used),
        )
        return {
            "status": "completed",
            "section": section,
            "text": text,
            "word_count": result.word_count,
            "citations_used": result.citations_used,
        }

    except Exception as exc:
        logger.error("Draft generation failed for %s: %s", section, exc, exc_info=True)
        return {"status": "error", "detail": f"Draft failed: {type(exc).__name__}: {exc}"}


def generate_sentences_task(
    project_id: str,
    citekey: str,
    data_dir: str,
    user_id: str = "",
    mode: str = "missing",
) -> dict:
    """Generate suggested sentences for uncurated fragments of a source (ADR-017).

    mode="missing" — skip fragments that already have suggested_text.
    mode="force"   — regenerate for all fragments of the source.

    Persists each success as a 'suggested' curation row with suggested_text +
    sentence_model populated (existing verdict preserved if already set).
    """
    from klemma.stores.paper_store import LocalPaperStore
    from klemma.stores.user_library import LocalUserLibrary
    from klemma.stores.user_store import LocalUserStore

    data_path = Path(data_dir)
    library_db = data_path / "library.db"
    paper_store = LocalPaperStore(library_db)
    user_library = LocalUserLibrary(library_db)
    user_store = LocalUserStore(data_path / "users.db")

    if user_id and not user_store.check_token_limit(user_id):
        return {"status": "error", "detail": "Token limit exhausted"}

    if not os.getenv("ANTHROPIC_API_KEY") and not os.getenv("OPENAI_API_KEY"):
        return {"status": "error", "detail": "No AI API key configured"}

    project = user_store.get_project_by_id(project_id)
    if not project:
        return {"status": "error", "detail": "Project not found"}

    src = user_library.get_source_by_citekey(citekey, user_id=user_id or None)
    if not src:
        return {"status": "error", "detail": f"Source '{citekey}' not found in library"}

    paper = paper_store.get_paper_by_id(src.paper_id)
    all_fragments = paper_store.get_fragments(src.paper_id)
    if not all_fragments:
        return {"status": "completed", "generated": 0, "failed": 0, "sentences": {}}

    # Existing curation rows for this source — need them to filter by mode and
    # to preserve verdict when writing back.
    existing = {
        row["fragment_id"]: row
        for row in user_store.get_curated(project_id, citekey=citekey)
    }

    # Filter by mode. "missing" skips fragments that already have suggested_text.
    if mode == "force":
        candidates = list(all_fragments)
    else:
        candidates = [
            f for f in all_fragments
            if not (existing.get(f.fragment_id) or {}).get("suggested_text")
        ]

    if not candidates:
        return {"status": "completed", "generated": 0, "failed": 0, "sentences": {}}

    fragments_payload = [
        {
            "fragment_id": f.fragment_id,
            "text": f.fragment_text,
            "citation_intent": f.citation_intent or "",
            "assigned_section": (existing.get(f.fragment_id) or {}).get("assigned_section") or "",
        }
        for f in candidates
    ]

    outline = project.get("outline") or []
    outline_payload = [
        {
            "section_id": s.get("id", ""),
            "title": s.get("name", ""),
            "description": s.get("description", "") or "",
        }
        for s in outline
    ]

    language = os.getenv("KLEMMA_SENTENCE_LANGUAGE", "Russian")

    # Use display citekey (external_citekey if set via BBT import) in the
    # generated sentence text. Internal citekey stays for DB writes below.
    display_ck = src.external_citekey or src.citekey

    try:
        ai, ai_config = _create_ai_provider()
        from klemma.config import _SHIPPED_PROMPTS_DIR
        from klemma.skills.sentence_generator import generate_sentences

        klemma_home = _SHIPPED_PROMPTS_DIR.parent

        result = generate_sentences(
            fragments_payload,
            citekey=display_ck,
            authors=(paper.authors if paper else "") or "",
            year=(paper.year if paper else None),
            outline=outline_payload,
            language=language,
            ai=ai,
            klemma_home=klemma_home,
        )
    except Exception as exc:
        logger.error("Sentence generation failed for %s: %s", citekey, exc, exc_info=True)
        return {"status": "error", "detail": f"Sentence generation failed: {type(exc).__name__}: {exc}"}

    # Persist successes. For fragments without any existing row, create one
    # as verdict='suggested' via curate_fragments(). For existing rows,
    # update_curation() preserves the current verdict.
    decisions_new: list[dict] = []
    updates_existing: list[tuple[str, str]] = []  # (fragment_id, suggested_text)
    for frag_id, sentence in result.sentences.items():
        if frag_id in existing:
            updates_existing.append((frag_id, sentence))
        else:
            decisions_new.append({
                "fragment_id": frag_id,
                "citekey": citekey,
                "verdict": "suggested",
                "assigned_section": None,
                "suggested_text": sentence,
                "sentence_model": result.model,
            })

    if decisions_new:
        user_store.curate_fragments(project_id, decisions_new)
    for frag_id, sentence in updates_existing:
        user_store.update_curation(
            project_id,
            frag_id,
            suggested_text=sentence,
            sentence_model=result.model,
        )

    if user_id:
        try:
            user_store.record_usage(
                user_id=user_id,
                operation="generate_sentences",
                model=ai_config.model,
                input_tokens=result.input_tokens,
                output_tokens=result.output_tokens,
                section="",
            )
        except Exception as exc:
            logger.warning("record_usage failed for generate_sentences: %s", exc)

    logger.info(
        "Suggested sentences: citekey=%s generated=%d failed=%d mode=%s",
        citekey, len(result.sentences), len(result.failed), mode,
    )
    return {
        "status": "completed",
        "generated": len(result.sentences),
        "failed": len(result.failed),
        "failed_ids": result.failed,
        "sentences": result.sentences,
        "model": result.model,
    }


def backfill_citation_intents(
    user_id: str,
    data_dir: str,
    batch_size: int = 20,
    cursor: Optional[str] = None,
    dry_run: bool = False,
) -> dict:
    """Backfill citation_intent for existing citation_graph entries.

    Uses full raw_text (body context) to extract intent — same extract.md prompt.
    Cursor-based: resumable by passing next_cursor from previous result.

    Args:
        dry_run: When True, run AI extraction but skip DB updates. Returns what
                 *would* be updated without mutating citation_graph. Safe to run
                 repeatedly — no side effects beyond token consumption.

    Returns:
        {processed, skipped_no_raw_text, failed, next_cursor, remaining}
    """
    from pathlib import Path as _Path

    from klemma.ai import extract_json
    from klemma.stores.paper_store import LocalPaperStore

    _data_dir = _Path(data_dir)
    paper_store = LocalPaperStore(_data_dir / "library.db")

    ai, ai_config = _create_ai_provider()

    papers, remaining_before = paper_store.get_papers_for_user_backfill(
        user_id, batch_size=batch_size, cursor=cursor
    )

    processed = 0
    skipped_no_raw_text = 0
    failed = 0
    last_paper_id = cursor

    for paper in papers:
        paper_id = paper["paper_id"]
        last_paper_id = paper_id

        raw_text = paper_store.get_raw_text(paper_id)
        if not raw_text:
            skipped_no_raw_text += 1
            processed += 1
            continue

        # Truncate to 50K chars (same as main extraction)
        text_for_ai = raw_text[:50000]

        try:
            system = (
                "You are a research assistant analyzing in-text citation patterns."
                " Output only valid JSON."
            )
            user_prompt = (
                "Analyze the in-text citations in this paper body and identify the "
                "citation intent for key references. For each reference you can find "
                "cited in the body text (not just listed in the bibliography), identify "
                "the citation function.\n\n"
                "Return JSON: {\"key_references\": [{\"title\": \"...\", \"citation_intent\": \"method\"}]}\n\n"
                "Rules:\n"
                "- citation_intent must be one of: background, method, result_comparison, "
                "extends, contrasts, uses_data\n"
                "- Only include references with clear in-text citation context\n"
                "- If a reference is only in the bibliography without in-text context, skip it\n\n"
                f"Paper text:\n{text_for_ai}"
            )

            result = ai.call_with_meta(system, user_prompt, max_tokens=4096)
            if result and result.text:
                data = extract_json(result.text)
                if data:
                    refs = data.get("key_references", [])
                    if refs:
                        if dry_run:
                            logger.info(
                                "Backfill (dry_run): would update intents for %d refs in paper %s",
                                len(refs), paper_id,
                            )
                        else:
                            updated = paper_store.update_citation_intents(paper_id, refs)
                            logger.info(
                                "Backfill: updated %d intents for paper %s",
                                updated, paper_id,
                            )
            processed += 1
        except Exception as exc:
            logger.warning("Backfill failed for paper %s: %s", paper_id, exc)
            failed += 1
            processed += 1

    # Re-count remaining after processing
    _, remaining_after = paper_store.get_papers_for_user_backfill(
        user_id, batch_size=1, cursor=last_paper_id
    )

    return {
        "processed": processed,
        "skipped_no_raw_text": skipped_no_raw_text,
        "failed": failed,
        "next_cursor": last_paper_id,
        "remaining": remaining_after,
    }
