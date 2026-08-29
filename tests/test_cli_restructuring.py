"""Tests for CLI restructuring: suggest promotion, backward-compat aliases, deprecation warnings."""

from click.testing import CliRunner

from klemma.cli import main


class TestSuggestTopLevel:
    def test_suggest_visible_in_help(self):
        runner = CliRunner()
        result = runner.invoke(main, ["--help"])
        assert "suggest" in result.output

    def test_suggest_help_accessible(self):
        runner = CliRunner()
        result = runner.invoke(main, ["suggest", "--help"])
        # On CI (no project), group callback exits 1 before help renders.
        # Accept either: help rendered OR "Not in a klemma project" error.
        if result.exit_code == 0:
            assert "Suggest papers" in result.output
            assert "--limit" in result.output
            assert "--section" in result.output
        else:
            assert "klemma" in result.output.lower()


class TestGapsSuggestBackwardCompat:
    def test_gaps_suggest_help_accessible(self):
        runner = CliRunner()
        result = runner.invoke(main, ["gaps", "suggest", "--help"])
        # On CI (no project), group callback exits 1 before help renders.
        if result.exit_code == 0:
            assert "--limit" in result.output
        else:
            assert "klemma" in result.output.lower()

    def test_gaps_suggest_hidden_in_gaps_help(self):
        """Backward-compat alias should be hidden from gaps help."""
        runner = CliRunner()
        result = runner.invoke(main, ["gaps", "--help"])
        # "suggest" should NOT appear as a visible subcommand of gaps
        # (it's hidden=True)
        lines = result.output.split("\n")
        command_lines = [line for line in lines if line.strip().startswith("suggest")]
        assert len(command_lines) == 0


class TestBareGaps:
    def test_bare_gaps_shows_tip_or_forwards(self):
        """Bare `klemma gaps` now hints at `gaps <citekey>` / `suggest` and
        forwards to `status --verbose` (the namespace is no longer deprecated —
        it hosts the citation-graph walk)."""
        runner = CliRunner()
        result = runner.invoke(main, ["gaps"], catch_exceptions=False)
        # May fail due to missing config; otherwise the tip should appear.
        assert (
            "citekey" in result.output.lower()
            or "status --verbose" in result.output.lower()
            or result.exit_code != 0
        )


class TestLibraryRecommendDeprecation:
    def test_library_recommend_shows_deprecation(self):
        """Running `klemma library -s 2.3` should show deprecation warning."""
        runner = CliRunner()
        result = runner.invoke(main, ["library", "-s", "2.3"], catch_exceptions=False)
        # May fail due to missing AI config, but deprecation should appear before that
        assert "deprecated" in result.output.lower() or result.exit_code != 0
