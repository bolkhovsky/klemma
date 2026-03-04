"""Source role classification — author publications vs external sources (ГОСТ Р 7.0.11-2011)."""

from enum import Enum


class SourceRole(str, Enum):
    """Classification of a source's role in the dissertation bibliography."""

    EXTERNAL = "external"
    AUTHOR_VAK = "author_vak"
    AUTHOR_SCOPUS = "author_scopus"
    AUTHOR_WOS = "author_wos"
    AUTHOR_CONF = "author_conf"
    AUTHOR_PATENT = "author_patent"
    AUTHOR_PROGRAM = "author_program"
    AUTHOR_OTHER = "author_other"

    @classmethod
    def author_roles(cls) -> list["SourceRole"]:
        """All roles that represent author's own publications."""
        return [r for r in cls if r != cls.EXTERNAL]

    @classmethod
    def values(cls) -> list[str]:
        return [r.value for r in cls]


# Human-readable labels for status display
ROLE_LABELS: dict[str, str] = {
    "author_vak": "ВАК",
    "author_scopus": "Scopus",
    "author_wos": "Web of Science",
    "author_conf": "Конференции",
    "author_patent": "Патенты",
    "author_program": "Свидетельства о программах для ЭВМ",
    "author_other": "Прочие",
}


def format_gost_phrase(counts: dict[str, int]) -> str:
    """Generate ГОСТ Р 7.0.11-2011 publication summary phrase.

    Example: 'Основные результаты изложены в 12 печатных изданиях,
    3 из которых в журналах ВАК, 2 — в Scopus, 1 — в тезисах докладов'
    """
    total = sum(counts.values())
    if total == 0:
        return ""

    parts = []
    if counts.get("author_vak", 0):
        parts.append(f"{counts['author_vak']} из которых в журналах ВАК")
    if counts.get("author_scopus", 0):
        parts.append(f"{counts['author_scopus']} — в Scopus")
    if counts.get("author_wos", 0):
        parts.append(f"{counts['author_wos']} — в Web of Science")
    if counts.get("author_conf", 0):
        parts.append(f"{counts['author_conf']} — в тезисах докладов")
    if counts.get("author_patent", 0):
        parts.append(f"{counts['author_patent']} — патентов")
    if counts.get("author_program", 0):
        parts.append(f"{counts['author_program']} — свидетельств о программах для ЭВМ")
    if counts.get("author_other", 0):
        parts.append(f"{counts['author_other']} — прочих публикаций")

    phrase = f"Основные результаты изложены в {total} печатных изданиях"
    if parts:
        phrase += ", " + ", ".join(parts)
    return phrase + "."
