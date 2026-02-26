"""Paper PDF resolvers: arXiv, CrossRef → Unpaywall.

Free, no-auth APIs for resolving paper titles to downloadable PDF URLs.
Tried in order: arXiv (best for CS/ML), CrossRef+Unpaywall (broader coverage).
"""

from __future__ import annotations

import logging
import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Optional
from urllib.parse import quote_plus

import requests

logger = logging.getLogger(__name__)

_ARXIV_DELAY = 3.0  # seconds between arXiv API requests
_CROSSREF_TIMEOUT = 10
_UNPAYWALL_TIMEOUT = 10
_UNPAYWALL_EMAIL = "klemma@example.com"

_last_arxiv_call: float = 0.0


@dataclass
class ResolvedPaper:
    title: str
    authors: str = ""
    year: Optional[int] = None
    doi: str = ""
    pdf_url: str = ""
    source: str = ""  # "arxiv" | "unpaywall" | ""


def resolve_arxiv(
    title: str, authors: str = "", year: Optional[int] = None
) -> Optional[ResolvedPaper]:
    """Search arXiv API by title. Returns first match with PDF link.

    Free, no auth. Rate limit: 3s between requests.
    """
    global _last_arxiv_call
    elapsed = time.monotonic() - _last_arxiv_call
    if elapsed < _ARXIV_DELAY:
        time.sleep(_ARXIV_DELAY - elapsed)

    query = f"ti:{title}"
    url = f"https://export.arxiv.org/api/query?search_query={quote_plus(query)}&max_results=3"

    try:
        _last_arxiv_call = time.monotonic()
        resp = requests.get(url, timeout=15)
        resp.raise_for_status()
    except Exception as e:
        logger.debug("arXiv API error: %s", e)
        return None

    try:
        root = ET.fromstring(resp.text)
    except ET.ParseError:
        return None

    ns = {"atom": "http://www.w3.org/2005/Atom"}
    for entry in root.findall("atom:entry", ns):
        entry_title = (entry.findtext("atom:title", "", ns) or "").strip()
        if not entry_title:
            continue

        # Basic title similarity check
        if not _titles_match(title, entry_title):
            continue

        pdf_link = ""
        for link in entry.findall("atom:link", ns):
            if link.get("title") == "pdf":
                pdf_link = link.get("href", "")
                break

        if not pdf_link:
            # Construct from arXiv ID
            arxiv_id = (entry.findtext("atom:id", "", ns) or "")
            if "arxiv.org/abs/" in arxiv_id:
                aid = arxiv_id.split("/abs/")[-1]
                pdf_link = f"https://arxiv.org/pdf/{aid}.pdf"

        entry_authors = ", ".join(
            (a.findtext("atom:name", "", ns) or "")
            for a in entry.findall("atom:author", ns)
        )

        pub_date = entry.findtext("atom:published", "", ns)
        entry_year = int(pub_date[:4]) if pub_date and len(pub_date) >= 4 else None

        return ResolvedPaper(
            title=entry_title,
            authors=entry_authors,
            year=entry_year,
            pdf_url=pdf_link,
            source="arxiv",
        )

    return None


def resolve_crossref_doi(
    title: str, authors: str = "", year: Optional[int] = None
) -> Optional[str]:
    """Search CrossRef for a DOI matching the title. Free, no auth."""
    query = title
    url = f"https://api.crossref.org/works?query.bibliographic={quote_plus(query)}&rows=3"

    try:
        resp = requests.get(
            url, timeout=_CROSSREF_TIMEOUT,
            headers={"User-Agent": "klemma/0.4 (mailto:klemma@example.com)"},
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        logger.debug("CrossRef API error: %s", e)
        return None

    items = data.get("message", {}).get("items", [])
    for item in items:
        item_title = " ".join(item.get("title", []))
        if _titles_match(title, item_title):
            return item.get("DOI", "")
    return None


def resolve_unpaywall(doi: str, email: str = _UNPAYWALL_EMAIL) -> Optional[str]:
    """Get open-access PDF URL from Unpaywall. Free, no auth."""
    url = f"https://api.unpaywall.org/v2/{quote_plus(doi)}?email={email}"
    try:
        resp = requests.get(url, timeout=_UNPAYWALL_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        logger.debug("Unpaywall API error: %s", e)
        return None

    best = data.get("best_oa_location")
    if best:
        return best.get("url_for_pdf") or best.get("url")
    return None


def resolve_pdf_url(
    title: str, authors: str = "", year: Optional[int] = None
) -> ResolvedPaper:
    """Try arXiv → CrossRef+Unpaywall. Return best result."""
    # 1. arXiv (best for CS/ML papers)
    result = resolve_arxiv(title, authors, year)
    if result and result.pdf_url:
        return result

    # 2. CrossRef → DOI → Unpaywall
    doi = resolve_crossref_doi(title, authors, year)
    if doi:
        pdf_url = resolve_unpaywall(doi)
        if pdf_url:
            return ResolvedPaper(
                title=title, authors=authors, year=year,
                doi=doi, pdf_url=pdf_url, source="unpaywall",
            )
        # Have DOI but no open-access PDF
        return ResolvedPaper(
            title=title, authors=authors, year=year,
            doi=doi, source="",
        )

    return ResolvedPaper(title=title, authors=authors, year=year)


def _titles_match(query: str, candidate: str) -> bool:
    """Fuzzy title comparison: normalize and check word overlap."""
    def normalize(s: str) -> set[str]:
        s = re.sub(r"[^\w\s]", "", s.lower())
        return {w for w in s.split() if len(w) > 2}

    q = normalize(query)
    c = normalize(candidate)
    if not q or not c:
        return False
    overlap = len(q & c) / max(len(q), len(c))
    return overlap > 0.6
