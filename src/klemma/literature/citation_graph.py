"""OpenAlex citation-graph client for ``klemma gaps`` discovery.

Walks a seed paper's OpenAlex citation graph — the works it *references* plus the
works that *cite* it — so the gaps skill can surface neighbours not yet in the
library. Standalone and SaaS-importable: it does **not** reuse
``search.OpenAlexSearchProvider`` because that exposes neither the OpenAlex work
id nor ``referenced_works``, both of which the graph walk needs.

References use the documented ``referenced_works`` + ``openalex:`` batch path
(the seed fetch already returns ``referenced_works``, so it is cost-neutral).
Citers use the ``cites:`` filter. Both sides filter to ``type:article`` to drop
EGU/Copernicus open-review replies and other paratext that otherwise pollute the
downstream embedding rerank.
"""

import logging
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import quote_plus

import requests

from .metadata import _titles_match

logger = logging.getLogger(__name__)

_API_BASE = "https://api.openalex.org"
_DEFAULT_MAILTO = "klemma@litresearch.ru"
_BATCH = 50  # OpenAlex OR-filter chunk size for referenced_works
_CITERS_CAP = 200  # OpenAlex max per-page; v1 fetches the first page of citers

_SEED_SELECT = "id,doi,title,referenced_works"
_CAND_SELECT = (
    "id,doi,title,publication_year,authorships,"
    "abstract_inverted_index,cited_by_count,primary_location"
)


@dataclass
class SeedWork:
    """Resolved seed paper: OpenAlex id + its outgoing reference ids."""

    openalex_id: str  # bare "W..." id
    doi: str
    title: str
    referenced_works: list[str] = field(default_factory=list)  # bare "W..." ids


@dataclass
class Candidate:
    """A citation-graph neighbour of the seed."""

    openalex_id: str
    doi: str
    title: str
    abstract: str
    year: Optional[int]
    venue: str
    cited_by: int
    first_author: str
    relation: str  # "cites" (cites seed) | "ref" (seed cites it) | "both"


def _headers(mailto: str) -> dict:
    return {"User-Agent": f"klemma/1.0 (mailto:{mailto})"}


def _bare_id(oa_id: str) -> str:
    """Normalize ``https://openalex.org/W123`` (or ``W123``) → ``W123``."""
    return (oa_id or "").rsplit("/", 1)[-1]


def _strip_doi(doi: str) -> str:
    return (doi or "").replace("https://doi.org/", "").strip().lower()


def _reconstruct_abstract(inverted_index: Optional[dict]) -> str:
    """Rebuild abstract text from OpenAlex inverted-index format.

    Format: ``{word: [pos1, pos2, ...], ...}``. Returns ``""`` when absent.
    """
    if not inverted_index:
        return ""
    words: list[tuple[int, str]] = []
    for word, positions in inverted_index.items():
        for pos in positions:
            words.append((pos, word))
    words.sort()
    return " ".join(w for _, w in words)


def _get(url: str, mailto: str, timeout: int) -> Optional[dict]:
    """GET + parse JSON; returns None on any network/HTTP error (never raises)."""
    try:
        resp = requests.get(url, timeout=timeout, headers=_headers(mailto))
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logger.warning("OpenAlex request failed (%s): %s", url[:90], e)
        return None


def _to_candidate(work: dict, relation: str) -> Candidate:
    source = (work.get("primary_location") or {}).get("source") or {}
    authorships = work.get("authorships") or []
    first_author = ""
    if authorships:
        first_author = (authorships[0].get("author") or {}).get("display_name", "")
    return Candidate(
        openalex_id=_bare_id(work.get("id", "")),
        doi=_strip_doi(work.get("doi") or ""),
        title=work.get("title") or "",
        abstract=_reconstruct_abstract(work.get("abstract_inverted_index")),
        year=work.get("publication_year"),
        venue=source.get("display_name") or "",
        cited_by=work.get("cited_by_count") or 0,
        first_author=first_author,
        relation=relation,
    )


def fetch_seed_work(
    *, doi: str = "", title: str = "", mailto: str = "", timeout: int = 10
) -> Optional[SeedWork]:
    """Resolve the seed paper on OpenAlex and return its id + referenced_works.

    Prefers an exact DOI lookup. For a no-DOI seed, fetches the top few title
    hits and applies the same fuzzy title-match gate ``OpenAlexSearchProvider``
    uses — ``search=...&per-page=1`` alone can return the wrong work.
    """
    mailto = mailto or _DEFAULT_MAILTO
    work: Optional[dict] = None

    doi = _strip_doi(doi)
    if doi:
        data = _get(f"{_API_BASE}/works/doi:{doi}?select={_SEED_SELECT}", mailto, timeout)
        if data and data.get("id"):
            work = data

    if work is None and title:
        data = _get(
            f"{_API_BASE}/works?search={quote_plus(title)}&per-page=5&select={_SEED_SELECT}",
            mailto,
            timeout,
        )
        for w in (data or {}).get("results", []):
            if _titles_match(title, w.get("title") or ""):
                work = w
                break

    if not work:
        return None

    return SeedWork(
        openalex_id=_bare_id(work.get("id", "")),
        doi=_strip_doi(work.get("doi") or ""),
        title=work.get("title") or "",
        referenced_works=[_bare_id(x) for x in (work.get("referenced_works") or [])],
    )


def fetch_citation_graph(
    seed: SeedWork, *, mailto: str = "", timeout: int = 10
) -> list[Candidate]:
    """Fetch the seed's references + citers as deduped ``Candidate`` neighbours.

    References: ``referenced_works`` ids batch-fetched via ``openalex:`` OR-filter
    (chunks of 50). Citers: ``cites:<seed>`` filter (first page, ``type:article``).
    A work appearing on both sides is tagged ``relation="both"``.
    """
    mailto = mailto or _DEFAULT_MAILTO
    by_id: dict[str, Candidate] = {}

    # References — batch-fetch the seed's referenced_works by id.
    refs = [r for r in seed.referenced_works if r]
    for i in range(0, len(refs), _BATCH):
        chunk = refs[i : i + _BATCH]
        url = (
            f"{_API_BASE}/works?filter=openalex:{'|'.join(chunk)},type:article"
            f"&per-page={_BATCH}&select={_CAND_SELECT}"
        )
        data = _get(url, mailto, timeout)
        for w in (data or {}).get("results", []):
            cand = _to_candidate(w, "ref")
            if cand.openalex_id:
                by_id[cand.openalex_id] = cand

    # Citers — works that cite the seed.
    url = (
        f"{_API_BASE}/works?filter=cites:{seed.openalex_id},type:article"
        f"&per-page={_CITERS_CAP}&select={_CAND_SELECT}"
    )
    data = _get(url, mailto, timeout)
    if data:
        total = (data.get("meta") or {}).get("count", 0)
        if total > _CITERS_CAP:
            logger.info(
                "seed %s has %d citers; using the first %d",
                seed.openalex_id, total, _CITERS_CAP,
            )
        for w in data.get("results", []):
            cand = _to_candidate(w, "cites")
            if not cand.openalex_id:
                continue
            if cand.openalex_id in by_id:
                by_id[cand.openalex_id].relation = "both"
            else:
                by_id[cand.openalex_id] = cand

    return list(by_id.values())
