"""Tests for `klemma source show <citekey>` command."""

from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from klemma.cli import main as klemma_cli
from klemma.commands import analyze as analyze_mod
from klemma.state import StateManager


def _make_ctx(state):
    mock_ctx = MagicMock()
    mock_ctx.state = state
    mock_ctx.config = MagicMock()
    mock_ctx.library = None
    mock_ctx.embeddings = None
    return mock_ctx


def _run(runner, mock_ctx, tmp_path, args):
    with patch("klemma.cli.discover_project_root", return_value=tmp_path), \
         patch("klemma.cli._init_components", return_value=mock_ctx), \
         patch.object(analyze_mod, "_get_context", return_value=mock_ctx):
        return runner.invoke(klemma_cli, args)


def test_source_show_displays_metadata(tmp_path):
    """source show prints title, authors, year, sections, fragments."""
    db = tmp_path / ".klemma" / "state.db"
    db.parent.mkdir(parents=True)
    sm = StateManager(db)
    sm.register_sources(["goessling2016"])
    sm.update_source_info(
        "goessling2016",
        title="Predictability of Arctic Sea Ice",
        authors="Goessling HF, Jung T",
        year=2016,
        doi="10.1002/2016GL071665",
    )
    sm.mark_completed("goessling2016", "notes/goessling2016.md")
    sm.save_fragments("goessling2016", [
        {"text": "IIEE measures ice edge error", "type": "definition", "section": "1.1"},
        {"text": "Misplacement error dominates", "type": "result", "section": "2.3"},
    ])

    mock_ctx = _make_ctx(sm)
    runner = CliRunner()
    result = _run(runner, mock_ctx, tmp_path, ["source", "show", "goessling2016"])

    assert result.exit_code == 0, result.output
    assert "goessling2016" in result.output
    assert "Predictability of Arctic Sea Ice" in result.output
    assert "Goessling HF, Jung T" in result.output
    assert "2016" in result.output
    assert "10.1002/2016GL071665" in result.output
    assert "1.1" in result.output
    assert "2.3" in result.output
    assert "IIEE measures ice edge error" in result.output


def test_source_show_not_found(tmp_path):
    """source show exits with error for unknown citekey."""
    db = tmp_path / ".klemma" / "state.db"
    db.parent.mkdir(parents=True)
    sm = StateManager(db)

    mock_ctx = _make_ctx(sm)
    runner = CliRunner()
    result = _run(runner, mock_ctx, tmp_path, ["source", "show", "nonexistent2024"])

    assert result.exit_code != 0
    assert "not found" in result.output


def test_source_show_no_fragments(tmp_path):
    """source show works for a source with no fragments."""
    db = tmp_path / ".klemma" / "state.db"
    db.parent.mkdir(parents=True)
    sm = StateManager(db)
    sm.register_sources(["empty2024"])
    sm.update_source_info("empty2024", title="Empty Paper", authors="Nobody", year=2024)

    mock_ctx = _make_ctx(sm)
    runner = CliRunner()
    result = _run(runner, mock_ctx, tmp_path, ["source", "show", "empty2024"])

    assert result.exit_code == 0, result.output
    assert "Empty Paper" in result.output
    assert "Fragments" in result.output


def test_source_show_help(tmp_path):
    """source show appears in source group help."""
    db = tmp_path / ".klemma" / "state.db"
    db.parent.mkdir(parents=True)
    sm = StateManager(db)

    mock_ctx = _make_ctx(sm)
    runner = CliRunner()
    result = _run(runner, mock_ctx, tmp_path, ["source", "--help"])
    assert "show" in result.output
    assert "role" in result.output
