"""Three-tier library stores (ADR-014).

Phase 1B: LocalPaperStore — SQLite implementation of PaperStore at ~/.klemma/library.db.
Phase 1C: LocalProjectStore — per-project data split (planned, issue #126).
"""

from .paper_store import LocalPaperStore

__all__ = ["LocalPaperStore"]
