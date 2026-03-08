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

        result = runner.invoke(klemma_cli, ["embed", "sources", "exists1", "missing1", "--dry-run"])

    assert result.exit_code == 0
    assert "Missing citekeys: missing1" in result.output
    assert "Would embed 1 sources" in result.output


def test_embed_fragments_flag(tmp_path):
    """klemma embed fragments embeds un-embedded fragments."""
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
        result = runner.invoke(klemma_cli, ["embed", "fragments"])

    assert result.exit_code == 0, result.output
    assert "Embedded: 2" in result.output
    assert mock_emb.embed.call_count == 2

    # Verify fragments now have embeddings
    stats = sm.get_fragment_embedding_stats()
    assert stats["embedded"] == 2


def test_embed_fragments_dry_run(tmp_path):
    """klemma embed fragments --dry-run shows count without embedding."""
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
        result = runner.invoke(klemma_cli, ["embed", "fragments", "--dry-run"])

    assert result.exit_code == 0, result.output
    assert "Would embed 1 fragments" in result.output


def test_section_embedding_roundtrip(tmp_path):
    """Save and retrieve section centroid embedding via StateManager."""
    db = tmp_path / "state.db"
    sm = StateManager(db)

    vec = [0.1, 0.2, 0.3, 0.4]
    sm.save_section_embedding("1.1", vec, "test-model", source_count=5)

    result = sm.get_section_embedding("1.1")
    assert result is not None
    got_vec, got_model, got_count = result
    assert got_model == "test-model"
    assert got_count == 5
    for a, b in zip(got_vec, vec):
        assert abs(a - b) < 1e-6

    # get_all returns dict
    all_embs = sm.get_all_section_embeddings()
    assert "1.1" in all_embs
    assert len(all_embs["1.1"]) == 4

    # Stats
    stats = sm.get_section_embedding_stats()
    assert stats["embedded_sections"] == 1
    assert stats["models"]["test-model"] == 1


def _make_state_with_sections_and_embeddings(tmp_path):
    """Helper: StateManager with sources, sections, and source embeddings."""
    db = tmp_path / ".klemma" / "state.db"
    db.parent.mkdir(parents=True)
    sm = StateManager(db)

    sm.register_sources(["paper1", "paper2", "paper3"])
    sm.mark_completed("paper1", "n/a")
    sm.mark_completed("paper2", "n/a")
    sm.mark_completed("paper3", "n/a")

    sm.set_source_sections("paper1", ["1.1"], [1])
    sm.set_source_sections("paper2", ["1.1", "2.1"], [1, 2])
    sm.set_source_sections("paper3", ["2.1"], [2])

    # Give paper1 and paper2 source embeddings; paper3 has none
    sm.save_embedding("paper1", [1.0, 0.0, 0.0], "test-model")
    sm.save_embedding("paper2", [0.0, 1.0, 0.0], "test-model")

    return sm


def test_embed_sections_flag(tmp_path):
    """klemma embed sections computes and stores centroid embeddings."""
    sm = _make_state_with_sections_and_embeddings(tmp_path)

    mock_emb = MagicMock()
    mock_emb.model_name = "test-model"

    mock_ctx = MagicMock()
    mock_ctx.state = sm
    mock_ctx.embeddings = mock_emb
    mock_ctx.config = MagicMock()
    mock_ctx.library = None

    runner = CliRunner()
    with patch("klemma.cli.discover_project_root", return_value=tmp_path), \
         patch("klemma.cli._init_components", return_value=mock_ctx), \
         patch("klemma.cli._get_context", return_value=mock_ctx):
        result = runner.invoke(klemma_cli, ["embed", "sections"])

    assert result.exit_code == 0, result.output
    assert "Section embeddings: 2 computed" in result.output

    # Verify section 1.1 centroid = mean([1,0,0], [0,1,0]) = [0.5, 0.5, 0]
    emb_result = sm.get_section_embedding("1.1", "test-model")
    assert emb_result is not None
    vec, model, count = emb_result
    assert model == "test-model"
    assert count == 2
    assert abs(vec[0] - 0.5) < 1e-6
    assert abs(vec[1] - 0.5) < 1e-6
    assert abs(vec[2] - 0.0) < 1e-6

    # Section 2.1: only paper2 has embedding (paper3 has none)
    emb_result2 = sm.get_section_embedding("2.1", "test-model")
    assert emb_result2 is not None
    vec2, _, count2 = emb_result2
    assert count2 == 1
    assert abs(vec2[1] - 1.0) < 1e-6


def test_embed_sections_dry_run(tmp_path):
    """klemma embed sections --dry-run shows count without storing."""
    sm = _make_state_with_sections_and_embeddings(tmp_path)

    mock_emb = MagicMock()
    mock_emb.model_name = "test-model"

    mock_ctx = MagicMock()
    mock_ctx.state = sm
    mock_ctx.embeddings = mock_emb
    mock_ctx.config = MagicMock()
    mock_ctx.library = None

    runner = CliRunner()
    with patch("klemma.cli.discover_project_root", return_value=tmp_path), \
         patch("klemma.cli._init_components", return_value=mock_ctx), \
         patch("klemma.cli._get_context", return_value=mock_ctx):
        result = runner.invoke(klemma_cli, ["embed", "sections", "--dry-run"])

    assert result.exit_code == 0, result.output
    assert "Would embed 2 sections" in result.output

    # Verify nothing was stored
    stats = sm.get_section_embedding_stats()
    assert stats["embedded_sections"] == 0
