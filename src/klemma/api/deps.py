"""Shared FastAPI dependencies for data store access."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from klemma.protocols import PaperStore, ProjectStore, UserLibrary

# Module-level store references, set during app startup via lifespan.
_paper_store: PaperStore | None = None
_user_library: UserLibrary | None = None
_project_store: ProjectStore | None = None


def set_paper_store(store: PaperStore) -> None:
    """Set the PaperStore instance."""
    global _paper_store  # noqa: PLW0603
    _paper_store = store


def get_paper_store() -> PaperStore:
    """Return the configured PaperStore, or raise if not set."""
    if _paper_store is None:
        raise RuntimeError("PaperStore not configured — call set_paper_store() at startup")
    return _paper_store


def set_user_library(lib: UserLibrary) -> None:
    """Set the UserLibrary instance."""
    global _user_library  # noqa: PLW0603
    _user_library = lib


def get_user_library() -> UserLibrary:
    """Return the configured UserLibrary, or raise if not set."""
    if _user_library is None:
        raise RuntimeError("UserLibrary not configured — call set_user_library() at startup")
    return _user_library


def set_project_store(store: ProjectStore) -> None:
    """Set the ProjectStore instance."""
    global _project_store  # noqa: PLW0603
    _project_store = store


def get_project_store() -> ProjectStore:
    """Return the configured ProjectStore, or raise if not set."""
    if _project_store is None:
        raise RuntimeError("ProjectStore not configured — call set_project_store() at startup")
    return _project_store
