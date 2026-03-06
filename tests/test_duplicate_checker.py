"""Tests for duplicate source detection (duplicate_checker.py)."""

from klemma.skills.duplicate_checker import (
    DuplicatePair,
    _find_author_year_title_duplicates,
    _find_doi_duplicates,
    _find_title_prefix_duplicates,
    _normalize,
    find_duplicates,
)


def _src(citekey, title="", authors="", year=None, doi=""):
    return {"id": citekey, "title": title, "authors": authors, "year": year, "doi": doi}


class TestNormalize:
    def test_lowercase_and_strip(self):
        assert _normalize("  Hello  World  ") == "hello world"

    def test_collapses_whitespace(self):
        assert _normalize("a   b\tc") == "a b c"

    def test_empty(self):
        assert _normalize("") == ""


class TestDoiDuplicates:
    def test_same_doi(self):
        sources = [
            _src("a", doi="10.1234/abc"),
            _src("b", doi="10.1234/ABC"),  # case-insensitive
        ]
        pairs = _find_doi_duplicates(sources)
        assert len(pairs) == 1
        assert pairs[0].confidence == 1.0
        assert pairs[0].strategy == "doi"

    def test_no_doi(self):
        sources = [_src("a"), _src("b")]
        assert _find_doi_duplicates(sources) == []

    def test_different_dois(self):
        sources = [
            _src("a", doi="10.1234/abc"),
            _src("b", doi="10.1234/def"),
        ]
        assert _find_doi_duplicates(sources) == []

    def test_empty_doi_ignored(self):
        sources = [
            _src("a", doi=""),
            _src("b", doi=""),
        ]
        assert _find_doi_duplicates(sources) == []

    def test_three_way_duplicate(self):
        sources = [
            _src("a", doi="10.1/x"),
            _src("b", doi="10.1/x"),
            _src("c", doi="10.1/x"),
        ]
        pairs = _find_doi_duplicates(sources)
        assert len(pairs) == 3  # a-b, a-c, b-c


class TestAuthorYearTitleDuplicates:
    def test_same_author_year_title(self):
        sources = [
            _src("smith2020a", title="Attention mechanisms in neural network architectures for NLP", authors="Smith, John", year=2020),
            _src("smith2020b", title="Attention mechanisms in neural network architectures for NLP: extended", authors="Smith, J.", year=2020),
        ]
        pairs = _find_author_year_title_duplicates(sources)
        assert len(pairs) == 1
        assert pairs[0].confidence == 0.9

    def test_different_year(self):
        sources = [
            _src("a", title="Same Title Here For Testing", authors="Smith, John", year=2020),
            _src("b", title="Same Title Here For Testing", authors="Smith, John", year=2021),
        ]
        assert _find_author_year_title_duplicates(sources) == []

    def test_different_author(self):
        sources = [
            _src("a", title="Same Title Here For Testing", authors="Smith, John", year=2020),
            _src("b", title="Same Title Here For Testing", authors="Jones, Bob", year=2020),
        ]
        assert _find_author_year_title_duplicates(sources) == []

    def test_missing_fields(self):
        sources = [
            _src("a", title="", authors="Smith", year=2020),
            _src("b", title="", authors="Smith", year=2020),
        ]
        assert _find_author_year_title_duplicates(sources) == []

    def test_and_separator(self):
        sources = [
            _src("a", title="Some Long Title For Matching Purpose", authors="Smith, J. and Jones, B.", year=2020),
            _src("b", title="Some Long Title For Matching Purpose", authors="Smith, John", year=2020),
        ]
        pairs = _find_author_year_title_duplicates(sources)
        assert len(pairs) == 1


class TestTitlePrefixDuplicates:
    def test_same_prefix(self):
        sources = [
            _src("a", title="A comprehensive survey of deep learning approaches in natural language processing"),
            _src("b", title="A comprehensive survey of deep learning approaches in NLP: extended version"),
        ]
        pairs = _find_title_prefix_duplicates(sources)
        assert len(pairs) == 1
        assert pairs[0].confidence == 0.7

    def test_short_title_skipped(self):
        sources = [
            _src("a", title="Short Title"),
            _src("b", title="Short Title"),
        ]
        assert _find_title_prefix_duplicates(sources) == []

    def test_case_insensitive(self):
        sources = [
            _src("a", title="The ROLE of embeddings in modern NLP systems and applications"),
            _src("b", title="the role of Embeddings in Modern NLP Systems and applications"),
        ]
        pairs = _find_title_prefix_duplicates(sources)
        assert len(pairs) == 1


class TestFindDuplicates:
    def test_empty(self):
        assert find_duplicates([]) == []

    def test_single_source(self):
        assert find_duplicates([_src("a")]) == []

    def test_no_duplicates(self):
        sources = [
            _src("a", title="Paper A on topic one research", authors="Smith", year=2020, doi="10.1/a"),
            _src("b", title="Paper B on topic two research", authors="Jones", year=2021, doi="10.1/b"),
        ]
        assert find_duplicates(sources) == []

    def test_dedup_across_strategies(self):
        """Same pair found by DOI and title — keep highest confidence."""
        sources = [
            _src("a", title="Attention is all you need for transformers", authors="Vaswani", year=2017, doi="10.1/att"),
            _src("b", title="Attention is all you need for transformers", authors="Vaswani", year=2017, doi="10.1/att"),
        ]
        pairs = find_duplicates(sources)
        # Should be 1 pair, not 3 (deduped)
        assert len(pairs) == 1
        assert pairs[0].confidence == 1.0  # DOI wins

    def test_sorted_by_confidence(self):
        sources = [
            _src("a", title="A long paper title about deep learning methods", authors="Smith", year=2020),
            _src("b", title="A long paper title about deep learning methods", authors="Smith", year=2020),
            _src("c", title="Another very interesting research paper title", doi="10.1/x"),
            _src("d", title="Completely different paper from another", doi="10.1/x"),
        ]
        pairs = find_duplicates(sources)
        assert len(pairs) == 2
        assert pairs[0].confidence >= pairs[1].confidence

    def test_returns_dataclass(self):
        sources = [
            _src("a", doi="10.1/same"),
            _src("b", doi="10.1/same"),
        ]
        pairs = find_duplicates(sources)
        assert len(pairs) == 1
        assert isinstance(pairs[0], DuplicatePair)
        assert pairs[0].citekey_a in ("a", "b")
        assert pairs[0].citekey_b in ("a", "b")

    def test_none_fields_handled(self):
        """Sources with None for title/authors/doi don't crash."""
        sources = [
            {"id": "a", "title": None, "authors": None, "year": None, "doi": None},
            {"id": "b", "title": None, "authors": None, "year": None, "doi": None},
        ]
        pairs = find_duplicates(sources)
        assert pairs == []
