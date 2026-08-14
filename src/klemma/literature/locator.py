"""Human-readable source locators derived from sidecar text.

Pure string heuristics for normative/structured documents (ГОСТ, РД,
standards): given a character offset into the sidecar canonical text,
find the nearest preceding structural marker — a numbered clause
(«п. 3.4»), a table caption («табл. 2») or an appendix heading
(«Приложение А») — and render it as the short citation locator used in
references like «[5, п. 3.4]».

Advisory-only: the heuristic can misfire on free-form prose (a line that
happens to start with "2.5 mm"), so locators must never gate a verdict —
they only make a confirmed span cheaper to check by hand.
"""

from __future__ import annotations

import re

# Numbered clause: line starts with a multi-level number ("3.4", "7.1.3.8").
# A single bare number is NOT a clause (too many false positives: list items,
# years, page numbers) — at least one dotted level is required.
_CLAUSE_RE = re.compile(r"^\s*(\d+(?:\.\d+)+)\b")

# Table caption: "Таблица 2", "Таблица А.1" (ГОСТ appendix tables).
_TABLE_RE = re.compile(r"^\s*Таблица\s+([А-ЯA-Z]?\.?\d+(?:\.\d+)*)\b", re.IGNORECASE)

# Appendix heading: "Приложение А" (single-letter designator per ГОСТ 1.5).
_APPENDIX_RE = re.compile(r"^\s*Приложение\s+([А-ЯA-Z])\b", re.IGNORECASE)


def derive_locator(
    text: str,
    span_start: int,
    page: int | None = None,
) -> str | None:
    """Derive a human-readable locator for a span inside ``text``.

    Scans lines from the one containing ``span_start`` upwards and returns
    the nearest structural marker: «п. X.Y» for numbered clauses,
    «табл. N» for table captions, «Приложение X» for appendix headings.
    Falls back to «с. {page}» when no marker precedes the span, and to
    ``None`` when the page is unknown too.
    """
    if not text:
        return f"с. {page}" if page is not None else None

    span_start = max(0, min(span_start, len(text)))
    # Extend to the end of the current line so a span that starts mid-line
    # still sees its own line's marker ("3.4 Определение ..." matched from
    # inside the clause text).
    line_end = text.find("\n", span_start)
    if line_end == -1:
        line_end = len(text)

    for line in reversed(text[:line_end].splitlines()):
        m = _CLAUSE_RE.match(line)
        if m:
            return f"п. {m.group(1)}"
        m = _TABLE_RE.match(line)
        if m:
            return f"табл. {m.group(1)}"
        m = _APPENDIX_RE.match(line)
        if m:
            return f"Приложение {m.group(1).upper()}"

    return f"с. {page}" if page is not None else None
