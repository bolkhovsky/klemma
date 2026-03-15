"""Async task definitions for rq worker (ADR-009, #186).

Tasks are enqueued by API endpoints and executed by the rq worker process.
Each task receives primitive arguments (strings, dicts) — no store objects
or connections, since the worker runs in a separate process.
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def process_source(paper_id: str, citekey: str, data_dir: str) -> dict:
    """Extract fragments from a paper's PDF.

    This is the rq task equivalent of `klemma process <citekey>`.
    Runs in the worker process — initializes its own stores.

    Returns a dict with status and fragment count.
    """
    from klemma.stores.paper_store import LocalPaperStore
    from klemma.stores.user_library import LocalUserLibrary

    data_path = Path(data_dir)
    library_db = data_path / "library.db"
    paper_store = LocalPaperStore(library_db)
    user_library = LocalUserLibrary(library_db)

    # Check paper exists
    paper = paper_store.get_paper_by_id(paper_id)
    if paper is None:
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

    # TODO: actual AI extraction will be wired here when the extraction
    # pipeline is adapted for headless (no CLI) operation. For now, mark
    # as pending — extraction requires PDF file access + AI backend config
    # which aren't yet available in the SaaS context.
    logger.info("process_source: %s (%s) — extraction not yet wired", citekey, paper_id)
    user_library.update_status(citekey, "pending")

    return {
        "status": "pending",
        "citekey": citekey,
        "detail": "Extraction pipeline not yet wired for SaaS",
    }
