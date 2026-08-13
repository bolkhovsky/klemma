"""Tests for numbered-reference parsing and matching (claim-provenance PR-3)."""
from __future__ import annotations

from klemma.literature.draft_parser import find_bibliography_section
from klemma.literature.reference_parser import parse_numbered_references
from klemma.skills.reference_matcher import (
    RefMap,
    _normalize_doi,
    _surnames,
    build_ref_map,
    collect_sources_meta,
)

# ---------------------------------------------------------------------------
# parse_numbered_references
# ---------------------------------------------------------------------------

BIB_BRACKETS = """[1] Smith J., Jones K. Sea ice concentration retrieval from passive microwave. Remote Sensing. 2020;12:100-115.
[2] Иванов И. И. Оценка точности ледовых прогнозов // Метеорология и гидрология. — 2019. — № 4. — С. 55–63.
[3] Lee H. Deep learning for sea ice forecasting. https://doi.org/10.1234/test.2021
"""

BIB_DOTS = """1. Palerme C., Röhrs J. MET-AICE v1.0: an operational sea ice prediction system // GMD. — 2025. — Vol. 18. — P. 9751–9766.
2. Ran J., Zhang W. Research progress of deep learning in sea ice prediction // Remote Sensing. — 2026. — Vol. 18. — Art. 419.
"""

BIB_PARENS = """1) Smith J. First numbered entry with enough length to parse. Journal A. 2020.
2) Jones K. Second numbered entry with enough length to parse. Journal B. 2021.
"""


def test_parse_numbered_brackets_preserves_numbers():
    refs = parse_numbered_references(BIB_BRACKETS)
    assert [n for n, _ in refs] == [1, 2, 3]


def test_parse_numbered_dots_preserves_numbers():
    refs = parse_numbered_references(BIB_DOTS)
    assert [n for n, _ in refs] == [1, 2]


def test_parse_numbered_parens_preserves_numbers():
    refs = parse_numbered_references(BIB_PARENS)
    assert [n for n, _ in refs] == [1, 2]


def test_parse_numbered_extracts_fields():
    refs = dict(parse_numbered_references(BIB_BRACKETS))
    assert refs[3].doi == "10.1234/test.2021"
    assert refs[1].year == 2020
    assert "Smith" in refs[1].authors or "Smith" in refs[1].raw


def test_parse_numbered_skips_short_entries():
    text = "[1] too short\n[2] A real reference entry that is long enough to count. Journal. 2020.\n"
    refs = parse_numbered_references(text)
    assert [n for n, _ in refs] == [2]


def test_parse_numbered_nonconsecutive_numbers_kept():
    text = "[3] Third entry with enough text to be a real reference. 2020.\n[7] Seventh entry with enough text to be a real reference. 2021.\n"
    refs = parse_numbered_references(text)
    assert [n for n, _ in refs] == [3, 7]


def test_parse_numbered_wrapped_year_line_not_split():
    # A wrapped line starting with a 4-digit year must not start a new entry
    text = "1. Author A. Long title of the first reference entry here //\n2011. — Vol. 5. — P. 1–10.\n2. Author B. Second reference entry long enough to parse. 2020.\n"
    refs = parse_numbered_references(text)
    assert [n for n, _ in refs] == [1, 2]
    assert "2011" in refs[0][1].raw


def test_parse_numbered_empty_text():
    assert parse_numbered_references("") == []


# ---------------------------------------------------------------------------
# find_bibliography_section
# ---------------------------------------------------------------------------

def test_find_bib_markdown_heading():
    text = "# Title\n\nBody text here.\n\n## Список литературы\n\n1. Entry one.\n2. Entry two.\n"
    span = find_bibliography_section(text)
    assert span is not None
    assert "1. Entry one." in text[span[0]:span[1]]
    assert "Body text" not in text[span[0]:span[1]]


def test_find_bib_plain_marker():
    text = "Some body.\nReferences\n[1] Entry one.\n"
    span = find_bibliography_section(text)
    assert span is not None
    assert "[1] Entry one." in text[span[0]:span[1]]


def test_find_bib_stops_at_next_heading():
    text = (
        "## Список литературы\n\n1. Entry one.\n\n"
        "## Сведения об авторах\n\nAuthor bio here.\n"
    )
    span = find_bibliography_section(text)
    assert span is not None
    section = text[span[0]:span[1]]
    assert "Entry one." in section
    assert "Author bio" not in section


