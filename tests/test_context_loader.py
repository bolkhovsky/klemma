"""Tests for context_loader shared helpers."""


from klemma.skills.context_loader import load_research_report


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
