"""Provider-agnostic paper search — resolve reference gaps to acquisition targets.

Root-level provider (alongside embeddings.py) to avoid cross-package
dependency between literature/ and evaluation/.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

logger = logging.getLogger(__name__)


@dataclass
class SearchResult:
    """Resolved paper metadata from an external search API."""

    title: str
    authors: str = ""
    year: int | None = None
    abstract: str = ""
    doi: str = ""
    pdf_url: str = ""
    source_api: str = ""  # "s2", "arxiv", "crossref", "unpaywall"


@runtime_checkable
class SearchProvider(Protocol):
    """Protocol for paper search backends."""

    backend_name: str

    def resolve(
        self,
        title: str,
        authors: str = "",
        year: int | None = None,
    ) -> SearchResult | None:
        """Look up paper metadata by title/authors/year."""
        ...

    def resolve_pdf_url(
        self,
        title: str,
        authors: str = "",
        year: int | None = None,
    ) -> str | None:
        """Resolve an open-access PDF URL for a paper."""
        ...


class S2SearchProvider:
    """Semantic Scholar search — wraps literature.metadata.lookup_s2().

    Best for CS/ML papers. Rate-limited (~1 req/3s), returns abstract.
    """

    backend_name = "s2"

    def __init__(self, throttle: float = 3.1) -> None:
        self._throttle = throttle

    def resolve(
        self,
        title: str,
        authors: str = "",
        year: int | None = None,
    ) -> SearchResult | None:
        from .literature.metadata import lookup_s2

        hit = lookup_s2(title)
        if not hit:
            return None

        return SearchResult(
            title=hit.get("title", title),
            authors=hit.get("authors", authors),
            year=hit.get("year") or year,
            abstract=hit.get("abstract", ""),
            doi=hit.get("doi", ""),
            source_api="s2",
        )

    def resolve_pdf_url(
        self,
        title: str,
        authors: str = "",
        year: int | None = None,
    ) -> str | None:
        from .evaluation.resolvers import resolve_pdf_url as _resolve

        result = _resolve(title, authors, year)
        return result.pdf_url if result and result.pdf_url else None


class CrossRefSearchProvider:
    """CrossRef search — free, no auth, generous rate limits.

    Broader coverage than S2 (especially non-CS). No abstract.
    Wraps evaluation.resolvers for both metadata and PDF resolution.
    """

    backend_name = "crossref"

    def resolve(
        self,
        title: str,
        authors: str = "",
        year: int | None = None,
    ) -> SearchResult | None:
        from urllib.parse import quote_plus

        import requests

        from .evaluation.resolvers import _titles_match

        query = title
        url = (
            f"https://api.crossref.org/works"
            f"?query.bibliographic={quote_plus(query)}&rows=3"
        )
        try:
            resp = requests.get(
                url, timeout=10,
                headers={"User-Agent": "klemma/0.4 (mailto:klemma@example.com)"},
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            logger.warning("CrossRef lookup failed for '%s': %s", title[:60], e)
            return None

        for item in data.get("message", {}).get("items", []):
            item_title = " ".join(item.get("title", []))
            if not _titles_match(title, item_title):
                continue

            # Extract authors
            cr_authors = ", ".join(
                f"{a.get('family', '')} {a.get('given', '')}".strip()
                for a in item.get("author", [])
            ) or authors

            # Extract year from published-print or issued
            cr_year = year
            for date_field in ("published-print", "issued", "published-online"):
                parts = item.get(date_field, {}).get("date-parts", [[]])
                if parts and parts[0] and parts[0][0]:
                    cr_year = int(parts[0][0])
                    break

            return SearchResult(
                title=item_title,
                authors=cr_authors,
                year=cr_year,
                doi=item.get("DOI", ""),
                source_api="crossref",
            )

        return None

    def resolve_pdf_url(
        self,
        title: str,
        authors: str = "",
        year: int | None = None,
    ) -> str | None:
        from .evaluation.resolvers import resolve_pdf_url as _resolve

        result = _resolve(title, authors, year)
        return result.pdf_url if result and result.pdf_url else None


class OpenAlexSearchProvider:
    """OpenAlex search — 250M+ works, no API key, 10 req/s polite pool.

    Free, no authentication needed. Provides abstract via inverted-index
    reconstruction and richer coverage than S2 for non-CS disciplines.
    Polite pool requires a mailto address in the User-Agent header.
    """

    backend_name = "openalex"
    _API_BASE = "https://api.openalex.org"

    def __init__(self, mailto: str = "") -> None:
        self._mailto = mailto or "klemma@example.com"

    def _headers(self) -> dict:
        return {"User-Agent": f"klemma/1.0 (mailto:{self._mailto})"}

    @staticmethod
    def _reconstruct_abstract(inverted_index: dict | None) -> str:
        """Reconstruct abstract text from OpenAlex inverted index format.

        Format: {word: [position1, position2, ...], ...}
        """
        if not inverted_index:
            return ""
        words: list[tuple[int, str]] = []
        for word, positions in inverted_index.items():
            for pos in positions:
                words.append((pos, word))
        words.sort()
        return " ".join(w for _, w in words)

    @staticmethod
    def _extract_authors(authorships: list) -> str:
        """Extract author display names from OpenAlex authorships list."""
        names = []
        for a in authorships:
            display = a.get("author", {}).get("display_name", "")
            if display:
                names.append(display)
        return ", ".join(names)

    def resolve(
        self,
        title: str,
        authors: str = "",
        year: int | None = None,
    ) -> SearchResult | None:
        from urllib.parse import quote_plus

        import requests

        from .evaluation.resolvers import _titles_match

        url = f"{self._API_BASE}/works?search={quote_plus(title)}&per-page=3"
        try:
            resp = requests.get(url, timeout=10, headers=self._headers())
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            logger.warning("OpenAlex lookup failed for '%s': %s", title[:60], e)
            return None

        for work in data.get("results", []):
            work_title = work.get("title") or ""
            if not _titles_match(title, work_title):
                continue

            oa_year = work.get("publication_year") or year
            doi = (work.get("doi") or "").replace("https://doi.org/", "")
            abstract = self._reconstruct_abstract(
                work.get("abstract_inverted_index")
            )
            oa_authors = self._extract_authors(work.get("authorships", []))

            return SearchResult(
                title=work_title,
                authors=oa_authors or authors,
                year=oa_year,
                abstract=abstract,
                doi=doi,
                source_api="openalex",
            )

        return None

    def resolve_pdf_url(
        self,
        title: str,
        authors: str = "",
        year: int | None = None,
    ) -> str | None:
        from urllib.parse import quote_plus

        import requests

        from .evaluation.resolvers import _titles_match

        url = f"{self._API_BASE}/works?search={quote_plus(title)}&per-page=3"
        try:
            resp = requests.get(url, timeout=10, headers=self._headers())
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            logger.warning("OpenAlex PDF resolve failed for '%s': %s", title[:60], e)
            return None

        for work in data.get("results", []):
            work_title = work.get("title") or ""
            if not _titles_match(title, work_title):
                continue
            # Check open access PDF URL
            oa_info = work.get("open_access", {})
            oa_url = oa_info.get("oa_url")
            if oa_url:
                return oa_url
            # Check primary location PDF
            primary = work.get("primary_location") or {}
            pdf_url = primary.get("pdf_url")
            if pdf_url:
                return pdf_url
        return None


class ChainSearchProvider:
    """Try multiple search providers in sequence — first hit wins.

    Default chain: CrossRef → S2. CrossRef has generous rate limits
    and broad coverage; S2 is last-resort fallback for CS/ML papers.
    """

    def __init__(self, providers: list) -> None:
        self._providers = providers
        names = [p.backend_name for p in providers]
        self.backend_name = "+".join(names)

    def resolve(
        self,
        title: str,
        authors: str = "",
        year: int | None = None,
    ) -> SearchResult | None:
        for provider in self._providers:
            try:
                result = provider.resolve(title, authors, year)
                if result:
                    return result
            except Exception:
                logger.debug(
                    "%s failed for '%s', trying next",
                    provider.backend_name, title[:60],
                    exc_info=True,
                )
        return None

    def resolve_pdf_url(
        self,
        title: str,
        authors: str = "",
        year: int | None = None,
    ) -> str | None:
        for provider in self._providers:
            try:
                url = provider.resolve_pdf_url(title, authors, year)
                if url:
                    return url
            except Exception:
                logger.debug(
                    "%s pdf resolve failed for '%s', trying next",
                    provider.backend_name, title[:60],
                    exc_info=True,
                )
        return None


def create_search(
    config: dict,
    api_keys: dict | None = None,
) -> SearchProvider | None:
    """Create a SearchProvider from config dict.

    Config keys:
        backend: "s2" | "crossref" | "openalex" | "auto" | "" (default: "" = disabled)
        throttle: seconds between S2 API requests (default: 3.1)
        mailto: email for OpenAlex polite pool User-Agent (default: klemma@example.com)

    "auto" creates a ChainSearchProvider: OpenAlex → CrossRef → S2.
    OpenAlex handles most lookups (free, generous rate limits, abstract included);
    CrossRef is fallback for DOI metadata; S2 is last-resort for CS/ML papers.

    Returns None if backend is empty or unknown.
    """
    if not config:
        return None

    backend = config.get("backend", "")
    if not backend:
        return None

    throttle = config.get("throttle", 3.1)
    mailto = config.get("mailto", "")

    if backend == "s2":
        return S2SearchProvider(throttle=throttle)

    if backend == "crossref":
        return CrossRefSearchProvider()

    if backend == "openalex":
        return OpenAlexSearchProvider(mailto=mailto)

    if backend == "auto":
        return ChainSearchProvider([
            OpenAlexSearchProvider(mailto=mailto),
            CrossRefSearchProvider(),
            S2SearchProvider(throttle=throttle),
        ])

    logger.warning("Unknown search backend: %s", backend)
    return None
