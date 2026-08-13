"""Text normalization helpers for PDF-extracted text.

Shared by fragment verbatim validation and (future) raw-text fragment search.
Both sides normalize identically so comparisons are meaningful despite the
noise PDF extractors introduce (ligatures, soft hyphens, line-break
hyphenation, smart quotes).
"""

from __future__ import annotations

import re
import unicodedata

# Line-break hyphenation: "word-\nword" -> "wordword". Handles Windows and mac
# line endings. Must run before whitespace collapse.
_LINE_BREAK_HYPHEN = re.compile(r"-[\r\n]+")

# Runs of whitespace (including the newlines left over after hyphen rejoin)
# collapse to a single space.
_WHITESPACE_RUN = re.compile(r"\s+")

# Soft hyphen: Unicode U+00AD, invisible hint character PDFs sprinkle
# inside words at line-break points.
_SOFT_HYPHEN = "\u00ad"


def normalize(text: str) -> str:
    """Canonicalize text for substring/similarity comparison.

    Applies:
      1. NFKC Unicode normalization — decomposes ligatures (fi, ffi, fl, ff),
         normalizes compatibility variants, unifies smart-quote/dash forms.
      2. Strips soft hyphens (U+00AD).
      3. Rejoins line-break hyphenation ("word-\\nword" -> "wordword").
      4. Collapses whitespace runs to a single space and strips ends.

    Idempotent: ``normalize(normalize(x)) == normalize(x)``.
    """
    if not text:
        return ""
    text = unicodedata.normalize("NFKC", text)
    text = text.replace(_SOFT_HYPHEN, "")
    text = _LINE_BREAK_HYPHEN.sub("", text)
    text = _WHITESPACE_RUN.sub(" ", text)
    return text.strip()


def normalize_with_map(text: str) -> tuple[str, list[int]]:
    """``normalize()`` plus a normalized→raw index map.

    Returns ``(normalized, idx_map)`` where ``idx_map[k]`` is the raw-text
    index of the character that produced ``normalized[k]``. Multi-char NFKC
    expansions (ligature "ﬁ" → "fi") map every output char to the raw char's
    index; a collapsed whitespace run maps its single space to the run's
    first raw whitespace char. This lets a difflib/substring match found in
    normalized space be translated back into raw sidecar coordinates.

    NFKC is applied per combining sequence (base char + trailing combining
    marks) rather than per character, so decomposed input ("и" + U+0306)
    still composes exactly as the whole-string ``normalize()`` does.
    """
    if not text:
        return "", []

    # Step 1: NFKC per combining sequence, tagging each output char with the
    # raw index of the sequence it came from.
    pairs: list[tuple[str, int]] = []  # (normalized_char, raw_index)
    i = 0
    n = len(text)
    while i < n:
        j = i + 1
        while j < n and unicodedata.combining(text[j]):
            j += 1
        for ch in unicodedata.normalize("NFKC", text[i:j]):
            pairs.append((ch, i))
        i = j

    # Step 2: strip soft hyphens.
    pairs = [(ch, idx) for ch, idx in pairs if ch != _SOFT_HYPHEN]

    # Step 3: rejoin line-break hyphenation ("-" followed by \r/\n run).
    joined: list[tuple[str, int]] = []
    k = 0
    m = len(pairs)
    while k < m:
        ch, idx = pairs[k]
        if ch == "-" and k + 1 < m and pairs[k + 1][0] in "\r\n":
            k += 1
            while k < m and pairs[k][0] in "\r\n":
                k += 1
            continue
        joined.append((ch, idx))
        k += 1

    # Step 4: collapse whitespace runs to one space; drop runs at the ends
    # (equivalent to the trailing .strip() in normalize()).
    out_chars: list[str] = []
    idx_map: list[int] = []
    pending_ws: int | None = None  # raw index of the current run's first ws char
    for ch, idx in joined:
        if _WHITESPACE_RUN.match(ch):
            if pending_ws is None:
                pending_ws = idx
            continue
        if pending_ws is not None and out_chars:
            out_chars.append(" ")
            idx_map.append(pending_ws)
        pending_ws = None
        out_chars.append(ch)
        idx_map.append(idx)

    return "".join(out_chars), idx_map
