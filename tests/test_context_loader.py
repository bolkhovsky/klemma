"""Tests for context_loader shared helpers."""

from unittest.mock import MagicMock

from klemma.skills.context_loader import load_research_report, load_section_sources


class TestLoadResearchReport:
    """Research report loading from project_root."""

    def test_reads_from_notes_research(self, tmp_path):
        notes = tmp_path / "notes" / "research"
        notes.mkdir(parents=True)
        report = notes / "Research_1.3.md"
        report.write_text("# Research 1.3\nContent here", encoding="utf-8")

        result = load_research_report("1.3", tmp_path)

        assert result is not None
        assert "Content here" in result

    def test_falls_back_to_legacy_path(self, tmp_path):
        report = tmp_path / "Research_2.1.md"
        report.write_text("Legacy report", encoding="utf-8")

        result = load_research_report("2.1", tmp_path)

        assert result == "Legacy report"

    def test_returns_none_when_missing(self, tmp_path):
        result = load_research_report("9.9", tmp_path)

        assert result is None

    def test_returns_none_for_empty_file(self, tmp_path):
        notes = tmp_path / "notes" / "research"
        notes.mkdir(parents=True)
        (notes / "Research_1.1.md").write_text("  \n  ", encoding="utf-8")

        result = load_research_report("1.1", tmp_path)

        assert result is None

    def test_prefers_notes_research_over_legacy(self, tmp_path):
        notes = tmp_path / "notes" / "research"
        notes.mkdir(parents=True)
        (notes / "Research_1.2.md").write_text("new path", encoding="utf-8")
        (tmp_path / "Research_1.2.md").write_text("legacy path", encoding="utf-8")

        result = load_research_report("1.2", tmp_path)

        assert result == "new path"


class TestLoadSectionSourcesMetadataGate:
    """Ghost sources without title/authors are excluded (#114)."""

    def _make_state(self, sources):
        state = MagicMock()
        state.get_by_section.return_value = sources
        state.get_by_chapter.return_value = []
        state.get_all_sources.return_value = sources
        return state

    def _make_vault(self):
        vault = MagicMock()
        vault.read_note.return_value = None
        return vault

    def test_ghost_source_excluded(self):
        """Source with no title and no authors is filtered out."""
        sources = [
            {"id": "ghostKey", "title": None, "authors": None,
             "quality_score": 4, "citation_priority": "medium"},
        ]
        state = self._make_state(sources)
        vault = self._make_vault()

        result = load_section_sources("1.1", 1, state, vault)

        assert len(result) == 0

    def test_source_with_metadata_included(self):
        """Source with title and authors passes the filter."""
        sources = [
            {"id": "smith2024Ice", "title": "Ice Dynamics",
             "authors": "Smith, J.", "quality_score": 4,
             "citation_priority": "high"},
        ]
        state = self._make_state(sources)
        vault = self._make_vault()

        result = load_section_sources("1.1", 1, state, vault)

        assert len(result) == 1
        assert result[0]["id"] == "smith2024Ice"

    def test_mixed_sources_only_valid_pass(self):
        """Only sources with metadata survive the filter."""
        sources = [
            {"id": "goodKey", "title": "A Paper", "authors": "Author, A.",
             "quality_score": 3, "citation_priority": "medium"},
            {"id": "ghostKey", "title": "", "authors": "",
             "quality_score": 4, "citation_priority": "high"},
            {"id": "anotherGood", "title": "Another", "authors": "B.",
             "quality_score": 2, "citation_priority": "low"},
        ]
        state = self._make_state(sources)
        vault = self._make_vault()

        result = load_section_sources("1.1", 1, state, vault)

        ids = [r["id"] for r in result]
        assert "goodKey" in ids
        assert "anotherGood" in ids
        assert "ghostKey" not in ids

    def test_ghost_excluded_with_citekey_filter(self):
        """Ghost sources also excluded when using citekey_filter (RAG path)."""
        sources = [
            {"id": "ghostRAG", "title": None, "authors": None,
             "quality_score": 4, "citation_priority": "medium"},
            {"id": "realRAG", "title": "Real Paper", "authors": "Auth",
             "quality_score": 3, "citation_priority": "medium"},
        ]
        state = self._make_state(sources)
        vault = self._make_vault()

        result = load_section_sources(
            "1.1", 1, state, vault,
            citekey_filter={"ghostRAG", "realRAG"},
        )

        assert len(result) == 1
        assert result[0]["id"] == "realRAG"
