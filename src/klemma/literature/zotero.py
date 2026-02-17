"""Zotero library access via pyzotero."""

import logging
from pathlib import Path
from typing import Optional

from pyzotero import zotero

from .models import Author, ZoteroEntry

logger = logging.getLogger(__name__)


class ZoteroLibrary:
    """Wrapper around pyzotero for library access."""

    def __init__(
        self,
        library_id: str,
        library_type: str = "user",
        api_key: Optional[str] = None,
        local: bool = False,
    ):
        if local:
            self.zot = zotero.Zotero(library_id, library_type)
        else:
            if not api_key:
                raise ValueError("API key required for non-local Zotero access")
            self.zot = zotero.Zotero(library_id, library_type, api_key)
        self._cache: dict[str, ZoteroEntry] = {}

    def get_all_items(self, limit: int = 0) -> list[ZoteroEntry]:
        """Load all top-level items from library."""
        items = self.zot.everything(self.zot.top(limit=limit)) if limit else self.zot.everything(self.zot.top())
        entries = []
        for item in items:
            entry = self._to_entry(item)
            if entry:
                entries.append(entry)
                self._cache[entry.id] = entry
        logger.info("Loaded %d entries from Zotero", len(entries))
        return entries

    def get_item(self, citekey: str) -> Optional[ZoteroEntry]:
        """Get single item by citekey. Checks cache first."""
        if citekey in self._cache:
            return self._cache[citekey]

        try:
            results = self.zot.items(q=citekey, qmode="everything", limit=10)
            for item in results:
                entry = self._to_entry(item)
                if entry and entry.id == citekey:
                    self._cache[entry.id] = entry
                    return entry
        except Exception as e:
            logger.error("Error fetching item %s: %s", citekey, e)
        return None

    def get_pdf_path(self, item_key: str) -> Optional[Path]:
        """Get path to PDF attachment for an item."""
        try:
            children = self.zot.children(item_key)
            for child in children:
                data = child.get("data", {})
                if data.get("contentType") == "application/pdf":
                    # For local Zotero, try to find the file path
                    filename = data.get("filename", "")
                    key = child.get("key", "")
                    if key and filename:
                        # Zotero stores attachments in storage/<KEY>/filename
                        return Path(f"storage/{key}/{filename}")
        except Exception as e:
            logger.warning("Error getting PDF for %s: %s", item_key, e)
        return None

    def search(self, query: str, limit: int = 20) -> list[ZoteroEntry]:
        """Search across library."""
        try:
            results = self.zot.items(q=query, qmode="everything", limit=limit)
            return [e for item in results if (e := self._to_entry(item))]
        except Exception as e:
            logger.error("Search error: %s", e)
            return []

    def get_collections(self) -> list[dict]:
        """List all collections."""
        try:
            return [
                {"key": c["key"], "name": c["data"]["name"]}
                for c in self.zot.collections()
            ]
        except Exception as e:
            logger.error("Collections error: %s", e)
            return []

    @staticmethod
    def _to_entry(item: dict) -> Optional[ZoteroEntry]:
        """Convert pyzotero item to ZoteroEntry."""
        data = item.get("data", {})
        item_type = data.get("itemType", "")

        # Skip non-document types
        if item_type in ("attachment", "note", "annotation"):
            return None

        # Build citekey: prefer extra field citekey, fallback to key
        citekey = ""
        extra = data.get("extra", "")
        for line in extra.split("\n"):
            if line.lower().startswith("citation key:"):
                citekey = line.split(":", 1)[1].strip()
                break
        if not citekey:
            citekey = item.get("key", data.get("key", ""))

        if not citekey:
            return None

        # Parse authors
        authors = []
        for creator in data.get("creators", []):
            if creator.get("creatorType") in ("author", "editor"):
                authors.append(
                    Author(
                        family=creator.get("lastName", ""),
                        given=creator.get("firstName"),
                        literal=creator.get("name"),
                    )
                )

        # Parse date
        date_str = data.get("date", "")
        issued = None
        if date_str:
            try:
                year = int(date_str[:4])
                issued = {"date-parts": [[year]]}
            except (ValueError, IndexError):
                pass

        return ZoteroEntry(
            id=citekey,
            type=item_type,
            title=data.get("title"),
            abstract=data.get("abstractNote"),
            author=authors,
            issued=issued,
            container_title=data.get("publicationTitle") or data.get("proceedingsTitle"),
            DOI=data.get("DOI"),
            URL=data.get("url"),
            language=data.get("language"),
            page=data.get("pages"),
            volume=data.get("volume"),
            issue=data.get("issue"),
        )
