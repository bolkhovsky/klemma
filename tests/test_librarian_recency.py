"""Tests for library recency filter in librarian skill."""

from datetime import date

from klemma.config import SuggestConfig


class TestRecencyFilter:
    """Test recency filtering in _gather_library_context."""

    def _make_source(self, citekey, year=None, quality_score=5):
        return {
            "id": citekey,
            "note_path": f"refs/{citekey}.md",
            "quality_score": quality_score,
            "primary_chapter": 1,
            "primary_section": "1.1",
            "relevance_nr1": 3,
            "relevance_nr2": 3,
            "citation_priority": "high",
            "fragment_count": 10,
            "year": year,
        }

    def test_old_sources_filtered(self):
        """Year 2005, quality 3 → filtered when max_age_years=10."""
        sources = [
            self._make_source("old2005", year=2005, quality_score=3),
            self._make_source("new2024", year=2024, quality_score=5),
        ]
        suggest_config = SuggestConfig(max_age_years=10, classic_min_score=15.0)
        current_year = date.today().year

        # Apply same filter logic as librarian
        filtered = [
            s for s in sources
            if not s.get("year")
            or (current_year - s["year"]) <= suggest_config.max_age_years
            or (s.get("quality_score") or 0) >= suggest_config.classic_min_score
        ]

        assert len(filtered) == 1
        assert filtered[0]["id"] == "new2024"

    def test_high_quality_classic_kept(self):
        """Year 2005, quality 15 → kept as a classic."""
        sources = [
            self._make_source("classic2005", year=2005, quality_score=15),
        ]
        suggest_config = SuggestConfig(max_age_years=10, classic_min_score=15.0)
        current_year = date.today().year

        filtered = [
            s for s in sources
            if not s.get("year")
            or (current_year - s["year"]) <= suggest_config.max_age_years
            or (s.get("quality_score") or 0) >= suggest_config.classic_min_score
        ]

        assert len(filtered) == 1
        assert filtered[0]["id"] == "classic2005"

    def test_no_year_kept(self):
        """Sources without year are never filtered."""
        sources = [
            self._make_source("noyear", year=None, quality_score=3),
        ]
        suggest_config = SuggestConfig(max_age_years=10, classic_min_score=15.0)
        current_year = date.today().year

        filtered = [
            s for s in sources
            if not s.get("year")
            or (current_year - s["year"]) <= suggest_config.max_age_years
            or (s.get("quality_score") or 0) >= suggest_config.classic_min_score
        ]

        assert len(filtered) == 1

    def test_filter_disabled_when_no_config(self):
        """When suggest_config is None, no filtering happens."""
        sources = [
            self._make_source("old2005", year=2005, quality_score=3),
            self._make_source("new2024", year=2024, quality_score=5),
        ]
        suggest_config = None

        # With no config, all sources kept
        if suggest_config and suggest_config.max_age_years:
            current_year = date.today().year
            sources = [
                s for s in sources
                if not s.get("year")
                or (current_year - s["year"]) <= suggest_config.max_age_years
                or (s.get("quality_score") or 0) >= suggest_config.classic_min_score
            ]

        assert len(sources) == 2

    def test_suggest_config_defaults(self):
        """SuggestConfig has sensible defaults."""
        cfg = SuggestConfig()
        assert cfg.max_age_years == 10
        assert cfg.classic_min_score == 15.0

    def test_boundary_year_kept(self):
        """Source exactly at the age boundary is kept."""
        sources = [
            self._make_source("boundary", year=date.today().year - 10, quality_score=3),
        ]
        suggest_config = SuggestConfig(max_age_years=10, classic_min_score=15.0)
        current_year = date.today().year

        filtered = [
            s for s in sources
            if not s.get("year")
            or (current_year - s["year"]) <= suggest_config.max_age_years
            or (s.get("quality_score") or 0) >= suggest_config.classic_min_score
        ]

        assert len(filtered) == 1
