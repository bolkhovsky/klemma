"""LibraryProvider — abstraction for Zotero library data access.

Single implementation: LocalLibrary (BBT JSON).
"""

import logging
from pathlib import Path
from typing import Optional, Protocol, runtime_checkable

from .literature.models import ZoteroEntry

logger = logging.getLogger(__name__)


@runtime_checkable
class LibraryProvider(Protocol):
    """Protocol for library data access."""

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

    @property
    def item_key_to_citekey(self) -> dict[str, str]:
        """Reverse lookup: Zotero itemKey → current citekey."""
        return {e.item_key: k for k, e in self.entries.items() if e.item_key}

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
    """Create LocalLibrary from config."""
    library_json = config.zotero.library_json
    path = Path(library_json) if library_json else None
    return LocalLibrary(path)
