"""Tests for `klemma library prune --apply` — execute prune verdicts."""

from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from klemma.cli import main as klemma_cli
from klemma.state import StateManager


@pytest.fixture()
def prune_state(tmp_path):
    """StateManager with sources and prune verdicts."""
    db = tmp_path / "test.db"
    sm = StateManager(db)

    sm.register_sources(["dropMe2020", "keepMe2020", "protectedHigh2020"])
    sm.mark_completed("dropMe2020", "notes/dropMe2020.md")
    sm.mark_completed("keepMe2020", "notes/keepMe2020.md")
    sm.mark_completed("protectedHigh2020", "notes/protectedHigh2020.md")

    sm.update_source_info("dropMe2020", title="Paper to drop", year=2020,
                          authors="Smith J., Jones K.", abstract="This paper is irrelevant.")
    sm.update_source_info("keepMe2020", title="Paper to keep", year=2020)
    sm.update_source_info("protectedHigh2020", title="High quality", year=2020)

    sm.save_fragments("dropMe2020", [
        {"text": "some fragment", "type": "quote"},
    ])

    sm.save_prune_verdicts(
        drop=[{"citekey": "dropMe2020", "reason": "Low quality, irrelevant to topic"}],
        maybe=[{"citekey": "keepMe2020", "reason": "Borderline"}],
    )

    return sm


@pytest.fixture()
def mock_ctx(prune_state, tmp_path):
    """Mock KlemmaContext with the prune_state."""
    ctx = MagicMock()
    ctx.state = prune_state
    ctx.config = MagicMock()
    ctx.library = None
    ctx.vault = None
    ctx.embeddings = None
    ctx.project_root = tmp_path
    return ctx


def _patches(mock_ctx, tmp_path):
    return (
        patch("klemma.cli.discover_project_root", return_value=tmp_path),
        patch("klemma.cli._init_components", return_value=mock_ctx),
        patch("klemma.cli._get_context", return_value=mock_ctx),
    )


class TestPruneApply:
    def test_apply_yes_deletes_all(self, mock_ctx, prune_state, tmp_path):
        """--apply --yes deletes all drop sources without prompting."""
        runner = CliRunner()
        p1, p2, p3 = _patches(mock_ctx, tmp_path)
        with p1, p2, p3:
            result = runner.invoke(klemma_cli, ["library", "prune", "--apply", "--yes"])

        assert result.exit_code == 0, result.output
        assert "1 deleted" in result.output
        assert "dropMe2020" in result.output
        assert prune_state.get_source("dropMe2020") is None
        assert prune_state.get_source("keepMe2020") is not None

    def test_apply_shows_details(self, mock_ctx, tmp_path):
        """--apply shows title, authors, year, reason, abstract."""
        runner = CliRunner()
        p1, p2, p3 = _patches(mock_ctx, tmp_path)
        with p1, p2, p3:
            result = runner.invoke(klemma_cli, ["library", "prune", "--apply", "--yes"])

        assert "Paper to drop" in result.output
        assert "Smith J., Jones K." in result.output
        assert "2020" in result.output
        assert "Low quality" in result.output
        assert "irrelevant" in result.output

    def test_apply_interactive_skip(self, mock_ctx, prune_state, tmp_path):
        """Answering 'n' keeps the source."""
        runner = CliRunner()
        p1, p2, p3 = _patches(mock_ctx, tmp_path)
        with p1, p2, p3:
            result = runner.invoke(
                klemma_cli, ["library", "prune", "--apply"], input="n\n"
            )

        assert result.exit_code == 0, result.output
        assert "Kept" in result.output
        assert "0 deleted" in result.output
        assert prune_state.get_source("dropMe2020") is not None

    def test_apply_interactive_delete(self, mock_ctx, prune_state, tmp_path):
        """Answering 'y' deletes the source."""
        runner = CliRunner()
        p1, p2, p3 = _patches(mock_ctx, tmp_path)
        with p1, p2, p3:
            result = runner.invoke(
                klemma_cli, ["library", "prune", "--apply"], input="y\n"
            )

        assert result.exit_code == 0, result.output
        assert "Deleted" in result.output
        assert "1 deleted" in result.output
        assert prune_state.get_source("dropMe2020") is None

    def test_apply_interactive_quit(self, mock_ctx, prune_state, tmp_path):
        """Answering 'q' stops the loop immediately."""
        runner = CliRunner()
        p1, p2, p3 = _patches(mock_ctx, tmp_path)
        with p1, p2, p3:
            result = runner.invoke(
                klemma_cli, ["library", "prune", "--apply"], input="q\n"
            )

        assert result.exit_code == 0, result.output
        assert "Quit" in result.output
        assert prune_state.get_source("dropMe2020") is not None

    def test_apply_no_targets(self, mock_ctx, prune_state, tmp_path):
        """--apply with no drop verdicts prints info message."""
        prune_state.clear_prune_verdict("dropMe2020")

        runner = CliRunner()
        p1, p2, p3 = _patches(mock_ctx, tmp_path)
        with p1, p2, p3:
            result = runner.invoke(klemma_cli, ["library", "prune", "--apply", "--yes"])

        assert result.exit_code == 0, result.output
        assert "No 'drop' verdicts" in result.output

    def test_apply_cascades_fragments(self, mock_ctx, prune_state, tmp_path):
        """Deleting a source also removes its fragments."""
        runner = CliRunner()
        p1, p2, p3 = _patches(mock_ctx, tmp_path)
        with p1, p2, p3:
            result = runner.invoke(klemma_cli, ["library", "prune", "--apply", "--yes"])

        assert result.exit_code == 0, result.output
        frags = prune_state.get_fragments()
        drop_frags = [f for f in frags if f.get("source_id") == "dropMe2020"]
        assert len(drop_frags) == 0
