"""Tests for Cyrillic → Latin transliteration used in citekey generation."""

from klemma.utils.translit import transliterate_ru


def test_cyrillic_surname_basic():
    assert transliterate_ru("Воронина") == "voronina"
    assert transliterate_ru("Иванов") == "ivanov"
    assert transliterate_ru("Смирнов") == "smirnov"


def test_cyrillic_with_hushers():
    assert transliterate_ru("Щедрин") == "shchedrin"
    assert transliterate_ru("Жуков") == "zhukov"
    assert transliterate_ru("Чехов") == "chekhov"
    assert transliterate_ru("Шишкин") == "shishkin"


def test_cyrillic_with_yo():
    assert transliterate_ru("Ёжиков") == "yozhikov"
    assert transliterate_ru("Фёдоров") == "fyodorov"


def test_cyrillic_soft_hard_signs_dropped():
    # ь and ъ collapse to nothing — they don't have phonetic Latin equivalent
    assert transliterate_ru("Подъезд") == "podezd"
    assert transliterate_ru("Льдов") == "ldov"


def test_latin_passthrough():
    # Non-Cyrillic ASCII is preserved (lowercased, but table doesn't map it
    # so the character falls through unchanged after lower())
    assert transliterate_ru("Andersson") == "andersson"
    assert transliterate_ru("SMITH") == "smith"
    assert transliterate_ru("O'Brien") == "o'brien"


def test_mixed_script():
    # Cyrillic letters convert, Latin letters stay
    assert transliterate_ru("Иван123") == "ivan123"


def test_empty_and_none_safe():
    assert transliterate_ru("") == ""
    assert transliterate_ru(None) == ""  # type: ignore[arg-type]
