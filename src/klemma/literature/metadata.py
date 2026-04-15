"""Auto-extract paper metadata from PDF properties + CrossRef/S2 lookup."""

import logging
import os
import re
import time
from pathlib import Path
from typing import Optional
from urllib.parse import quote_plus

import fitz  # PyMuPDF
import requests

logger = logging.getLogger(__name__)

# Generic PDF titles that should trigger first-page fallback
_GENERIC_TITLES = {
    "untitled", "microsoft word", "document", "paper", "arxiv",
}

_s2_last_request = 0.0
_S2_THROTTLE = 3.1  # seconds between S2 API calls

# CrossRef recommends including a mailto address to enter the polite pool
# (higher, predictable rate limits). Read from env, fall back to a neutral one.
_DEFAULT_CROSSREF_MAILTO = "klemma@litresearch.ru"


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


def _crossref_mailto() -> str:
    return os.environ.get("KLEMMA_CROSSREF_MAILTO", _DEFAULT_CROSSREF_MAILTO)


def _extract_abstract_from_text(text: str) -> str:
    """Extract abstract from PDF text using heading markers.

    Searches for 'Abstract', 'Аннотация', or 'Резюме' markers and captures
    the following paragraph (up to the next section heading or blank line).
    Returns empty string for empty/missing input — never raises.

    Cap: 2000 characters.
    """
    if not text:
        return ""
    try:
        pattern = re.compile(
            r"(?is)\b(abstract|аннотация|резюме)\b\s*[:.]?\s*\n?"
            r"(.+?)"
            r"(?=\n\s*\n|\b(?:keywords|ключевые\s+слова|introduction|введение|1[.\s])\b)",
        )
        m = pattern.search(text[:8000])  # abstracts are near the top
        if m:
            abstract = m.group(2).strip()
            # Collapse internal newlines (multi-line PDF extraction artifacts)
            abstract = re.sub(r"\s*\n\s*", " ", abstract)
            return abstract[:2000]
    except Exception:
        pass
    return ""


def _extract_doi_from_text(text: str) -> str:
    """Extract the first DOI from PDF text (first 3000 chars).

    Filters obvious sentinel values (10.0000/*, 10.1000/*).
    Returns empty string for empty input — never raises.
    """
    if not text:
        return ""
    try:
        m = re.search(r"\b(10\.\d{4,9}/[-._;()/:\w]+)\b", text[:3000])
        if m:
            doi = m.group(1).rstrip(".")
            # Reject known sentinel patterns
            if re.match(r"10\.(0000|1000)/", doi):
                return ""
            return doi
    except Exception:
        pass
    return ""


def lookup_crossref_by_doi(
    doi: str,
    mailto: Optional[str] = None,
    timeout: int = 10,
) -> Optional[dict]:
    """Look up paper metadata on CrossRef by DOI (exact lookup).

    Returns ``{"title", "authors", "year", "abstract", "doi"}`` or ``None``.
    Unlike the title-based lookup, this is a single deterministic request —
    no fuzzy matching needed.

    Returns None on 404 or network error (does not raise).
    """
    if not doi:
        return None

    mailto = mailto or _crossref_mailto()
    url = f"https://api.crossref.org/works/{doi}"
    try:
        resp = requests.get(
            url,
            timeout=timeout,
            headers={"User-Agent": f"klemma/1.0 (mailto:{mailto})"},
            params={"mailto": mailto},
        )
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        item = resp.json().get("message", {})
    except Exception as e:
        logger.warning("CrossRef DOI lookup failed for '%s': %s", doi, e)
        return None

    item_title = " ".join(item.get("title", []) or [])
    authors = ", ".join(
        f"{a.get('family', '')} {a.get('given', '')}".strip()
        for a in item.get("author", [])
        if a.get("family") or a.get("given")
    )
    year: Optional[int] = None
    for date_field in ("published-print", "issued", "published-online"):
        parts = (item.get(date_field) or {}).get("date-parts") or [[]]
        if parts and parts[0] and parts[0][0]:
            try:
                year = int(parts[0][0])
                break
            except (TypeError, ValueError):
                pass
    raw_abstract = item.get("abstract") or ""
    abstract = re.sub(r"<[^>]+>", "", raw_abstract).strip()

    return {
        "title": item_title,
        "authors": authors,
        "year": year,
        "abstract": abstract,
        "doi": item.get("DOI", "") or doi,
    }


def lookup_crossref(title: str, mailto: Optional[str] = None, timeout: int = 10) -> Optional[dict]:
    """Look up paper metadata on CrossRef by title.

    Returns ``{"title", "authors", "year", "abstract", "doi"}`` or ``None``.

    CrossRef's polite pool (https://api.crossref.org) gives higher, more
    predictable rate limits when the request carries a ``mailto=`` parameter
    and a ``User-Agent`` header with the same address. We set both.

    Abstract field is usually empty on CrossRef — it's returned as JATS XML
    in ``message.abstract`` only for publishers that deposit it. We strip
    the tags when present; otherwise an empty string.
    """
    if not title:
        return None

    mailto = mailto or _crossref_mailto()
    url = (
        "https://api.crossref.org/works"
        f"?query.bibliographic={quote_plus(title)}&rows=3&mailto={quote_plus(mailto)}"
    )
    try:
        resp = requests.get(
            url,
            timeout=timeout,
            headers={"User-Agent": f"klemma/1.0 (mailto:{mailto})"},
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        logger.warning("CrossRef lookup failed for '%s': %s", title[:60], e)
        return None

    for item in data.get("message", {}).get("items", []):
        item_title = " ".join(item.get("title", []) or [])
        if not _titles_match(title, item_title):
            continue

        # Authors: CrossRef returns structured {family, given} pairs
        authors = ", ".join(
            f"{a.get('family', '')} {a.get('given', '')}".strip()
            for a in item.get("author", [])
            if a.get("family") or a.get("given")
        )

        # Year: prefer published-print, then issued, then published-online
        year: Optional[int] = None
        for date_field in ("published-print", "issued", "published-online"):
            parts = (item.get(date_field) or {}).get("date-parts") or [[]]
            if parts and parts[0] and parts[0][0]:
                try:
                    year = int(parts[0][0])
                    break
                except (TypeError, ValueError):
                    pass

        raw_abstract = item.get("abstract") or ""
        abstract = re.sub(r"<[^>]+>", "", raw_abstract).strip()

        return {
            "title": item_title,
            "authors": authors,
            "year": year,
            "abstract": abstract,
            "doi": item.get("DOI", ""),
        }

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

    # Layer 3: CrossRef enrichment — primary (and only) network lookup.
    # S2 is intentionally disabled on this path: it's rate-limited, flaky
    # under load, and CrossRef covers both CS and non-CS literature with
    # generous rate limits when using the polite pool (mailto param).
    if result["title"]:
        cr = lookup_crossref(result["title"])
        if cr:
            if not result["authors"]:
                result["authors"] = cr.get("authors", "")
            if result["year"] is None:
                result["year"] = cr.get("year")
            if not result["abstract"]:
                result["abstract"] = cr.get("abstract", "")
            if not result["doi"]:
                result["doi"] = cr.get("doi", "")

    return result