def test_find_bib_none_when_absent():
    assert find_bibliography_section("# Title\n\nNo bibliography here.\n") is None


def test_find_bib_english_heading():
    text = "body\n\n# References\n\n[1] Smith J. Title. 2020.\n"
    span = find_bibliography_section(text)
    assert span is not None
    assert "[1] Smith" in text[span[0]:span[1]]


# ---------------------------------------------------------------------------
# build_ref_map — matching
# ---------------------------------------------------------------------------

_SOURCES_META = [
    {
        "citekey": "smith2020seaice",
        "title": "Sea ice concentration retrieval from passive microwave observations",
        "authors": "Smith, John; Jones, Kate",
        "year": 2020,
        "doi": "10.5555/smith.2020",
    },
    {
        "citekey": "lee2021deep",
        "title": "Deep learning for sea ice forecasting",
        "authors": "Lee, Hana",
        "year": 2021,
        "doi": "10.1234/test.2021",
    },
    {
        "citekey": "ivanov2019",
        "title": "Оценка точности ледовых прогнозов",
        "authors": "Иванов И. И.",
        "year": 2019,
        "doi": "",
    },
]


def _md_with_bib(bib_entries: str) -> str:
    return f"# Paper\n\nBody sentence with [1].\n\n## Список литературы\n\n{bib_entries}"


def test_ref_map_doi_match():
    md = _md_with_bib(
        "1. Lee H. Some retitled version of the paper. 2021. https://doi.org/10.1234/test.2021\n"
    )
    ref_map = build_ref_map(md, _SOURCES_META)
    assert ref_map.number_to_citekey == {1: "lee2021deep"}
    assert ref_map.match(1).method == "doi"
    assert ref_map.confidence(1) == 1.0


def test_ref_map_title_match():
    md = _md_with_bib(
        "1. Smith, J., & Jones, K. (2020). Sea ice concentration retrieval from passive microwave observations. Remote Sensing, 12(3), 100-115.\n"
    )
    ref_map = build_ref_map(md, _SOURCES_META)
    assert ref_map.number_to_citekey == {1: "smith2020seaice"}
    assert ref_map.match(1).method == "title"
    assert 0 < ref_map.confidence(1) < 1.0


def test_ref_map_title_match_rejected_on_year_mismatch():
    # Same title but a different year → title stage must not match
    md = _md_with_bib(
        "1. Smith J. Sea ice concentration retrieval from passive microwave observations. 1999.\n"
    )
    meta = [dict(_SOURCES_META[0], authors="Другой Автор")]
    ref_map = build_ref_map(md, meta)
    assert 1 in ref_map.unmatched


def test_ref_map_authors_year_match():
    md = _md_with_bib(
        "1. Иванов И. И. Совсем другое название работы // Метеорология. — 2019. — № 4.\n"
    )
    ref_map = build_ref_map(md, _SOURCES_META)
    assert ref_map.number_to_citekey == {1: "ivanov2019"}
    assert ref_map.match(1).method == "authors_year"


def test_ref_map_title_containment_match():
    # "Author A. B."-style entry breaks heuristic title extraction, but the
    # raw entry still contains the source title verbatim → containment match
    md = _md_with_bib(
        "1. Lee H. Q. Deep learning for sea ice forecasting // Remote Sensing. — 2021. — Vol. 9.\n"
    )
    meta = [dict(_SOURCES_META[1], doi="")]
    ref_map = build_ref_map(md, meta)
    assert ref_map.number_to_citekey == {1: "lee2021deep"}
    assert ref_map.match(1).method == "title"
    assert ref_map.confidence(1) == 0.75


def test_ref_map_two_letter_surname_match():
    # 2-letter surnames ("Ma") must survive the surname filter
    md = _md_with_bib(
        "1. Ma L., Qian S. Совсем другое название про навигацию // JMSE. — 2025. — Vol. 13.\n"
    )
    meta = [{
        "citekey": "ma2025vessel",
        "title": "Comparative Research on Vessel Navigability",
        "authors": "Long Ma, Sihan Qian, Xiaoguang Mou et al.",
        "year": 2025,
        "doi": "",
    }]
    ref_map = build_ref_map(md, meta)
    assert ref_map.number_to_citekey == {1: "ma2025vessel"}
    assert ref_map.match(1).method == "authors_year"


