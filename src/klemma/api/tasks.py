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


def process_source(paper_id: str, citekey: str, data_dir: str) -> dict:
    """Extract fragments from a paper's PDF.

    This is the rq task equivalent of `klemma process <citekey>`.
    Runs in the worker process — initializes its own stores.

    Pipeline: FileStore → PDFExtractor → AI extraction → PaperStore.

    Returns a dict with status and fragment count.
    """
    from klemma.stores.file_store import LocalFileStore
    from klemma.stores.paper_store import LocalPaperStore
    from klemma.stores.user_library import LocalUserLibrary

    data_path = Path(data_dir)
    library_db = data_path / "library.db"
    paper_store = LocalPaperStore(library_db)
    user_library = LocalUserLibrary(library_db)
    file_store = LocalFileStore(data_path / "files")

    # Check paper exists
    paper = paper_store.get_paper_by_id(paper_id)
    if paper is None:
        user_library.update_status(citekey, "failed")
        return {"status": "error", "detail": f"Paper {paper_id} not found"}

    # Check if already processed (has fragments)
    existing = paper_store.get_fragments(paper_id)
    if existing:
        user_library.update_status(citekey, "completed")
        return {
            "status": "already_processed",
            "citekey": citekey,
            "fragment_count": len(existing),
        }

    # Mark as processing
    user_library.update_status(citekey, "processing")

    # Find PDF file in FileStore
    paper_dir = file_store.get_paper_dir(paper_id)
    pdf_files = list(paper_dir.glob("*.pdf")) if paper_dir.is_dir() else []
    if not pdf_files:
        user_library.update_status(citekey, "failed")
        return {"status": "error", "detail": f"PDF not found for paper {paper_id}"}

    # Extract PDF text
    try:
        from klemma.literature.pdf import PDFExtractor

        pdf_path = pdf_files[0]

        extractor = PDFExtractor()
        pdf_text = extractor.extract(pdf_path)
        if not pdf_text or len(pdf_text) < 500:
            user_library.update_status(citekey, "failed")
            return {"status": "error", "detail": "PDF text too short or extraction failed"}

        logger.info("Extracted %d chars from PDF for %s", len(pdf_text), citekey)
    except Exception as exc:
        logger.error("PDF extraction failed for %s: %s", citekey, exc)
        user_library.update_status(citekey, "failed")
        return {"status": "error", "detail": f"PDF extraction failed: {exc}"}

    # Check AI config
    if not os.getenv("ANTHROPIC_API_KEY") and not os.getenv("OPENAI_API_KEY"):
        user_library.update_status(citekey, "pending")
        return {
            "status": "pending",
            "citekey": citekey,
            "detail": "No AI API key configured (set ANTHROPIC_API_KEY or OPENAI_API_KEY)",
        }

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

        # Render extraction prompt
        prompt_path = Path(__file__).parent.parent.parent.parent / "prompts" / "extract.md"
        if not prompt_path.exists():
            # Installed package — prompts shipped alongside
            import importlib.resources
            prompt_path = Path(importlib.resources.files("klemma").parent.parent / "prompts" / "extract.md")

        user_prompt = ai.render_prompt(
            prompt_path,
            title=entry.title or "Unknown",
            authors=entry.authors_str,
            year=entry.year or "Unknown",
            journal=entry.container_title or "N/A",
            doi=entry.DOI or "N/A",
            abstract=entry.abstract or "Not available",
            pdf_text=pdf_text[:50000],  # Cap at 50K chars
            dissertation_context="",
            available_tags="",
            language="ru",
            project_type="research",
        )

        system = (
            "You are a research assistant extracting citation-worthy fragments from scientific papers. "
            "Output only valid JSON with fragments array."
        )

        data = ai.call_json(system, user_prompt, max_tokens=4096)
        if not data:
            user_library.update_status(citekey, "failed")
            return {"status": "error", "detail": "AI extraction returned no data"}

        # Parse and save fragments
        fragments = []
        for f_data in data.get("fragments", []):
            text = f_data.get("text", "").strip()
            if not text:
                continue
            fragment_id = compute_content_hash(paper_id, text, f_data.get("page"))
            fragments.append(FragmentRecord(
                fragment_id=fragment_id,
                paper_id=paper_id,
                fragment_text=text,
                fragment_type=f_data.get("type", "key_idea"),
                page_number=f_data.get("page"),
                citation_intent=f_data.get("citation_intent"),
                content_hash=fragment_id,
            ))

        if not fragments:
            user_library.update_status(citekey, "failed")
            return {"status": "error", "detail": "No fragments extracted from PDF"}

        # Save to paper store
        prompt_hash = ""
        model_name = ai_config.model
        saved = paper_store.save_fragments(paper_id, fragments, prompt_hash, model_name)

        user_library.update_status(citekey, "completed")
        logger.info("Extracted %d fragments for %s (%s)", saved, citekey, paper_id)

        return {
            "status": "completed",
            "citekey": citekey,
            "fragment_count": saved,
        }

    except Exception as exc:
        logger.error("AI extraction failed for %s: %s", citekey, exc, exc_info=True)
        user_library.update_status(citekey, "failed")
        return {"status": "error", "detail": f"Extraction failed: {type(exc).__name__}: {exc}"}


def generate_research(section: str, data_dir: str) -> dict:
    """Generate a research briefing for a section.

    Returns a dict with status and content.
    """
    # TODO: wire to researcher.research_section() when adapted for headless mode.
    logger.info("generate_research: section %s — not yet wired", section)
    return {
        "status": "pending",
        "section": section,
        "detail": "Research pipeline not yet wired for SaaS",
    }


def generate_draft(section: str, data_dir: str) -> dict:
    """Generate a section draft.

    Returns a dict with status and content.
    """
    # TODO: wire to drafter.generate_draft() when adapted for headless mode.
    logger.info("generate_draft: section %s — not yet wired", section)
    return {
        "status": "pending",
        "section": section,
        "detail": "Draft pipeline not yet wired for SaaS",
    }
