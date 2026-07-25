"""Cyrillic → Latin transliteration + Latin diacritic stripping.

Produces BBT-compatible lowercase ASCII output for citekey generation.
Covers:
    - Russian Cyrillic (phonetic table, 33 letters)
    - Western/Eastern European Latin with diacritics (NFKD + mark strip)
    - A few ligatures that don't decompose (ß→ss, æ→ae, œ→oe, ø→o, ð→d, þ→th, ł→l)

Non-goals: full ICU transliteration, non-Russian Cyrillic (Serbian ћ,
Ukrainian і etc.). Those letters fall through and are stripped downstream
by the ``[a-z0-9]`` slug filter.
"""

from __future__ import annotations

import unicodedata

_RU_TO_LAT: dict[str, str] = {
    "а": "a",  "б": "b",  "в": "v",  "г": "g",  "д": "d",
    "е": "e",  "ё": "yo", "ж": "zh", "з": "z",  "и": "i",
    "й": "y",  "к": "k",  "л": "l",  "м": "m",  "н": "n",
    "о": "o",  "п": "p",  "р": "r",  "с": "s",  "т": "t",
    "у": "u",  "ф": "f",  "х": "kh", "ц": "ts", "ч": "ch",
    "ш": "sh", "щ": "shch","ъ": "",  "ы": "y",  "ь": "",
    "э": "e",  "ю": "yu", "я": "ya",
}

# Letters that don't decompose cleanly under NFKD but need explicit mapping
# for BBT-compatible output (BBT's `auth` formatter emits these Latin-ASCII
# equivalents).
_LATIN_LIGATURES: dict[str, str] = {
    "ß": "ss",
    "æ": "ae",
    "œ": "oe",
    "ø": "o",
    "ð": "d",
    "þ": "th",
    "ħ": "h",
    "ł": "l",
    "đ": "d",
}


def transliterate_ru(text: str) -> str:
    """Normalize a surname/label to lowercase Latin ASCII.

    Order of operations:
        1. Lowercase.
        2. Replace Russian Cyrillic via phonetic table (``Воронина → voronina``,
           ``Щедрин → shchedrin``).
        3. Replace Latin ligatures that don't decompose (``Straße → strasse``,
           ``Łukasiewicz → lukasiewicz``).
        4. NFKD-normalize and strip combining marks so Latin diacritics
           collapse to plain ASCII (``é → e``, ``ü → u``, ``ñ → n``).

    Non-Russian-Cyrillic letters pass through unchanged; the caller's
    ``[a-z0-9]`` slug filter drops them.

    Examples::

        "Воронина"     → "voronina"
        "Щедрин"       → "shchedrin"
        "Müller"       → "muller"
        "Straße"       → "strasse"
        "Łukasiewicz"  → "lukasiewicz"
        "Andersson"    → "andersson"
    """
    if not text:
        return ""
    lower = text.lower()
    out: list[str] = []
    for ch in lower:
        if ch in _RU_TO_LAT:
            out.append(_RU_TO_LAT[ch])
        elif ch in _LATIN_LIGATURES:
            out.append(_LATIN_LIGATURES[ch])
        else:
            out.append(ch)
    decomposed = unicodedata.normalize("NFKD", "".join(out))
    return "".join(c for c in decomposed if not unicodedata.combining(c))
