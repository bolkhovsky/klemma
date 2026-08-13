"""Parse bibliography strings into structured references (#76).

Pure string processing — no AI, no external deps. Handles common
academic citation formats (APA, numbered, inline). Used by the
Klemma onboarding pipeline to extract references from draft PDFs.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class ParsedReference:
    """A structured reference parsed from a bibliography string."""

    raw: str
    authors: str = ""
    year: int | None = None
    title: str = ""
    journal: str = ""
    doi: str = ""
    url: str = ""


# DOI pattern: 10.XXXX/... (standard DOI format)
_DOI_RE = re.compile(r"10\.\d{4,}/[^\s,;}\]]+")

# URL pattern
_URL_RE = re.compile(r"https?://[^\s,;}\]]+")

# Year pattern: 4-digit year in parentheses or standalone, 1900-2099
_YEAR_RE = re.compile(r"\b((?:19|20)\d{2})\b")

# Numbered reference prefix: [1], 1., 1)
_NUMBERED_PREFIX_RE = re.compile(r"^\s*\[?\d+[.\])]\s*")

# Numbered entry start for number-preserving parsing. Capped at 3 digits so a
# wrapped line starting with a year ("2011. — Vol. …") is not taken for a new entry.
_NUMBERED_ENTRY_RE = re.compile(r"^\s*\[?(\d{1,3})[.\])]\s+", re.MULTILINE)


def parse_reference(raw: str) -> ParsedReference:
    """Parse a single bibliography string into structured fields.

    Handles:
    - APA: "Smith, J., & Jones, K. (2020). Title. Journal, 1(2), 3-4."
    - Numbered: "[1] Smith J. Title. Journal. 2020;1:3-4."
    - Inline DOI: "... https://doi.org/10.1234/test"

    Returns ParsedReference with best-effort extraction. Empty fields
    when parsing fails — never raises.
    """
    ref = ParsedReference(raw=raw.strip())

    # Extract DOI
    doi_match = _DOI_RE.search(raw)
    if doi_match:
        ref.doi = doi_match.group().rstrip(".")

    # Extract URL (prefer non-DOI URL if both present)
    url_match = _URL_RE.search(raw)
    if url_match:
        url = url_match.group().rstrip(".")
        if "doi.org" not in url:
            ref.url = url
        elif not ref.doi:
            ref.doi = _DOI_RE.search(url).group() if _DOI_RE.search(url) else ""
            ref.url = url

    # Strip numbered prefix
    text = _NUMBERED_PREFIX_RE.sub("", raw).strip()

    # Extract year
    year_match = _YEAR_RE.search(text)
    if year_match:
        ref.year = int(year_match.group(1))

    # Try APA-style parsing: "Authors (Year). Title. Journal..."
    apa = _try_apa(text)
    if apa:
        ref.authors = apa["authors"]
        ref.title = apa["title"]
        ref.journal = apa["journal"]
        if not ref.year and apa.get("year"):
            ref.year = apa["year"]
        return ref

    # Fallback: split on periods, heuristic assignment
    ref.authors, ref.title, ref.journal = _split_heuristic(text, ref.year)

    return ref


def parse_references(text: str) -> list[ParsedReference]:
    """Parse a bibliography section into individual references.

    Splits on numbered prefixes ([1], 1., etc.) or double newlines.
    Filters out empty/too-short entries.
    """
    # Try splitting on numbered references first
    numbered = re.split(r"\n\s*\[?\d+[.\])]\s+", "\n" + text)
    if len(numbered) > 2:
        entries = [e.strip() for e in numbered if e.strip()]
    else:
        # Fall back to double-newline or single-newline splitting
        entries = [e.strip() for e in re.split(r"\n\n+|\n(?=[A-Z])", text) if e.strip()]

    results = []
    for entry in entries:
        # Skip entries that are too short to be real references
        if len(entry) < 20:
            continue
        ref = parse_reference(entry)
        results.append(ref)

    return results


def parse_numbered_references(text: str) -> list[tuple[int, ParsedReference]]:
    """Parse a numbered bibliography section, PRESERVING entry numbers.

    Like parse_references(), but splits on entry markers ([1], 1., 1)) via
    finditer and returns (number, ParsedReference) pairs — the number is what
    in-text markers like "[5]" refer to. Entries too short to be real
    references (< 20 chars) are skipped.
    """
    matches = list(_NUMBERED_ENTRY_RE.finditer(text))
    results: list[tuple[int, ParsedReference]] = []
    for i, m in enumerate(matches):
        entry_end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        entry = text[m.end():entry_end].strip()
        if len(entry) < 20:
            continue
        results.append((int(m.group(1)), parse_reference(entry)))
    return results


# ---------------------------------------------------------------------------
# Internal parsers
# ---------------------------------------------------------------------------


def _try_apa(text: str) -> dict | None:
    """Try to parse APA-style: "Authors (Year). Title. Journal, vol(issue), pages."

    Returns dict with authors/year/title/journal or None if doesn't match.
    """
    # Pattern: "text (YYYY)" or "text, YYYY"
    m = re.match(
        r"^(.+?)\s*\((\d{4})\)\.\s*(.+)",
        text,
    )
    if not m:
        return None

    authors = m.group(1).strip().rstrip(",")
    year = int(m.group(2))
    rest = m.group(3).strip()

    # Split remaining on first period to get title vs journal
    parts = rest.split(". ", 1)
    title = parts[0].strip().rstrip(".")
    journal = parts[1].strip().rstrip(".") if len(parts) > 1 else ""

    # Clean up journal — remove volume/pages info
    if journal:
        journal = re.split(r",\s*\d+", journal)[0].strip()

    return {"authors": authors, "year": year, "title": title, "journal": journal}


def _split_heuristic(text: str, year: int | None) -> tuple[str, str, str]:
    """Fallback parser: split on periods, assign by position.

    Returns (authors, title, journal).
    """
    # Remove year in parens for cleaner splitting
    cleaned = text
    if year:
        cleaned = re.sub(rf"\({year}\)", "", cleaned)
        cleaned = re.sub(rf"\b{year}\b", "", cleaned, count=1)

    parts = [p.strip() for p in cleaned.split(".") if p.strip()]

    if len(parts) >= 3:
        return parts[0], parts[1], parts[2]
    elif len(parts) == 2:
        return parts[0], parts[1], ""
    elif len(parts) == 1:
        return "", parts[0], ""
    return "", "", ""