def test_ref_map_unmatched():
    md = _md_with_bib(
        "1. Unknown A. Completely unrelated work about volcanoes. 1985.\n"
    )
    ref_map = build_ref_map(md, _SOURCES_META)
    assert ref_map.number_to_citekey == {}
    assert 1 in ref_map.unmatched
    assert ref_map.match(1) is None
    assert ref_map.confidence(1) == 0.0


def test_ref_map_empty_sources_meta_all_unmatched():
    md = _md_with_bib(
        "1. Smith J. Sea ice concentration retrieval from passive microwave observations. 2020.\n"
    )
    ref_map = build_ref_map(md, [])
    assert ref_map.number_to_citekey == {}
    assert 1 in ref_map.unmatched


def test_ref_map_no_bibliography_returns_empty():
    ref_map = build_ref_map("# Title\n\nJust body text with [1].\n", _SOURCES_META)
    assert ref_map.number_to_citekey == {}
    assert ref_map.unmatched == {}


def test_ref_map_doi_priority_over_title():
    # Entry whose title matches source A but DOI matches source B → DOI wins
    md = _md_with_bib(
        "1. Smith J. Sea ice concentration retrieval from passive microwave observations. 2020. doi:10.1234/test.2021\n"
    )
    ref_map = build_ref_map(md, _SOURCES_META)
    assert ref_map.number_to_citekey == {1: "lee2021deep"}
    assert ref_map.match(1).method == "doi"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def test_normalize_doi_strips_prefixes():
    assert _normalize_doi("https://doi.org/10.1/A.B") == "10.1/a.b"
    assert _normalize_doi("doi:10.1/a.b") == "10.1/a.b"
    assert _normalize_doi("10.1/a.b.") == "10.1/a.b"
    assert _normalize_doi(None) == ""


def test_surnames_skips_initials():
    s = _surnames("Иванов И. И., Петров П.")
    assert s == {"иванов", "петров"}


def test_surnames_english_and_stopwords():
    s = _surnames("Smith J. and Jones K. et al.")
    assert "smith" in s and "jones" in s
    assert "and" not in s


def test_collect_sources_meta_from_state():
    class FakeState:
        def get_all_sources_metadata(self):
            return [
                {"id": "ck1", "title": "T1", "authors": "A1", "year": 2020, "doi": "10.1/x"},
                {"id": "ck1", "title": "dup", "authors": "", "year": None, "doi": ""},
                {"id": "", "title": "no key", "authors": "", "year": None, "doi": ""},
            ]

    meta = collect_sources_meta(state=FakeState())
    assert len(meta) == 1
    assert meta[0]["citekey"] == "ck1"
    assert meta[0]["doi"] == "10.1/x"


def test_collect_sources_meta_state_failure_is_silent():
    class BrokenState:
        def get_all_sources_metadata(self):
            raise RuntimeError("db locked")

    assert collect_sources_meta(state=BrokenState()) == []


def test_collect_sources_meta_user_library_adds_new_citekeys():
    class FakeState:
        def get_all_sources_metadata(self):
            return [{"id": "ck1", "title": "T1", "authors": "", "year": None, "doi": ""}]

    class FakeSource:
        def __init__(self, citekey, paper_id):
            self.citekey = citekey
            self.paper_id = paper_id

    class FakePaper:
        title = "Lib title"
        authors = "Lib Author"
        year = 2022
        doi = "10.9/lib"

    class FakeLibrary:
        def get_all_sources(self):
            return [FakeSource("ck1", "p1"), FakeSource("ck2", "p2")]

    class FakePaperStore:
        def get_paper_by_id(self, paper_id):
            return FakePaper() if paper_id == "p2" else None

    meta = collect_sources_meta(
        state=FakeState(), paper_store=FakePaperStore(), user_library=FakeLibrary(),
    )
    citekeys = [m["citekey"] for m in meta]
    assert citekeys == ["ck1", "ck2"]
    assert meta[1]["doi"] == "10.9/lib"


def test_ref_map_dataclass_defaults():
    ref_map = RefMap()
    assert ref_map.number_to_citekey == {}
    assert ref_map.unmatched == {}
    assert ref_map.match(5) is None
    assert ref_map.confidence(5) == 0.0
