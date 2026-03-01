from pathlib import Path
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from klemma.cli import main as klemma_cli
from klemma.state import StateManager


def test_embed_accepts_multiple_citekeys_and_warns_missing(tmp_path, monkeypatch):
    runner = CliRunner()
    monkeypatch.setenv("KLEMMA_HOME", str(tmp_path / ".klemma_home"))

    with runner.isolated_filesystem():
        init_result = runner.invoke(klemma_cli, ["init", "--no-input"])
        assert init_result.exit_code == 0

        state = StateManager(Path(".klemma/data/klemma.db"))
        state.register_sources(["exists1"])

        result = runner.invoke(klemma_cli, ["embed", "exists1", "missing1", "--dry-run"])

    assert result.exit_code == 0
    assert "Missing citekeys: missing1" in result.output
    assert "Would embed 0 sources" in result.output
    assert "sources have no abstract" in result.output


def test_embed_fragments_flag(tmp_path):
    """klemma embed --fragments embeds un-embedded fragments."""
    db = tmp_path / ".klemma" / "state.db"
    db.parent.mkdir(parents=True)
    sm = StateManager(db)
    sm.register_sources(["paper1"])
    sm.mark_completed("paper1", "notes/paper1.md")
    sm.save_fragments("paper1", [
        {"text": "Ice forecast accuracy", "type": "result"},
        {"text": "Neural networks for prediction", "type": "method"},
    ])

    mock_emb = MagicMock()
    mock_emb.model_name = "test-model"
    mock_emb.embed.return_value = [0.1, 0.2, 0.3]

    mock_ctx = MagicMock()
    mock_ctx.state = sm
    mock_ctx.embeddings = mock_emb
    mock_ctx.config = MagicMock()
    mock_ctx.library = None

    runner = CliRunner()
    with patch("klemma.cli.discover_project_root", return_value=tmp_path), \
         patch("klemma.cli._init_components", return_value=mock_ctx), \
         patch("klemma.cli._get_context", return_value=mock_ctx):
        result = runner.invoke(klemma_cli, ["embed", "--fragments"])

    assert result.exit_code == 0, result.output
    assert "Embedded: 2" in result.output
    assert mock_emb.embed.call_count == 2

    # Verify fragments now have embeddings
    stats = sm.get_fragment_embedding_stats()
    assert stats["embedded"] == 2


def test_embed_fragments_dry_run(tmp_path):
    """klemma embed --fragments --dry-run shows count without embedding."""
    db = tmp_path / ".klemma" / "state.db"
    db.parent.mkdir(parents=True)
    sm = StateManager(db)
    sm.register_sources(["paper1"])
    sm.save_fragments("paper1", [{"text": "Fragment A", "type": "result"}])

    mock_ctx = MagicMock()
    mock_ctx.state = sm
    mock_ctx.embeddings = MagicMock()
    mock_ctx.config = MagicMock()
    mock_ctx.library = None

    runner = CliRunner()
    with patch("klemma.cli.discover_project_root", return_value=tmp_path), \
         patch("klemma.cli._init_components", return_value=mock_ctx), \
         patch("klemma.cli._get_context", return_value=mock_ctx):
        result = runner.invoke(klemma_cli, ["embed", "--fragments", "--dry-run"])

    assert result.exit_code == 0, result.output
    assert "Would embed 1 fragments" in result.output
