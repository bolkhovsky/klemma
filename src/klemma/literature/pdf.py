"""PDF text extraction using PyMuPDF."""

import re
from pathlib import Path
from typing import Optional

import fitz


class PDFExtractor:
    """Extract text content from PDF files."""

    def __init__(self, max_chars: int = 50000):
        self.max_chars = max_chars

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

    def find_pdf(
        self,
        entry_id: str,
        search_paths: list[Path],
        entry_title: str = "",
        direct_path: Optional[str] = None,
    ) -> Optional[Path]:
        """Find PDF file for entry across search paths."""
        if direct_path:
            path = Path(direct_path)
            if path.exists():
                return path

        safe_id = re.sub(r"[^\w\-]", "", entry_id).lower()
        title_words = re.findall(r"\b\w{4,}\b", entry_title.lower()) if entry_title else []

        for search_path in search_paths:
            search_path = Path(search_path)
            if not search_path.exists():
                continue
            try:
                for pdf_path in search_path.glob("**/*.pdf"):
                    filename_lower = pdf_path.name.lower()
                    if safe_id in filename_lower or entry_id.lower() in filename_lower:
                        return pdf_path
                    if title_words:
                        matching = sum(1 for kw in title_words[:5] if kw in filename_lower)
                        if matching >= 2:
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
