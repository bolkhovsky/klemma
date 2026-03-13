"""Three-tier library stores (ADR-014).

Phase 1B: LocalPaperStore — SQLite implementation of PaperStore at ~/.klemma/library.db.
Phase 1C: LocalUserLibrary — citekey→paper_id mapping in library.db.
Phase 1C: LocalProjectStore — per-project data at project/.klemma/data/project.db.
"""

from .paper_store import LocalPaperStore
from .project_store import LocalProjectStore
from .user_library import LocalUserLibrary

__all__ = ["LocalPaperStore", "LocalProjectStore", "LocalUserLibrary"]
