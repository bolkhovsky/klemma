"""LibraryProvider — abstraction for Zotero library data access.

Two implementations:
- LocalLibrary: wraps existing PDFExtractor.load_entry_lookup() (BBT JSON)
- MCPLibrary: uses zotero-mcp server (added in Phase 2)
"""

import logging
from pathlib import Path
from typing import Optional, Protocol, runtime_checkable

from .literature.models import ZoteroEntry

logger = logging.getLogger(__name__)


@runtime_checkable
class LibraryProvider(Protocol):
    """Protocol for library data access. Implementations are swappable via config."""

    @property
    def entries(self) -> dict[str, ZoteroEntry]:
        """Full library catalog. Lazy-loaded, cached for command lifetime."""
        ...

    @property
    def pdf_paths(self) -> dict[str, str]:
        """citekey → PDF file path mapping."""
        ...

    def get_text(self, citekey: str) -> Optional[str]:
        """Get full text for a paper. Returns None if not available
        (caller should fall back to local PyMuPDF extraction)."""
        ...


class LocalLibrary:
    """BBT JSON backend — wraps existing PDFExtractor static methods.

    Zero new behavior. This is a pure extraction of current code into
    the LibraryProvider interface.
    """

    def __init__(self, library_json_path: Optional[Path] = None):
        self._library_json_path = library_json_path
        self._entries: Optional[dict[str, ZoteroEntry]] = None
        self._pdf_paths: Optional[dict[str, str]] = None

    @property
    def entries(self) -> dict[str, ZoteroEntry]:
        if self._entries is None:
            self._entries = self._load_entries()
        return self._entries

    @property
    def pdf_paths(self) -> dict[str, str]:
        if self._pdf_paths is None:
            self._pdf_paths = {
                k: v.pdf_path for k, v in self.entries.items() if v.pdf_path
            }
        return self._pdf_paths

    def get_text(self, citekey: str) -> Optional[str]:
        # LocalLibrary doesn't provide text — caller uses PyMuPDF
        return None

    def _load_entries(self) -> dict[str, ZoteroEntry]:
        if not self._library_json_path:
            return {}
        try:
            from .literature.pdf import PDFExtractor

            return PDFExtractor.load_entry_lookup(self._library_json_path)
        except Exception as e:
            logger.error("Failed to load BBT JSON: %s", e)
            return {}


def create_library(config) -> LibraryProvider:
    """Factory: create the right LibraryProvider from config.

    config.zotero.backend == "local" → LocalLibrary (default)
    config.zotero.backend == "mcp"   → MCPLibrary (Phase 2)
    """
    backend = getattr(config.zotero, "backend", "local")

    if backend == "mcp":
        # Phase 2: MCPLibrary will be imported and returned here
        raise NotImplementedError(
            "MCP library backend not yet implemented. "
            "Use backend: 'local' or install Phase 2."
        )

    # Default: local BBT JSON
    library_json = config.zotero.library_json
    path = Path(library_json) if library_json else None
    return LocalLibrary(path)
