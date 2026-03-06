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


class ChainSearchProvider:
    """Try multiple search providers in sequence — first hit wins.

    Default chain: S2 → CrossRef. If S2 rate-limits (429) or fails,
    CrossRef picks up the slack.
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
        backend: "s2" | "crossref" | "auto" | "" (default: "" = disabled)
        throttle: seconds between S2 API requests (default: 3.1)

    "auto" (and the on-demand default in `gaps suggest`) creates a
    ChainSearchProvider: S2 → CrossRef, so rate-limited S2 calls
    fall through to CrossRef automatically.

    Returns None if backend is empty or unknown.
    """
    if not config:
        return None

    backend = config.get("backend", "")
    if not backend:
        return None

    throttle = config.get("throttle", 3.1)

    if backend == "s2":
        return S2SearchProvider(throttle=throttle)

    if backend == "crossref":
        return CrossRefSearchProvider()

    if backend == "auto":
        return ChainSearchProvider([
            S2SearchProvider(throttle=throttle),
            CrossRefSearchProvider(),
        ])

    logger.warning("Unknown search backend: %s", backend)
    return None
