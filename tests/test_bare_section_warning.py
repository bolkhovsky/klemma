"""Tests for bare chapter section warning in _sync_sections (issue #148)."""

from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from klemma.cli import main as klemma_cli
from klemma.state import StateManager


def _make_ctx(tmp_path, vault_notes: dict):
    """Build a mock KlemmaContext with a real StateManager and fake vault."""
    db = tmp_path / ".klemma" / "state.db"
    db.parent.mkdir(parents=True)
    sm = StateManager(db)

    mock_vault = MagicMock()
    mock_vault.list_notes.return_value = list(vault_notes.keys())
    mock_vault.get_properties.side_effect = lambda name: vault_notes.get(name, {})

    mock_ctx = MagicMock()
    mock_ctx.state = sm
    mock_ctx.vault = mock_vault
    mock_ctx.config = MagicMock()
    mock_ctx.config.obsidian.notes_folder = ""
    mock_ctx.library = None
    mock_ctx.project = None
    mock_ctx.project_store = None
    mock_ctx.user_library = None
    mock_ctx.embeddings = None
    return mock_ctx


def test_bare_section_emits_warning(tmp_path):
    """vault note with sections: [1] triggers a yellow warning during sync."""
    mock_ctx = _make_ctx(tmp_path, {
        "@paper1": {"sections": [1]},
    })

    runner = CliRunner()
    with patch("klemma.cli.discover_project_root", return_value=tmp_path), \
         patch("klemma.cli._init_components", return_value=mock_ctx), \
         patch("klemma.cli._get_context", return_value=mock_ctx):
        result = runner.invoke(klemma_cli, ["status"])

    assert "bare chapter" in result.output or "Warning" in result.output, result.output
    assert "'1'" in result.output, result.output


def test_subsection_no_warning(tmp_path):
    """Proper subsection assignment (e.g. 1.1) produces no warning."""
    mock_ctx = _make_ctx(tmp_path, {
        "@paper1": {"sections": ["1.1"]},
    })

    runner = CliRunner()
    with patch("klemma.cli.discover_project_root", return_value=tmp_path), \
         patch("klemma.cli._init_components", return_value=mock_ctx), \
         patch("klemma.cli._get_context", return_value=mock_ctx):
        result = runner.invoke(klemma_cli, ["status"])

    assert "bare chapter" not in result.output


def test_mixed_sections_warns_only_bare(tmp_path):
    """Mixed sections list: only bare integers trigger a warning."""
    mock_ctx = _make_ctx(tmp_path, {
        "@paper1": {"sections": ["1.1", "2"]},  # 2 is bare, 1.1 is fine
    })

    runner = CliRunner()
    with patch("klemma.cli.discover_project_root", return_value=tmp_path), \
         patch("klemma.cli._init_components", return_value=mock_ctx), \
         patch("klemma.cli._get_context", return_value=mock_ctx):
        result = runner.invoke(klemma_cli, ["status"])

    assert "'2'" in result.output
    assert "'1.1'" not in result.output


def test_multiple_sources_each_warned(tmp_path):
    """Two sources with bare chapter assignments each get a warning line."""
    mock_ctx = _make_ctx(tmp_path, {
        "@alpha": {"sections": ["3"]},
        "@beta": {"sections": ["3"]},
    })

    runner = CliRunner()
    with patch("klemma.cli.discover_project_root", return_value=tmp_path), \
         patch("klemma.cli._init_components", return_value=mock_ctx), \
         patch("klemma.cli._get_context", return_value=mock_ctx):
        result = runner.invoke(klemma_cli, ["status"])

    # Both sources should appear in warnings
    assert result.output.count("Warning") >= 2 or result.output.count("bare chapter") >= 2
