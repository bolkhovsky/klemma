"""Unit tests for src/klemma/api/routes/drafts.py.

Tests cover the pure Markdown-processing helpers:
  parse_headings, extract_section, upsert_section, _heading_to_filename, _split_dissertation
"""


from klemma.api.routes.drafts import (
    _heading_to_filename,
    _split_dissertation,
    extract_section,
    parse_headings,
    upsert_section,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SAMPLE_DOC = """\
# Диссертация

## 1 Введение

Вводный текст.

## 2 Методология

### 2.1 Описание данных

Данные получены из открытых источников.

### 2.2 Модель

Применяется линейная регрессия.

## 3 Результаты

Результаты показывают улучшение.
"""


# ---------------------------------------------------------------------------
# parse_headings
# ---------------------------------------------------------------------------


def test_parse_headings_returns_all_headings():
    headings = parse_headings(SAMPLE_DOC)
    section_ids = [h["section_id"] for h in headings]
    assert "1" in section_ids
    assert "2" in section_ids
    assert "2.1" in section_ids
    assert "2.2" in section_ids
    assert "3" in section_ids


def test_parse_headings_levels():
    headings = parse_headings(SAMPLE_DOC)
    by_id = {h["section_id"]: h for h in headings}
    assert by_id["1"]["level"] == 2
    assert by_id["2.1"]["level"] == 3


def test_parse_headings_titles():
    headings = parse_headings(SAMPLE_DOC)
    by_id = {h["section_id"]: h for h in headings}
    assert by_id["1"]["title"] == "Введение"
    assert by_id["2.1"]["title"] == "Описание данных"


def test_parse_headings_empty_doc():
    assert parse_headings("") == []


def test_parse_headings_no_numeric_sections():
    """A doc with only an h1 title and prose has no numeric section headings
    but parse_headings still returns the h1 as a non-numeric heading."""
    doc = "# Just a title\n\nSome prose."
    headings = parse_headings(doc)
    # h1 is returned with slug section_id
    assert len(headings) == 1
    assert headings[0]["section_id"] == "just-a-title"


def test_parse_headings_non_numeric_headings():
    """Non-numeric headings get slug section_ids, don't crash."""
    doc = "## Introduction\n\nSome text."
    headings = parse_headings(doc)
    assert len(headings) == 1
    assert headings[0]["section_id"] == "introduction"


# ---------------------------------------------------------------------------
# extract_section
# ---------------------------------------------------------------------------


def test_extract_section_found():
    result = extract_section(SAMPLE_DOC, "2.1")
    assert result is not None
    body, start, end = result
    assert "открытых источников" in body


def test_extract_section_not_found():
    assert extract_section(SAMPLE_DOC, "99") is None


def test_extract_section_stops_at_same_level():
    """Section 2.1 should not bleed into 2.2."""
    result = extract_section(SAMPLE_DOC, "2.1")
    assert result is not None
    body, _, _ = result
    assert "линейная регрессия" not in body


def test_extract_section_stops_at_higher_level():
    """Section 2 (##) body should not include section 3 content."""
    result = extract_section(SAMPLE_DOC, "2")
    assert result is not None
    body, _, _ = result
    assert "Результаты показывают" not in body


def test_extract_section_last_section_reaches_eof():
    result = extract_section(SAMPLE_DOC, "3")
    assert result is not None
    body, _, end = result
    assert "Результаты показывают" in body
    # end should equal total line count
    assert end == len(SAMPLE_DOC.splitlines())


# ---------------------------------------------------------------------------
# upsert_section
# ---------------------------------------------------------------------------


def test_upsert_section_replaces_body():
    updated = upsert_section(SAMPLE_DOC, "1", "Новый вводный текст.")
    result = extract_section(updated, "1")
    assert result is not None
    body, _, _ = result
    assert "Новый вводный текст." in body
    # Original text gone
    assert "Вводный текст." not in body


def test_upsert_section_preserves_other_sections():
    updated = upsert_section(SAMPLE_DOC, "1", "Replaced.")
    # Other sections still intact
    result_2_1 = extract_section(updated, "2.1")
    assert result_2_1 is not None
    assert "открытых источников" in result_2_1[0]


def test_upsert_section_appends_new_section():
    updated = upsert_section(SAMPLE_DOC, "4", "Заключение.", heading_title="Заключение")
    assert "## 4 Заключение" in updated
    assert "Заключение." in updated


def test_upsert_section_appended_section_is_extractable():
    updated = upsert_section(SAMPLE_DOC, "4", "Финальный текст.", heading_title="Выводы")
    result = extract_section(updated, "4")
    assert result is not None
    body, _, _ = result
    assert "Финальный текст." in body


def test_upsert_section_idempotent_on_same_content():
    updated = upsert_section(SAMPLE_DOC, "3", "Результаты показывают улучшение.")
    # Applying same content should yield same structure
    result = extract_section(updated, "3")
    assert result is not None
    assert "Результаты показывают улучшение." in result[0]


def test_upsert_section_empty_body():
    """Replacing with empty body should not corrupt document."""
    updated = upsert_section(SAMPLE_DOC, "1", "")
    assert "## 1 Введение" in updated
    # Other sections still intact
    assert extract_section(updated, "2") is not None


# ---------------------------------------------------------------------------
# _heading_to_filename
# ---------------------------------------------------------------------------


def test_heading_to_filename_intro_ru():
    assert _heading_to_filename("Введение") == "intro.md"


def test_heading_to_filename_intro_en():
    assert _heading_to_filename("Introduction") == "intro.md"


def test_heading_to_filename_conclusion_ru():
    assert _heading_to_filename("Заключение") == "conclusion.md"


def test_heading_to_filename_conclusion_vyody():
    assert _heading_to_filename("Выводы") == "conclusion.md"


def test_heading_to_filename_numeric_prefix():
    assert _heading_to_filename("1 Методология") == "chapter_1.md"
    assert _heading_to_filename("3. Результаты") == "chapter_3.md"


def test_heading_to_filename_glava_pattern():
    assert _heading_to_filename("Глава 2. Анализ") == "chapter_2.md"


def test_heading_to_filename_chapter_en():
    assert _heading_to_filename("Chapter 4 Discussion") == "chapter_4.md"


def test_heading_to_filename_slug_fallback():
    name = _heading_to_filename("Some Unusual Title")
    assert name.endswith(".md")
    assert " " not in name


# ---------------------------------------------------------------------------
# _split_dissertation
# ---------------------------------------------------------------------------

DISSERTATION_DOC = """\
## Введение

Вводный текст диссертации.

## 1 Методология

Описание методов.

## 2 Результаты

Результаты исследования.

## Заключение

Итоги работы.
"""


def test_split_dissertation_returns_chunks():
    chunks = _split_dissertation(DISSERTATION_DOC)
    assert len(chunks) == 4


def test_split_dissertation_canonical_filenames():
    chunks = _split_dissertation(DISSERTATION_DOC)
    filenames = [c[0] for c in chunks]
    assert "intro.md" in filenames
    assert "chapter_1.md" in filenames
    assert "chapter_2.md" in filenames
    assert "conclusion.md" in filenames


def test_split_dissertation_content_preserved():
    chunks = _split_dissertation(DISSERTATION_DOC)
    by_name = dict(chunks)
    assert "Вводный текст диссертации." in by_name["intro.md"]
    assert "Описание методов." in by_name["chapter_1.md"]


def test_split_dissertation_no_headings_returns_empty():
    assert _split_dissertation("Just plain text without headings.") == []


def test_split_dissertation_each_chunk_includes_heading():
    chunks = _split_dissertation(DISSERTATION_DOC)
    for _, content in chunks:
        assert content.startswith("##")
