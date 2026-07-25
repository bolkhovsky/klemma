"""Tests for `_citekey_from_filename` — BBT-style citekey generation."""

from klemma.api.routes.library import _citekey_from_filename

# ---------------------------------------------------------------------------
# Zotero "Author - Year - Title" pattern
# ---------------------------------------------------------------------------


def test_zotero_pattern_cyrillic():
    """The main bug from prod: Russian PDF was generating `воронина2023_...`."""
    result = _citekey_from_filename(
        "Воронина - 2023 - Основные направления устойчивого функционирования.pdf"
    )
    assert result == "voronina2023"


def test_zotero_pattern_latin():
    assert _citekey_from_filename(
        "Andersson et al. - 2021 - Seasonal Arctic sea ice.pdf"
    ) == "andersson2021"


def test_zotero_pattern_multiple_authors_cyrillic():
    """Only first author is used for citekey."""
    assert _citekey_from_filename(
        "Смирнов, Иванов - 2021 - Ледовый прогноз.pdf"
    ) == "smirnov2021"


def test_zotero_pattern_em_dash():
    # Some exports use — instead of -
    assert _citekey_from_filename(
        "Smith — 2020 — Machine Learning.pdf"
    ) == "smith2020"


# ---------------------------------------------------------------------------
# Underscore-separated pattern (Zotero, Mendeley variants)
# ---------------------------------------------------------------------------


def test_underscore_pattern_latin():
    assert _citekey_from_filename("Smith_2020_Machine_Learning.pdf") == "smith2020"


def test_underscore_pattern_cyrillic():
    assert _citekey_from_filename("Иванов_2019_Статья.pdf") == "ivanov2019"


def test_space_separated_cyrillic():
    assert _citekey_from_filename("Иванов 2019.pdf") == "ivanov2019"


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_no_title_slug_anywhere():
    """Regression: the old generator produced
    `воронина2023_основные_направления_устоичиво`. The new one must NEVER emit
    a title slug — downstream formats rely on a short stable prefix.
    """
    bad = _citekey_from_filename("Воронина - 2023 - Основные направления устойчивого функционирования.pdf")
    assert "_" not in bad
    # And no Cyrillic characters should leak into the output
    assert all(c.isascii() for c in bad)


def test_author_only_no_year():
    # Without a year we can't produce a good key, but we still strip to Latin
    assert _citekey_from_filename("Воронина.pdf") == "voronina"


def test_year_only():
    assert _citekey_from_filename("2023.pdf") == "paper2023"


def test_empty_name():
    # Unlikely in practice but must not crash
    assert _citekey_from_filename(".pdf") == "unknown"


def test_special_chars_stripped():
    assert _citekey_from_filename("O'Brien - 2020 - Title.pdf") == "obrien2020"


def test_latin_diacritics_normalized():
    """Latin with accents → plain ASCII (BBT-compatible). Regression: the
    previous version stripped diacritics instead of normalizing, producing
    `mller2018` and similar.
    """
    assert _citekey_from_filename("Müller - 2018 - Paper.pdf") == "muller2018"
    assert _citekey_from_filename("Łukasiewicz - 2019 - X.pdf") == "lukasiewicz2019"
    assert _citekey_from_filename("Jiménez - 2022 - Y.pdf") == "jimenez2022"
    assert _citekey_from_filename("Straße - 2020 - Z.pdf") == "strasse2020"
