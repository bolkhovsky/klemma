"""Numbered-reference → citekey matching for papers/ (claim-provenance gap, PR-3).

Journals require numbered citations ("[5]"), so submitted papers carry no
[@citekey] markers and the citation checker used to skip them entirely.
This module parses the bibliography section into a map «number → citekey»
so check-citations can verify numbered manuscripts against the library.

Matching order per entry (first hit wins):
1. normalized DOI exact match (confidence 1.0)
2. fuzzy title match — metadata._titles_match on the parsed title (>0.6
   word overlap, confidence 0.85) or containment of the source title in
   the raw entry (confidence 0.75); when both years are known they must agree
3. author surnames overlap + exact year (confidence 0.7)

Unmatched positions are kept — they are findings in their own right
(the checker reports them as soft_warn).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Literal, Optional

from ..literature.draft_parser import find_bibliography_section
from ..literature.metadata import _titles_match
from ..literature.reference_parser import ParsedReference, parse_numbered_references

logger = logging.getLogger(__name__)


@dataclass
class RefMatch:
    """A resolved bibliography entry: which citekey, how, and how confidently."""

    number: int
    citekey: str
    method: Literal["doi", "title", "authors_year"]
    confidence: float


@dataclass
class RefMap:
    """Map of numbered bibliography entries to library citekeys."""

    number_to_citekey: dict[int, str] = field(default_factory=dict)
    unmatched: dict[int, ParsedReference] = field(default_factory=dict)
    matches: dict[int, RefMatch] = field(default_factory=dict)

    def match(self, number: int) -> Optional[RefMatch]:
        return self.matches.get(number)

    def confidence(self, number: int) -> float:
        m = self.matches.get(number)
        return m.confidence if m else 0.0


def build_ref_map(md_text: str, sources_meta: list[dict]) -> RefMap:
    """Build the [N] → citekey map from the file's bibliography section.

    sources_meta rows: {"citekey", "title", "authors", "year", "doi"}.
    Returns an empty RefMap when the file has no bibliography section.
    """
    ref_map = RefMap()
    span = find_bibliography_section(md_text)
    if span is None:
        return ref_map

    for number, ref in parse_numbered_references(md_text[span[0]:span[1]]):
        match = _match_reference(number, ref, sources_meta)
        if match:
            ref_map.matches[number] = match
            ref_map.number_to_citekey[number] = match.citekey
        else:
            ref_map.unmatched[number] = ref
    return ref_map


def collect_sources_meta(*, state=None, paper_store=None, user_library=None) -> list[dict]:
    """Gather {citekey, title, authors, year, doi} rows for reference matching.

    Project state first; user_library (+ paper_store for the metadata itself)
    adds citekeys the project DB does not know about. Never raises — a failed
    backend just contributes nothing.
    """
    meta: list[dict] = []
    seen: set[str] = set()

    if state is not None:
        try:
            for row in state.get_all_sources_metadata():
                citekey = row.get("id") or ""
                if not citekey or citekey in seen:
                    continue
                seen.add(citekey)
                meta.append({
                    "citekey": citekey,
                    "title": row.get("title") or "",
                    "authors": row.get("authors") or "",
                    "year": row.get("year"),
                    "doi": row.get("doi") or "",
                })
        except Exception:
            logger.debug("state sources metadata unavailable for ref matching")

    if user_library is not None and paper_store is not None:
        try:
            for src in user_library.get_all_sources():
                citekey = getattr(src, "citekey", "") or ""
                if not citekey or citekey in seen:
                    continue
                paper = paper_store.get_paper_by_id(getattr(src, "paper_id", ""))
                if paper is None:
                    continue
                seen.add(citekey)
                meta.append({
                    "citekey": citekey,
                    "title": paper.title or "",
                    "authors": paper.authors or "",
                    "year": paper.year,
                    "doi": paper.doi or "",
                })
        except Exception:
            logger.debug("user_library metadata unavailable for ref matching")

    return meta


# ---------------------------------------------------------------------------
# Matching internals
# ---------------------------------------------------------------------------

_DOI_PREFIXES = (
    "https://doi.org/",
    "http://doi.org/",
    "https://dx.doi.org/",
    "http://dx.doi.org/",
    "doi:",
)

# Surname tokens: ≥2 letters — initials ("J.", "К.") are single letters, while
# 2-letter surnames ("Ma", "Xu") are common and must survive
_SURNAME_RE = re.compile(r"[A-Za-zА-Яа-яЁё][A-Za-zА-Яа-яЁё\-]+")
_SURNAME_STOPWORDS = {"and", "et", "al", "др"}


def _normalize_doi(doi: Optional[str]) -> str:
    doi = (doi or "").strip().lower()
    for prefix in _DOI_PREFIXES:
        if doi.startswith(prefix):
            doi = doi[len(prefix):]
            break
    return doi.rstrip("./")


def _surnames(authors: Optional[str]) -> set[str]:
    return {
        t.lower()
        for t in _SURNAME_RE.findall(authors or "")
        if t.lower() not in _SURNAME_STOPWORDS
    }


def _same_year(a, b) -> bool:
    try:
        return int(a) == int(b)
    except (TypeError, ValueError):
        return False


def _title_contained(title: str, raw: str) -> bool:
    """True when ≥80 % of the source title's significant words appear in the raw entry.

    Complements _titles_match: heuristic title extraction breaks on
    "Author A. B."-style entries (initials split the string), but the raw
    entry still contains the full source title verbatim.
    """
    def normalize(s: str) -> set[str]:
        s = re.sub(r"[^\w\s]", "", s.lower())
        return {w for w in s.split() if len(w) > 2}

    title_words = normalize(title)
    if len(title_words) < 4:  # too short — would match everywhere
        return False
    raw_words = normalize(raw)
    return len(title_words & raw_words) / len(title_words) > 0.8


def _match_reference(
    number: int, ref: ParsedReference, sources_meta: list[dict],
) -> Optional[RefMatch]:
    # 1. Normalized DOI exact
    ref_doi = _normalize_doi(ref.doi)
    if ref_doi:
        for meta in sources_meta:
            if _normalize_doi(meta.get("doi")) == ref_doi:
                return RefMatch(number, meta["citekey"], "doi", 1.0)

    # 2. Fuzzy title (year must agree when both known): parsed-title overlap
    # first, then containment of the source title in the raw entry
    for meta in sources_meta:
        title = meta.get("title") or ""
        if not title:
            continue
        meta_year = meta.get("year")
        if ref.year and meta_year and not _same_year(ref.year, meta_year):
            continue
        if ref.title and _titles_match(ref.title, title):
            return RefMatch(number, meta["citekey"], "title", 0.85)
        if _title_contained(title, ref.raw):
            return RefMatch(number, meta["citekey"], "title", 0.75)

    # 3. Author surnames + exact year
    if ref.year and ref.authors:
        ref_surnames = _surnames(ref.authors)
        if ref_surnames:
            for meta in sources_meta:
                if not _same_year(ref.year, meta.get("year")):
                    continue
                if ref_surnames & _surnames(meta.get("authors")):
                    return RefMatch(number, meta["citekey"], "authors_year", 0.7)

    return None
