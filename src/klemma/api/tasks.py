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
        user_library.update_status(citekey, "pending")
        return {"status": "error", "detail": "Token limit exhausted"}

    # Check paper exists
    paper = paper_store.get_paper_by_id(paper_id)
    if paper is None:
        user_library.update_status(citekey, "failed")
        return {"status": "error", "detail": f"Paper {paper_id} not found"}

    # Check if already processed (has fragments)
    existing = paper_store.get_fragments(paper_id)
    if existing and not force:
        user_library.update_status(citekey, "completed")
        return {
            "status": "already_processed",
            "citekey": citekey,
            "fragment_count": len(existing),
        }
    if existing and force:
        deleted = paper_store.delete_fragments(paper_id)
        logger.info("Force reprocess: deleted %d existing fragments for %s", deleted, citekey)

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

        # Render extraction prompt
        # Dev (editable): 4 levels up from tasks.py → repo root / prompts/
        # Installed (Docker): KLEMMA_PROMPTS_DIR env var or /app/prompts (Dockerfile default)
        prompt_path = Path(__file__).parent.parent.parent.parent / "prompts" / "extract.md"
        if not prompt_path.exists():
            prompts_dir = os.environ.get("KLEMMA_PROMPTS_DIR", "/app/prompts")
            prompt_path = Path(prompts_dir) / "extract.md"

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
            "Output only valid JSON with fragments array."
        )

        result = ai.call_with_meta(system, user_prompt, max_tokens=4096)
        if not result or not result.text:
            user_library.update_status(citekey, "failed")
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
            user_library.update_status(citekey, "failed")
            return {"status": "error", "detail": "Failed to parse AI response as JSON"}

        # Parse and save fragments; collect AI-predicted section assignments
        fragments = []
        predicted_sections: set[str] = set()
        predicted_chapters: set[int] = set()
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
            sec = str(f_data.get("section", "")).strip()
            if sec:
                predicted_sections.add(sec)
            chap = f_data.get("chapter")
            if isinstance(chap, int):
                predicted_chapters.add(chap)

        if not fragments:
            user_library.update_status(citekey, "failed")
            return {"status": "error", "detail": "No fragments extracted from PDF"}

        # Save to paper store
        prompt_hash = ""
        model_name = ai_config.model
        saved = paper_store.save_fragments(paper_id, fragments, prompt_hash, model_name)

        # Auto-assign sections based on AI predictions (SaaS only — when project context given)
        if project_id and predicted_sections:
            from klemma.stores.project_store import LocalProjectStore
            project_store = LocalProjectStore(data_path / "project.db")
            project_store.set_source_sections(
                citekey, paper_id,
                sorted(predicted_sections),
                sorted(predicted_chapters),
            )
            logger.info(
                "Auto-assigned sections %s for %s (project %s)",
                sorted(predicted_sections), citekey, project_id,
            )

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

        prompt_path = Path(__file__).parent.parent.parent.parent / "prompts" / "outline.md"
        if not prompt_path.exists():
            prompts_dir = os.environ.get("KLEMMA_PROMPTS_DIR", "/app/prompts")
            prompt_path = Path(prompts_dir) / "outline.md"

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
        state_adapter = _SaaSStateAdapter(paper_store, project_store, user_library)
        vault = _NullVault()
        config = KlemmaConfig()

        from klemma.skills.researcher import research_section

        result = research_section(
            section=section,
            config=config,
            state=state_adapter,
            vault=vault,
            ai=ai,
            save_to_vault=False,
            dissertation_context=dissertation_context,
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

        logger.info("Research report generated for section %s (project %s)", section, project_id)
        return {
            "status": "completed",
            "section": section,
            "report_text": result.research_text,
        }

    except Exception as exc:
        logger.error("Research generation failed for %s: %s", section, exc, exc_info=True)
        return {"status": "error", "detail": f"Research failed: {type(exc).__name__}: {exc}"}


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
