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
