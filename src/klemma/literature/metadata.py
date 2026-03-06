"""Auto-extract paper metadata from PDF properties + Semantic Scholar API."""

import logging
import os
import re
import time
from pathlib import Path
from typing import Optional

import fitz  # PyMuPDF
import requests

logger = logging.getLogger(__name__)

# Generic PDF titles that should trigger first-page fallback
_GENERIC_TITLES = {
    "untitled", "microsoft word", "document", "paper", "arxiv",
}

_s2_last_request = 0.0
_S2_THROTTLE = 3.1  # seconds between S2 API calls


def extract_pdf_metadata(pdf_path: Path) -> dict:
    """Extract title/author from PDF document properties.

    Falls back to first-page heuristic (largest font text) when the
    embedded title is empty or generic.

    Returns {"title": str, "authors": str} — best-effort, may be empty.
    """
    title = ""
    authors = ""

    try:
        with fitz.open(pdf_path) as doc:
            meta = doc.metadata or {}
            title = (meta.get("title") or "").strip()
            authors = (meta.get("author") or "").strip()

            # Check if title is generic
            if title and any(g in title.lower() for g in _GENERIC_TITLES):
                title = _extract_title_from_first_page(doc) or ""

            # If still no title, try first-page heuristic
            if not title and len(doc) > 0:
                title = _extract_title_from_first_page(doc) or ""
    except Exception as e:
        logger.warning("PDF metadata extraction failed: %s", e)

    return {"title": title, "authors": authors}


def _extract_title_from_first_page(doc) -> Optional[str]:
    """Extract title as the largest-font text on page 1."""
    try:
        page = doc[0]
        data = page.get_text("dict")
        best_text = ""
        best_size = 0.0

        for block in data.get("blocks", []):
            if block.get("type") != 0:  # text blocks only
                continue
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    size = span.get("size", 0)
                    text = (span.get("text") or "").strip()
                    if size > best_size and len(text) > 3:
                        best_size = size
                        best_text = text

        return best_text if best_text else None
    except Exception:
        return None


def lookup_s2(title: str) -> Optional[dict]:
    """Look up paper metadata on Semantic Scholar by title.

    Returns {"title", "authors", "year", "abstract", "doi"} or None.
    Rate-limited to ~1 request per 3 seconds.
    """
    global _s2_last_request

    if not title:
        return None

    for attempt in range(4):
        # Rate limiting (applied before every attempt)
        elapsed = time.time() - _s2_last_request
        if elapsed < _S2_THROTTLE:
            time.sleep(_S2_THROTTLE - elapsed)

        try:
            headers = {}
            api_key = os.environ.get("S2_API_KEY")
            if api_key:
                headers["x-api-key"] = api_key
            resp = requests.get(
                "https://api.semanticscholar.org/graph/v1/paper/search",
                params={
                    "query": title,
                    "fields": "title,authors,year,abstract,externalIds",
                    "limit": 3,
                },
                headers=headers,
                timeout=15,
            )
            _s2_last_request = time.time()
            if resp.status_code == 429:
                wait = 15 * (2 ** attempt)  # 15, 30, 60, 120s
                logger.debug("S2 rate-limited, retrying in %ds", wait)
                time.sleep(wait)
                continue
            resp.raise_for_status()

            papers = resp.json().get("data", [])
            for paper in papers:
                if _titles_match(title, paper.get("title", "")):
                    author_names = [a.get("name", "") for a in paper.get("authors", [])]
                    return {
                        "title": paper.get("title", ""),
                        "authors": ", ".join(author_names),
                        "year": paper.get("year"),
                        "abstract": paper.get("abstract") or "",
                        "doi": (paper.get("externalIds") or {}).get("DOI", ""),
                    }
            return None
        except Exception as e:
            logger.warning("S2 lookup failed for '%s': %s", title[:60], e)
            return None
    logger.warning("S2 rate-limited for '%s' after retries", title[:60])
    return None


def _titles_match(query: str, candidate: str) -> bool:
    """Fuzzy title comparison: normalize and check word overlap (>0.6)."""
    def normalize(s: str) -> set[str]:
        s = re.sub(r"[^\w\s]", "", s.lower())
        return {w for w in s.split() if len(w) > 2}

    q = normalize(query)
    c = normalize(candidate)
    if not q or not c:
        return False
    overlap = len(q & c) / max(len(q), len(c))
    return overlap > 0.6


def resolve_metadata(
    pdf_path: Path,
    cli_title: str = "",
    cli_authors: str = "",
    cli_year: Optional[int] = None,
    cli_doi: str = "",
) -> dict:
    """Orchestrate metadata resolution: CLI flags → PDF → S2 → empty fallback.

    Returns {"title", "authors", "year", "abstract", "doi"} with best available data.
    """
    result = {"title": "", "authors": "", "year": None, "abstract": "", "doi": ""}

    # Layer 1: PDF metadata (lowest priority for title/authors)
    pdf_meta = extract_pdf_metadata(pdf_path)
    if pdf_meta.get("title"):
        result["title"] = pdf_meta["title"]
    if pdf_meta.get("authors"):
        result["authors"] = pdf_meta["authors"]

    # Layer 2: CLI flags override PDF
    if cli_title:
        result["title"] = cli_title
    if cli_authors:
        result["authors"] = cli_authors
    if cli_year is not None:
        result["year"] = cli_year
    if cli_doi:
        result["doi"] = cli_doi

    # Layer 3: S2 enrichment (if we have a title to search with)
    if result["title"]:
        s2 = lookup_s2(result["title"])
        if s2:
            # S2 fills in blanks but doesn't override existing values
            if not result["authors"]:
                result["authors"] = s2.get("authors", "")
            if result["year"] is None:
                result["year"] = s2.get("year")
            if not result["abstract"]:
                result["abstract"] = s2.get("abstract", "")
            if not result["doi"]:
                result["doi"] = s2.get("doi", "")

    return result
