"""Tests for `klemma library prune --apply` — execute prune verdicts."""

from pathlib import Path
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

    # Register and mark sources
    sm.register_sources(["dropMe2020", "keepMe2020", "protectedHigh2020"])
    sm.mark_completed("dropMe2020", "notes/dropMe2020.md")
    sm.mark_completed("keepMe2020", "notes/keepMe2020.md")
    sm.mark_completed("protectedHigh2020", "notes/protectedHigh2020.md")

    # Set metadata so we can read titles
    sm.update_source_info("dropMe2020", title="Paper to drop", year=2020)
    sm.update_source_info("keepMe2020", title="Paper to keep", year=2020)
    sm.update_source_info("protectedHigh2020", title="High quality", year=2020)

    # Add fragments for drop target
    sm.save_fragments("dropMe2020", [
        {"text": "some fragment", "type": "quote"},
    ])

    # Save prune verdicts (protected sources are auto-filtered by _is_protected)
    sm.save_prune_verdicts(
        drop=[{"citekey": "dropMe2020", "reason": "Low quality"}],
        maybe=[{"citekey": "keepMe2020", "reason": "Borderline"}],
    )

    return sm


@pytest.fixture()
def mock_ctx(prune_state):
    """Mock KlemmaContext with the prune_state."""
    ctx = MagicMock()
    ctx.state = prune_state
    ctx.config = MagicMock()
    ctx.library = None
    ctx.vault = None
    ctx.embeddings = None
    return ctx


class TestPruneApply:
    def test_apply_deletes_drop_sources(self, mock_ctx, prune_state):
        runner = CliRunner()
        with patch("klemma.cli._get_context", return_value=mock_ctx):
            result = runner.invoke(klemma_cli, ["library", "prune", "--apply", "--yes"])

        assert result.exit_code == 0, result.output
        assert "Removed 1 sources" in result.output
        assert "dropMe2020" in result.output

        # Verify source is gone
        assert prune_state.get_source("dropMe2020") is None
        assert prune_state.get_source("keepMe2020") is not None

        # Verify fragments cascaded
        frags = prune_state.get_fragments()
        drop_frags = [f for f in frags if f.get("source_id") == "dropMe2020"]
        assert len(drop_frags) == 0

    def test_apply_no_targets(self, mock_ctx, prune_state):
        """--apply with no drop verdicts prints info message."""
        # Clear all drop verdicts
        prune_state.clear_prune_verdict("dropMe2020")

        runner = CliRunner()
        with patch("klemma.cli._get_context", return_value=mock_ctx):
            result = runner.invoke(klemma_cli, ["library", "prune", "--apply", "--yes"])

        assert result.exit_code == 0, result.output
        assert "No 'drop' verdicts" in result.output

    def test_apply_without_yes_aborts(self, mock_ctx, prune_state):
        """--apply without --yes prompts and aborts on 'n'."""
        runner = CliRunner()
        with patch("klemma.cli._get_context", return_value=mock_ctx):
            result = runner.invoke(
                klemma_cli, ["library", "prune", "--apply"], input="n\n"
            )

        assert result.exit_code == 0, result.output
        assert "Aborted" in result.output

        # Source should still exist
        assert prune_state.get_source("dropMe2020") is not None

    def test_apply_shows_targets_before_confirm(self, mock_ctx):
        """--apply lists targets before asking for confirmation."""
        runner = CliRunner()
        with patch("klemma.cli._get_context", return_value=mock_ctx):
            result = runner.invoke(
                klemma_cli, ["library", "prune", "--apply"], input="n\n"
            )

        assert "dropMe2020" in result.output
        assert "Paper to drop" in result.output
        assert "Sources to remove" in result.output
