"""PDF text extraction using PyMuPDF."""

import json
import re
from pathlib import Path
from typing import Optional

import fitz

from .models import Author, ZoteroEntry


class PDFExtractor:
    """Extract text content from PDF files."""

    def __init__(self, max_chars: int = 50000):
        self.max_chars = max_chars

    @staticmethod
    def _load_bbt_json(library_json: Path) -> list[dict]:
        """Load items from BetterBibTeX JSON export."""
        path = Path(library_json)
        if not path.exists():
            return []
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return [
            item for item in data.get("items", [])
            if item.get("itemType") not in ("attachment", "note")
            and item.get("citationKey")
        ]

    @staticmethod
    def load_pdf_lookup(library_json: Path) -> dict[str, str]:
        """Build citekey → pdf_path mapping from BetterBibTeX JSON export."""
        items = PDFExtractor._load_bbt_json(library_json)
        lookup: dict[str, str] = {}
        for item in items:
            citekey = item["citationKey"]
            for att in item.get("attachments", []):
                if att.get("path", "").lower().endswith(".pdf"):
                    lookup[citekey] = att["path"]
                    break
        return lookup

    @staticmethod
    def load_entry_lookup(library_json: Path) -> dict[str, ZoteroEntry]:
        """Build citekey → ZoteroEntry mapping from BetterBibTeX JSON export."""
        items = PDFExtractor._load_bbt_json(library_json)
        lookup: dict[str, ZoteroEntry] = {}
        for item in items:
            citekey = item["citationKey"]
            # Parse authors
            authors = []
            for c in item.get("creators", []):
                if c.get("creatorType") == "author":
                    authors.append(Author(
                        family=c.get("lastName", ""),
                        given=c.get("firstName"),
                    ))
            # Parse year from date string
            issued = None
            date_str = item.get("date", "")
            if date_str:
                year_match = re.search(r"\d{4}", date_str)
                if year_match:
                    issued = {"date-parts": [[int(year_match.group())]]}
            # Find PDF path
            pdf_path = None
            for att in item.get("attachments", []):
                if att.get("path", "").lower().endswith(".pdf"):
                    pdf_path = att["path"]
                    break
            # Collect tags
            tags = [t["tag"] for t in item.get("tags", []) if t.get("tag")]
            lookup[citekey] = ZoteroEntry(
                id=citekey,
                type=item.get("itemType", "article"),
                title=item.get("title"),
                abstract=item.get("abstractNote"),
                author=authors,
                issued=issued,
                container_title=item.get("publicationTitle"),
                DOI=item.get("DOI"),
                URL=item.get("url"),
                language=item.get("language"),
                page=item.get("pages"),
                volume=item.get("volume"),
                issue=item.get("issue"),
                keywords=", ".join(tags) if tags else None,
                pdf_path=pdf_path,
                item_key=item.get("itemKey"),
            )
        return lookup

    def extract(self, pdf_path: Path) -> Optional[str]:
        """Extract text from PDF with page markers."""
        if not pdf_path.exists():
            return None
        try:
            doc = fitz.open(pdf_path)
            pages_text = []
            for page_num, page in enumerate(doc):
                text = page.get_text("text")
                text = self._clean_text(text)
                pages_text.append(f"[Page {page_num + 1}]\n{text}")
            doc.close()

            combined = "\n\n".join(pages_text)
            if len(combined) > self.max_chars:
                combined = combined[: self.max_chars] + "\n\n[...content truncated...]"
            return combined
        except Exception as e:
            print(f"PDF extraction error for {pdf_path}: {e}")
            return None

    def _clean_text(self, text: str) -> str:
        text = text.replace("\x00", "")
        text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        text = re.sub(r"\n\d+\s*\n", "\n", text)
        text = re.sub(r"-\n", "", text)
        return text.strip()

    @staticmethod
    def _split_citekey(citekey: str) -> list[str]:
        """Split camelCase citekey into lowercase parts (3+ chars)."""
        # Split on uppercase boundaries: wagnerSeaiceInformation2020
        # → ["wagner", "Seaice", "Information", "2020"]
        parts = re.findall(r"[A-Z][a-z]+|[a-z]+|\d{4}", citekey)
        return [p.lower() for p in parts if len(p) >= 3]

    def find_pdf(
        self,
        entry_id: str,
        search_paths: list[Path],
        entry_title: str = "",
        direct_path: Optional[str] = None,
        pdf_lookup: Optional[dict[str, str]] = None,
    ) -> Optional[Path]:
        """Find PDF file for entry across search paths."""
        if direct_path:
            path = Path(direct_path)
            if path.exists():
                return path

        # BetterBibTeX JSON lookup (most reliable)
        if pdf_lookup and entry_id in pdf_lookup:
            path = Path(pdf_lookup[entry_id])
            if path.exists():
                return path

        safe_id = re.sub(r"[^\w\-]", "", entry_id).lower()
        title_words = re.findall(r"\b\w{4,}\b", entry_title.lower()) if entry_title else []
        citekey_parts = self._split_citekey(entry_id)
        # Extract year from citekey (last 4-digit group)
        year_match = re.search(r"\d{4}", entry_id)
        citekey_year = year_match.group() if year_match else ""

        for search_path in search_paths:
            search_path = Path(search_path)
            if not search_path.exists():
                continue
            try:
                for pdf_path in search_path.glob("**/*.pdf"):
                    filename_lower = pdf_path.name.lower()
                    # Exact citekey match
                    if safe_id in filename_lower or entry_id.lower() in filename_lower:
                        return pdf_path
                    # Title words match (require year in filename to avoid cross-paper false positives)
                    if title_words and (not citekey_year or citekey_year in filename_lower):
                        matching = sum(1 for kw in title_words[:5] if kw in filename_lower)
                        if matching >= 2:
                            return pdf_path
                    # Citekey parts match: require author in filename prefix + 2 others
                    if len(citekey_parts) >= 3:
                        fn_stripped = filename_lower.replace("-", "")
                        author = citekey_parts[0]
                        # Author must appear before first separator (Zotero: "Author и др. - Year")
                        fn_prefix = filename_lower.split(" - ")[0] if " - " in filename_lower else filename_lower[:40]
                        if author in fn_prefix:
                            rest_matching = sum(
                                1 for p in citekey_parts[1:] if p in fn_stripped
                            )
                            if rest_matching >= 2:
                                return pdf_path
            except Exception:
                continue
        return None

    def get_pdf_info(self, pdf_path: Path) -> dict:
        try:
            doc = fitz.open(pdf_path)
            info = {
                "pages": doc.page_count,
                "title": doc.metadata.get("title", ""),
                "author": doc.metadata.get("author", ""),
            }
            doc.close()
            return info
        except Exception:
            return {}
