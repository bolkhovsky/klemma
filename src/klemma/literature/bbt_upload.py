"""BetterBibTeX JSON parser for SaaS upload path.

Used by ``POST /library/import-bbt``: accepts the raw bytes of a BBT JSON
export, returns a list of ``BbtEntry`` dataclasses (one per non-attachment
item) for downstream matching against the user's library.

Mirrors the field-extraction logic in ``literature/pdf.py::_load_bbt_json``
(CLI mode) but is I/O-free so it can accept an HTTP upload body.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Optional


@dataclass
class BbtEntry:
    """One BetterBibTeX record reduced to the fields we need for matching."""

    citekey: str
    doi: Optional[str] = None
    title: str = ""
    first_author_lastname: str = ""
    year: Optional[int] = None


def parse_bbt_upload(data: bytes) -> list[BbtEntry]:
    """Parse BBT JSON bytes into a list of entries.

    Silently skips items without a citationKey or of type ``attachment`` /
    ``note``. Malformed JSON raises ``ValueError`` with a cleaned-up message.
    """
    try:
        doc = json.loads(data.decode("utf-8"))
    except UnicodeDecodeError as exc:
        raise ValueError(f"BBT JSON must be UTF-8: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid BBT JSON: {exc.msg} at line {exc.lineno}") from exc

    items = doc.get("items", []) if isinstance(doc, dict) else []
    out: list[BbtEntry] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        if item.get("itemType") in ("attachment", "note"):
            continue
        citekey = item.get("citationKey")
        if not citekey or not isinstance(citekey, str):
            continue
        out.append(
            BbtEntry(
                citekey=citekey,
                doi=_normalize_doi(item.get("DOI")),
                title=(item.get("title") or "").strip(),
                first_author_lastname=_first_author_lastname(item.get("creators")),
                year=_year_from_date(item.get("date")),
            )
        )
    return out


def _normalize_doi(raw: object) -> Optional[str]:
    """Strip URL prefix and lowercase. Returns None for empty/invalid input."""
    if not isinstance(raw, str):
        return None
    s = raw.strip().lower()
    if not s:
        return None
    # Drop common URL wrappers: https://doi.org/10.xxx
    s = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", s)
    s = s.lstrip("/")
    return s or None


def _first_author_lastname(creators: object) -> str:
    """Return ``lastName`` of the first ``creatorType == "author"`` entry."""
    if not isinstance(creators, list):
        return ""
    for c in creators:
        if not isinstance(c, dict):
            continue
        if c.get("creatorType") != "author":
            continue
        last = c.get("lastName")
        if isinstance(last, str) and last.strip():
            return last.strip()
    return ""


def _year_from_date(raw: object) -> Optional[int]:
    """Extract a 4-digit year from BBT ``date`` field.

    BBT dates are often ``"YYYY"``, ``"YYYY-MM-DD"``, or free-form
    (``"Spring 2023"``). Return the first 4-digit run we find.
    """
    if not isinstance(raw, str) or not raw:
        return None
    m = re.search(r"\b(\d{4})\b", raw)
    if not m:
        return None
    try:
        return int(m.group(1))
    except ValueError:
        return None
