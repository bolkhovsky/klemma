"""Tests for orphan resolution logic — matching stale DB entries to BBT citekeys."""

from types import SimpleNamespace

import pytest

from klemma.cli import build_bbt_index, resolve_orphan


def _entry(item_key: str = "") -> SimpleNamespace:
    """Minimal BBT entry stub with item_key."""
    return SimpleNamespace(item_key=item_key)


@pytest.fixture
def bbt_lookup() -> dict:
    """Simulated BBT entry_lookup: {citekey: entry}."""
    return {
        "loS2ORCSemanticScholar2020": _entry("8PPS6TTJ"),
        "lewisRAG2021": _entry("BT4EX2TV"),
        "pautassoTenSimpleRules2013": _entry("EN582WFI"),
        "smithDeepLearning2022": _entry("ABCD1234"),
        "karandashevaZonirovanieBarencevaKarskogo2025": _entry("CAHXBS49"),
    }


@pytest.fixture
def bbt_index(bbt_lookup):
    return build_bbt_index(bbt_lookup)


class TestBuildBBTIndex:
    def test_item_key_lookup(self, bbt_lookup):
        by_item_key, _ = build_bbt_index(bbt_lookup)
        assert by_item_key["8PPS6TTJ"] == "loS2ORCSemanticScholar2020"
        assert by_item_key["CAHXBS49"] == "karandashevaZonirovanieBarencevaKarskogo2025"

    def test_author_year_lookup(self, bbt_lookup):
        _, by_author_year = build_bbt_index(bbt_lookup)
        assert ("lo", "2020") in by_author_year
        assert ("lewis", "2021") in by_author_year
        assert ("pautasso", "2013") in by_author_year
        assert ("smith", "2022") in by_author_year
        # Values are lists now
        assert len(by_author_year[("smith", "2022")]) == 1

    def test_entry_without_item_key(self):
        lookup = {"testEntry2020": _entry("")}
        by_item_key, by_author_year = build_bbt_index(lookup)
        assert len(by_item_key) == 0
        assert ("test", "2020") in by_author_year

    def test_author_year_collision_stores_both(self):
        """Multiple papers by same author+year are stored as list."""
        lookup = {
            "wangSelfConsistency2023": _entry("KEY1"),
            "wangSurveyLLM2023": _entry("KEY2"),
        }
        _, by_author_year = build_bbt_index(lookup)
        candidates = by_author_year[("wang", "2023")]
        assert len(candidates) == 2


class TestResolveOrphanBareKey:
    """Strategy 1: bare Zotero key (8-char alphanumeric) → item_key lookup."""

    def test_bare_key_matches(self, bbt_index):
        result = resolve_orphan("8PPS6TTJ", bbt_index)
        assert result == ("loS2ORCSemanticScholar2020", "8PPS6TTJ")

    def test_bare_key_no_match(self, bbt_index):
        result = resolve_orphan("ZZZZZZZZ", bbt_index)
        assert result is None

    def test_bare_key_wrong_length(self, bbt_index):
        result = resolve_orphan("8PPS6TT", bbt_index)  # 7 chars
        assert result is None

    def test_bare_key_lowercase_not_matched(self, bbt_index):
        result = resolve_orphan("8pps6ttj", bbt_index)  # lowercase
        assert result is None


class TestResolveOrphanAcquireFormat:
    """Strategy 2: acquire-format (Author2020_Title_Slug)."""

    def test_acquire_format_matches(self, bbt_index):
        result = resolve_orphan("Lo2020_S2ORC_The_Semantic_Scholar_Ope", bbt_index)
        assert result is not None
        assert result[0] == "loS2ORCSemanticScholar2020"

    def test_acquire_format_year_mismatch_no_match(self, bbt_index):
        # Lewis2020 ≠ lewisRAG2021 — different years, no fuzzy year matching
        result = resolve_orphan("Lewis2020_Retrieval-Augmented_Generation", bbt_index)
        assert result is None

    def test_acquire_format_smith(self, bbt_index):
        result = resolve_orphan("Smith2022_Deep_Learning_Review", bbt_index)
        assert result is not None
        assert result[0] == "smithDeepLearning2022"

    def test_acquire_format_pautasso(self, bbt_index):
        result = resolve_orphan("Pautasso2013_Ten_Simple_Rules", bbt_index)
        assert result is not None
        assert result[0] == "pautassoTenSimpleRules2013"

    def test_acquire_format_no_match(self, bbt_index):
        result = resolve_orphan("Unknown2099_Nonexistent_Paper", bbt_index)
        assert result is None


class TestResolveOrphanBBTFormat:
    """Strategy 3: BBT-format (authorTitle2022a)."""

    def test_bbt_suffix_matches(self, bbt_index):
        result = resolve_orphan("smithDeepLearning2022a", bbt_index)
        assert result is not None
        assert result[0] == "smithDeepLearning2022"

    def test_bbt_prefix_dot_removed(self, bbt_index):
        result = resolve_orphan("a.b.smithDeepLearning2022", bbt_index)
        assert result is not None
        assert result[0] == "smithDeepLearning2022"

    def test_bbt_no_match(self, bbt_index):
        result = resolve_orphan("nobodyNowhere2099", bbt_index)
        assert result is None

    def test_non_parseable_key(self, bbt_index):
        result = resolve_orphan("NLP_and_DA", bbt_index)
        assert result is None


class TestAuthorYearCollision:
    """Ambiguous author+year matches must be skipped to prevent wrong renames."""

    def test_ambiguous_acquire_format_skipped(self):
        """Two Wang2023 papers — acquire-format orphan should NOT match either."""
        lookup = {
            "wangSelfConsistency2023": _entry("KEY1"),
            "wangSurveyLLM2023": _entry("KEY2"),
        }
        bbt_index = build_bbt_index(lookup)
        result = resolve_orphan("Wang2023_A_Survey_on_Large_Language_Mod", bbt_index)
        assert result is None

    def test_ambiguous_bbt_format_skipped(self):
        """Two wang2023 papers — BBT-format orphan should NOT match either."""
        lookup = {
            "wangSelfConsistency2023": _entry("KEY1"),
            "wangSurveyLLM2023": _entry("KEY2"),
        }
        bbt_index = build_bbt_index(lookup)
        result = resolve_orphan("wangOldTitle2023a", bbt_index)
        assert result is None

    def test_unambiguous_still_works(self):
        """Single candidate for author+year — still matches correctly."""
        lookup = {
            "wangSelfConsistency2023": _entry("KEY1"),
            "smithDeepLearning2022": _entry("KEY2"),
        }
        bbt_index = build_bbt_index(lookup)
        result = resolve_orphan("Wang2023_Self_Consistency", bbt_index)
        assert result is not None
        assert result[0] == "wangSelfConsistency2023"

    def test_bbt_suffix_stripped_for_year(self):
        """BBT disambiguation suffix 'a' is stripped: 2023a → 2023."""
        lookup = {
            "smithDeepLearning2023": _entry("KEY1"),
        }
        bbt_index = build_bbt_index(lookup)
        result = resolve_orphan("smithOldTitle2023a", bbt_index)
        assert result is not None
        assert result[0] == "smithDeepLearning2023"

    def test_self_match_prevented(self):
        """An orphan should not resolve to itself."""
        lookup = {
            "smithDeepLearning2023": _entry("KEY1"),
        }
        bbt_index = build_bbt_index(lookup)
        # If this orphan IS the same citekey as in BBT, skip
        result = resolve_orphan("smithDeepLearning2023", bbt_index)
        assert result is None
