"""Async task definitions for rq worker (ADR-009, #186).

Tasks are enqueued by API endpoints and executed by the rq worker process.
Each task receives primitive arguments (strings, dicts) — no store objects
or connections, since the worker runs in a separate process.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)


def _create_ai_provider():
    """Create an AI provider from environment variables.

    Supports: ANTHROPIC_API_KEY, OPENAI_API_KEY via litellm backend.
    Model: KLEMMA_AI_MODEL env var (default: anthropic/claude-sonnet-4-20250514).
    """
    from klemma.ai import create_ai
    from klemma.config import AIConfig

    model = os.getenv("KLEMMA_AI_MODEL", "anthropic/claude-sonnet-4-20250514")
    api_keys = {}
    if os.getenv("ANTHROPIC_API_KEY"):
        api_keys["anthropic"] = os.environ["ANTHROPIC_API_KEY"]
    if os.getenv("OPENAI_API_KEY"):
        api_keys["openai"] = os.environ["OPENAI_API_KEY"]

    config = AIConfig(backend="litellm", model=model)
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
        deleted = paper_store.delete_fragments(paper_id)
        logger.info("Force reprocess: deleted %d existing fragments for %s", deleted, citekey)

    # Mark as processing
    user_library.update_status(citekey, "processing", user_id=user_id or None)

    # Find PDF file in FileStore
    paper_dir = file_store.get_paper_dir(paper_id)
    pdf_files = list(paper_dir.glob("*.pdf")) if paper_dir.is_dir() else []
    if not pdf_files:
        user_library.update_status(citekey, "failed", user_id=user_id or None)
        return {"status": "error", "detail": f"PDF not found for paper {paper_id}"}

    # Extract PDF text
    try:
        from klemma.literature.pdf import PDFExtractor

        pdf_path = pdf_files[0]

        extractor = PDFExtractor()
        pdf_text = extractor.extract(pdf_path)
        if not pdf_text or len(pdf_text) < 500:
            user_library.update_status(citekey, "failed", user_id=user_id or None)
            return {"status": "error", "detail": "PDF text too short or extraction failed"}

        logger.info("Extracted %d chars from PDF for %s", len(pdf_text), citekey)

        # Cache the full PDF text on the paper record so the verbatim validator
        # and future find-in-page UX can search the same string the AI saw.
        try:
            paper_store.update_paper_raw_text(paper_id, pdf_text)
        except Exception as cache_exc:
            logger.warning(
                "raw_text cache write failed for %s (non-fatal): %s", citekey, cache_exc,
            )
    except Exception as exc:
        logger.error("PDF extraction failed for %s: %s", citekey, exc)
        user_library.update_status(citekey, "failed", user_id=user_id or None)
        return {"status": "error", "detail": f"PDF extraction failed: {exc}"}

    # Extract abstract directly from PDF text (no network call).
    # CrossRef enrichment (authors/year/doi) is now an explicit user action
    # on the SourceView page — it's no longer in the critical upload path.
    try:
        from klemma.literature.metadata import _extract_abstract_from_text

        abstract = _extract_abstract_from_text(pdf_text)
        if abstract:
            paper_store.update_paper_metadata(paper_id, abstract=abstract)
            logger.info("Extracted abstract from PDF text for %s (%d chars)", citekey, len(abstract))
    except Exception as exc:
        logger.warning("Abstract extraction failed for %s (non-fatal): %s", citekey, exc)

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

    # Create AI provider and extract fragments
    try:
        ai, ai_config = _create_ai_provider()

        from klemma.hashing import compute_content_hash
        from klemma.literature.models import ZoteroEntry
        from klemma.models import FragmentRecord

        # Build minimal entry from paper metadata
        entry = ZoteroEntry(
            id=citekey,
            title=paper.title or citekey,
            author=[{"family": a.strip()} for a in (paper.authors or "").split(",") if a.strip()],
            issued={"date-parts": [[paper.year]]} if paper.year else None,
            DOI=paper.doi,
            abstract=paper.abstract or "",
        )

        # Render extraction prompt — uses _SHIPPED_PROMPTS_DIR (respects KLEMMA_PROMPTS_DIR env)
        from klemma.config import _SHIPPED_PROMPTS_DIR
        prompt_path = _SHIPPED_PROMPTS_DIR / "extract.md"

        user_prompt = ai.render_prompt(
            prompt_path,
            title=entry.title or "Unknown",
            authors=entry.authors_str,
            year=entry.year or "Unknown",
            journal=entry.container_title or "N/A",
            doi=entry.DOI or "N/A",
            abstract=entry.abstract or "Not available",
            pdf_text=pdf_text[:50000],  # Cap at 50K chars
            dissertation_context=dissertation_context,
            available_tags=available_tags,
            language="ru",
            project_type="research",
        )

        system = (
            "You are a research assistant extracting citation-worthy fragments from scientific papers. "
            "Output only valid JSON with fragments array and key_references array."
        )

        # Adaptive max_tokens: short PDFs produce few fragments, so a high
        # cap is wasted latency + cost. ~4 chars per token, fragments are
        # typically 200-400 tokens each; we scale with pdf size but floor
        # at 2048 (floor was 1024, but PDFs shorter than 8K chars would get
        # truncated JSON responses — raised after e2e test confirmed the bug)
        # and cap at 8192 (the previous hardcoded ceiling).
        pdf_chars = len(pdf_text)
        adaptive_max_tokens = max(2048, min(8192, pdf_chars // 4))
        result = ai.call_with_meta(system, user_prompt, max_tokens=adaptive_max_tokens)
        if not result or not result.text:
            user_library.update_status(citekey, "failed", user_id=user_id or None)
            return {"status": "error", "detail": "AI extraction returned no data"}

        # Record token usage
        if user_store and user_id:
            user_store.record_usage(
                user_id=user_id,
                operation="process_source",
                model=ai_config.model,
                input_tokens=result.input_tokens or 0,
                output_tokens=result.output_tokens or 0,
                citekey=citekey,
            )

        # Parse JSON from response
        from klemma.ai import extract_json
        data = extract_json(result.text)
        if not data:
            user_library.update_status(citekey, "failed", user_id=user_id or None)
            return {"status": "error", "detail": "Failed to parse AI response as JSON"}

        # Parse and save fragments; collect AI-predicted section assignments
        fragments = []
        fragment_ai_sections: dict[str, str | None] = {}  # fragment_id → AI predicted section
        predicted_sections: set[str] = set()
        predicted_chapters: set[int] = set()
        # Build parallel Fragment (Pydantic) list purely to drive the verbatim
        # validator — the SaaS worker stores FragmentRecord (dataclass), so we
        # copy the validated flag back after the validator may downgrade it.
        from klemma.literature.models import Fragment
        from klemma.skills.extractor import validate_verbatim_fragments

        pydantic_frags: list[Fragment] = []
        for f_data in data.get("fragments", []):
            text = f_data.get("text", "").strip()
            if not text:
                continue
            fragment_id = compute_content_hash(paper_id, text, f_data.get("page"))
            claimed_verbatim = bool(f_data.get("verbatim", False))
            pydantic_frags.append(Fragment(text=text, verbatim=claimed_verbatim))
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
            # Capture per-fragment AI section prediction (safe — keyed by ID, not index)
            sec = str(f_data.get("section", "")).strip()
            fragment_ai_sections[fragment_id] = sec or None
            if sec:
                predicted_sections.add(sec)
            chap = f_data.get("chapter")
            if isinstance(chap, int):
                predicted_chapters.add(chap)

        if not fragments:
            user_library.update_status(citekey, "failed", user_id=user_id or None)
            return {"status": "error", "detail": "No fragments extracted from PDF"}

        # Verbatim integrity check — validator does offline substring matching,
        # so it's safe to use a larger window than the AI prompt (50K cap).
        # For small PDFs validate against full text; cap large PDFs at 150K to
        # keep peak RAM predictable while still catching bibliography fragments.
        from klemma.api.constants import (
            VERBATIM_VALIDATION_CAP_LARGE,
            VERBATIM_VALIDATION_CAP_SMALL,
        )
        _verbatim_window = (
            pdf_text
            if len(pdf_text) < VERBATIM_VALIDATION_CAP_SMALL
            else pdf_text[:VERBATIM_VALIDATION_CAP_LARGE]
        )
        downgrade_stats = validate_verbatim_fragments(
            pydantic_frags, _verbatim_window, citekey,
        )
        for record, pyd in zip(fragments, pydantic_frags):
            record.verbatim = pyd.verbatim
        if downgrade_stats.downgraded:
            logger.warning(
                "verbatim validator (%s): %d/%d claimed fragments downgraded "
                "(%d fuzzy-rescued, %d confirmed)",
                citekey,
                downgrade_stats.downgraded,
                downgrade_stats.verbatim_claimed,
                downgrade_stats.fuzzy_rescued,
                downgrade_stats.verbatim_confirmed,
            )

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
        key_refs = data.get("key_references", [])
        if not key_refs:
            # Main extraction often omits key_references — extract in a focused call
            # Re-extract the FULL PDF text (main extraction truncates at 50K which often
            # cuts off the bibliography section at the end)
            try:
                import re as _re
                full_extractor = PDFExtractor(max_chars=200000)
                full_text = full_extractor.extract(pdf_path) or ""
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

        user_library.update_status(citekey, "completed", user_id=user_id or None)
        logger.info("Extracted %d fragments for %s (%s)", saved, citekey, paper_id)

        result_dict: dict = {
            "status": "completed",
            "citekey": citekey,
            "fragment_count": saved,
            "downgrade_stats": downgrade_stats.as_dict(),
        }
        if auto_suggest_job_id:
            result_dict["auto_suggest_job_id"] = auto_suggest_job_id
        return result_dict

    except Exception as exc:
        logger.error("AI extraction failed for %s: %s", citekey, exc, exc_info=True)
        user_library.update_status(citekey, "failed", user_id=user_id or None)
        return {"status": "error", "detail": f"Extraction failed: {type(exc).__name__}: {exc}"}


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

    # Load section citekeys and build source_summaries + fragments
    source_summaries: list[dict] = []
    fragments: list[dict] = []
    valid_citekeys: set[str] = set()

    try:
        section_citekeys = project_store.get_sources_by_section(section, user_id=user_id or None)
        for citekey in section_citekeys:
            valid_citekeys.add(citekey)
            src = user_library.get_source_by_citekey(citekey, user_id=user_id or None)
            if not src:
                continue
            paper = paper_store.get_paper_by_id(src.paper_id)
            if paper:
                source_summaries.append({
                    "citekey": citekey,
                    "quality": "?",
                    "priority": "medium",
                    "summary": (paper.abstract or "")[:300],
                })
            paper_fragments = paper_store.get_fragments(src.paper_id)
            for f in paper_fragments:
                fragments.append({
                    "source": citekey,
                    "type": f.fragment_type or "key_idea",
                    "relevance": 3,
                    "text": f.fragment_text,
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
            for row in accepted_rows:
                sentence = (row.get("suggested_text") or "").strip()
                if sentence:
                    candidate_sentences.append({
                        "citekey": row["citekey"],
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

        # Convert Obsidian wikilinks [[@citekey]] → [@citekey] for SaaS output
        text = re.sub(r"\[\[@([\w\-]+)\]\]", r"[@\1]", result.text)

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

    try:
        ai, ai_config = _create_ai_provider()
        from klemma.config import _SHIPPED_PROMPTS_DIR
        from klemma.skills.sentence_generator import generate_sentences

        klemma_home = _SHIPPED_PROMPTS_DIR.parent

        result = generate_sentences(
            fragments_payload,
            citekey=citekey,
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
