"""Cyrillic → Latin transliteration for citekey generation.

Simplified phonetic table (BBT-compatible lowercase output). Not strict
ГОСТ 7.79 — we drop diacritics (`c` instead of `c'`) and use `yo` for ё
to stay inside the ASCII word-class so downstream citekey slugify rules
don't need to be Unicode-aware.
"""

from __future__ import annotations

_RU_TO_LAT: dict[str, str] = {
    "а": "a",  "б": "b",  "в": "v",  "г": "g",  "д": "d",
    "е": "e",  "ё": "yo", "ж": "zh", "з": "z",  "и": "i",
    "й": "y",  "к": "k",  "л": "l",  "м": "m",  "н": "n",
    "о": "o",  "п": "p",  "р": "r",  "с": "s",  "т": "t",
    "у": "u",  "ф": "f",  "х": "kh", "ц": "ts", "ч": "ch",
    "ш": "sh", "щ": "shch","ъ": "",  "ы": "y",  "ь": "",
    "э": "e",  "ю": "yu", "я": "ya",
}


def transliterate_ru(text: str) -> str:
    """Lowercase Cyrillic → Latin. Non-Cyrillic chars pass through unchanged.

    Intended for citekey generation (author surnames, short labels). Examples:
        "Воронина" → "voronina"
        "Щедрин"   → "shchedrin"
        "Ёжиков"   → "yozhikov"
        "Andersson"→ "andersson"
    """
    if not text:
        return ""
    out: list[str] = []
    for ch in text:
        lower = ch.lower()
        if lower in _RU_TO_LAT:
            out.append(_RU_TO_LAT[lower])
        else:
            out.append(lower)
    return "".join(out)
