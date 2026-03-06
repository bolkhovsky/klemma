"""Suggest papers to acquire for filling reference gaps.

Pure skill — no file I/O, no CLI. Resolves top-scored gaps
via a SearchProvider and builds acquisition command strings.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from ..search import SearchProvider, SearchResult

logger = logging.getLogger(__name__)


@dataclass
class SuggestCandidate:
    """A reference gap enriched with search results and acquisition info."""

    ref_authors: str
    ref_year: int | None
    ref_title: str
    score: float
    sections: list[str]
    search_result: SearchResult | None = None
    pdf_url: str = ""
    doi: str = ""
    acquire_cmd: str = ""


def suggest_acquisitions(
    gaps: list[dict],
    search: SearchProvider,
    limit: int = 10,
) -> list[SuggestCandidate]:
    """Resolve top gaps via search provider.

    For each gap (sorted by score desc, limited):
    1. Call search.resolve(title, authors, year) for metadata
    2. If found, call search.resolve_pdf_url() for open-access PDF
    3. Build acquire_cmd string for CLI usage

    Returns list of SuggestCandidate, longest first.
    """
    sorted_gaps = sorted(gaps, key=lambda g: g.get("score", 0), reverse=True)

    candidates: list[SuggestCandidate] = []
    for gap in sorted_gaps:
        if len(candidates) >= limit:
            break

        title = gap.get("ref_title", "")
        authors = gap.get("ref_authors", "")
        year = gap.get("ref_year")
        score = gap.get("score", 0)

        # Parse sections — DB stores JSON arrays, GROUP_CONCAT joins them
        sections_raw = gap.get("dissertation_sections", "")
        sections = _parse_sections(sections_raw)

        candidate = SuggestCandidate(
            ref_authors=authors,
            ref_year=year,
            ref_title=title,
            score=score,
            sections=sections,
        )

        # Resolve metadata via search
        try:
            result = search.resolve(title, authors, year)
            if result:
                candidate.search_result = result
                candidate.doi = result.doi
                # Try to get PDF URL
                pdf_url = search.resolve_pdf_url(
                    result.title, result.authors, result.year,
                )
                if pdf_url:
                    candidate.pdf_url = pdf_url
                elif result.doi:
                    candidate.doi = result.doi
        except Exception:
            logger.warning("Search failed for '%s'", title[:60], exc_info=True)

        # Build acquire command
        if candidate.pdf_url:
            section_flags = " ".join(f"-s {s}" for s in candidate.sections)
            candidate.acquire_cmd = f"klemma acquire {candidate.pdf_url}"
            if section_flags:
                candidate.acquire_cmd += f" {section_flags}"
        elif candidate.doi:
            section_flags = " ".join(f"-s {s}" for s in candidate.sections)
            candidate.acquire_cmd = f"klemma acquire https://doi.org/{candidate.doi}"
            if section_flags:
                candidate.acquire_cmd += f" {section_flags}"

        candidates.append(candidate)

    return candidates


def _parse_sections(raw: str) -> list[str]:
    """Parse sections from DB dissertation_sections field.

    The DB stores JSON arrays per row (e.g. '["1.3", "2.3"]').
    GROUP_CONCAT joins multiple rows: '["1.3", "2.3"],["1.4"]'.
    We need to extract the unique flat list: ["1.3", "2.3", "1.4"].
    """
    if not raw:
        return []

    # Wrap in array brackets if GROUP_CONCAT joined multiple JSON arrays
    # e.g. '["1.3"],["2.3"]' → '[["1.3"],["2.3"]]'
    normalized = raw.strip()
    try:
        parsed = json.loads(normalized)
        if isinstance(parsed, list):
            # Could be flat ["1.3", "2.3"] or nested [["1.3"], ["2.3"]]
            result: list[str] = []
            for item in parsed:
                if isinstance(item, list):
                    result.extend(str(s) for s in item)
                else:
                    result.append(str(item))
            return list(dict.fromkeys(result))  # dedup preserving order
    except (json.JSONDecodeError, TypeError):
        pass

    # GROUP_CONCAT of multiple JSON arrays: '["1.3","2.3"],["1.4"]'
    try:
        parsed = json.loads(f"[{normalized}]")
        if isinstance(parsed, list):
            result = []
            for item in parsed:
                if isinstance(item, list):
                    result.extend(str(s) for s in item)
                else:
                    result.append(str(item))
            return list(dict.fromkeys(result))
    except (json.JSONDecodeError, TypeError):
        pass

    # Last resort: plain comma-separated
    return [s.strip().strip('"') for s in raw.split(",") if s.strip().strip('"')]
