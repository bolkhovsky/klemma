"""LibraryProvider — abstraction for Zotero library data access.

Three implementations:
- LocalLibrary: wraps existing PDFExtractor.load_entry_lookup() (BBT JSON)
- MCPLibrary: uses zotero-mcp server via MCP protocol
- CompositeLibrary: merges local + MCP (local entries win on conflict)
"""

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Optional, Protocol, runtime_checkable

if TYPE_CHECKING:
    from .tools.client import MCPClient

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


class MCPLibrary:
    """Zotero MCP backend — fetches library data via zotero-mcp server.

    Uses zotero_search_items for catalog, zotero_item_fulltext for text.
    MCP doesn't expose filesystem paths, so pdf_paths is always empty.
    """

    def __init__(self, client: "MCPClient"):
        self._client = client
        self._entries: Optional[dict[str, ZoteroEntry]] = None
        self._pdf_paths: dict[str, str] = {}

    @property
    def entries(self) -> dict[str, ZoteroEntry]:
        if self._entries is None:
            self._entries = self._load_entries()
        return self._entries

    @property
    def pdf_paths(self) -> dict[str, str]:
        return self._pdf_paths

    def get_text(self, citekey: str) -> Optional[str]:
        """Get full text via zotero_item_fulltext."""
        result = self._client.call_tool("zotero_item_fulltext", {"query": citekey})
        if result.is_error or not result.content:
            return None
        return result.content

    def _load_entries(self) -> dict[str, ZoteroEntry]:
        """Load all library entries via zotero_search_items."""
        result = self._client.call_tool("zotero_search_items", {"query": ""})
        if result.is_error or not result.content:
            logger.warning("MCPLibrary: failed to load entries: %s", result.content)
            return {}
        try:
            items = json.loads(result.content) if isinstance(result.content, str) else result.content
            if not isinstance(items, list):
                items = [items]
            entries = {}
            for item in items:
                citekey = item.get("citekey") or item.get("key", "")
                if not citekey:
                    continue
                entries[citekey] = ZoteroEntry(
                    id=citekey,
                    title=item.get("title", ""),
                    authors=item.get("authors") or item.get("creators", []),
                    year=item.get("year") or item.get("date", ""),
                    item_type=item.get("itemType", ""),
                    abstract=item.get("abstract", ""),
                )
            return entries
        except (json.JSONDecodeError, TypeError) as e:
            logger.error("MCPLibrary: failed to parse entries: %s", e)
            return {}


def create_library(config) -> LibraryProvider:
    """Factory: create the right LibraryProvider from config.

    config.zotero.backend == "local" → LocalLibrary (default)
    config.zotero.backend == "mcp"   → MCPLibrary (zotero-mcp server)
    """
    backend = getattr(config.zotero, "backend", "local")

    if backend == "mcp":
        zotero_srv = config.mcp.servers.get("zotero")
        if not zotero_srv or not zotero_srv.command:
            raise ValueError(
                "zotero.backend is 'mcp' but no 'zotero' MCP server configured. "
                "Use: klemma tools add zotero --command uvx --args zotero-mcp"
            )
        from .tools.client import MCPClient

        client = MCPClient(
            command=zotero_srv.command,
            args=zotero_srv.args,
            env=zotero_srv.env,
        )
        return MCPLibrary(client)

    # Default: local BBT JSON
    library_json = config.zotero.library_json
    path = Path(library_json) if library_json else None
    return LocalLibrary(path)
