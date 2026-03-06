"""Duplicate source detection by metadata."""

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class DuplicatePair:
    """A pair of sources suspected to be duplicates."""

    citekey_a: str
    citekey_b: str
    strategy: str
    confidence: float  # 0.0-1.0
    detail: str = ""


def _normalize(text: str) -> str:
    """Lowercase, strip, collapse whitespace."""
    return " ".join(text.lower().split())


def _find_doi_duplicates(sources: list[dict]) -> list[DuplicatePair]:
    """Find sources sharing the same DOI."""
    doi_map: dict[str, list[str]] = {}
    for s in sources:
        doi = (s.get("doi") or "").strip().lower()
        if doi:
            doi_map.setdefault(doi, []).append(s["id"])

    pairs = []
    for doi, ids in doi_map.items():
        if len(ids) > 1:
            for i in range(len(ids)):
                for j in range(i + 1, len(ids)):
                    pairs.append(DuplicatePair(
                        citekey_a=ids[i],
                        citekey_b=ids[j],
                        strategy="doi",
                        confidence=1.0,
                        detail=f"DOI: {doi}",
                    ))
    return pairs


def _find_author_year_title_duplicates(sources: list[dict]) -> list[DuplicatePair]:
    """Find sources with same first author surname + year + similar title prefix."""
    def _first_author_surname(authors: str) -> str:
        if not authors:
            return ""
        first = authors.split(",")[0].split(" and ")[0].strip()
        parts = first.split()
        return parts[-1].lower() if parts else ""

    key_map: dict[tuple, list[str]] = {}
    for s in sources:
        surname = _first_author_surname(s.get("authors") or "")
        year = s.get("year")
        title = _normalize(s.get("title") or "")[:50]
        if surname and year and title:
            key = (surname, year, title)
            key_map.setdefault(key, []).append(s["id"])

    pairs = []
    for (surname, year, title), ids in key_map.items():
        if len(ids) > 1:
            for i in range(len(ids)):
                for j in range(i + 1, len(ids)):
                    pairs.append(DuplicatePair(
                        citekey_a=ids[i],
                        citekey_b=ids[j],
                        strategy="author+year+title",
                        confidence=0.9,
                        detail=f"{surname} ({year}): {title}...",
                    ))
    return pairs


def _find_title_prefix_duplicates(sources: list[dict]) -> list[DuplicatePair]:
    """Find sources with identical title prefix (first 50 chars, normalized)."""
    prefix_map: dict[str, list[str]] = {}
    for s in sources:
        title = _normalize(s.get("title") or "")
        prefix = title[:50]
        if len(prefix) >= 15:  # skip very short titles
            prefix_map.setdefault(prefix, []).append(s["id"])

    pairs = []
    for prefix, ids in prefix_map.items():
        if len(ids) > 1:
            for i in range(len(ids)):
                for j in range(i + 1, len(ids)):
                    pairs.append(DuplicatePair(
                        citekey_a=ids[i],
                        citekey_b=ids[j],
                        strategy="title_prefix",
                        confidence=0.7,
                        detail=f"Title: {prefix}...",
                    ))
    return pairs


def find_duplicates(sources: list[dict]) -> list[DuplicatePair]:
    """Find duplicate sources using all metadata strategies.

    Pure skill: receives source list, returns duplicate pairs.
    Sources must have keys: id, title, authors, year, doi.

    Strategies (in confidence order):
    1. DOI match (confidence=1.0)
    2. Author surname + year + title prefix (confidence=0.9)
    3. Title prefix 50 chars (confidence=0.7)
    """
    if len(sources) < 2:
        return []

    all_pairs: list[DuplicatePair] = []
    all_pairs.extend(_find_doi_duplicates(sources))
    all_pairs.extend(_find_author_year_title_duplicates(sources))
    all_pairs.extend(_find_title_prefix_duplicates(sources))

    # Deduplicate: same pair may match multiple strategies — keep highest confidence
    seen: dict[tuple[str, str], DuplicatePair] = {}
    for pair in all_pairs:
        key = (min(pair.citekey_a, pair.citekey_b), max(pair.citekey_a, pair.citekey_b))
        existing = seen.get(key)
        if existing is None or pair.confidence > existing.confidence:
            seen[key] = pair

    result = sorted(seen.values(), key=lambda p: (-p.confidence, p.citekey_a))
    if result:
        logger.info("Found %d duplicate pair(s)", len(result))
    return result
