"""Tests for `klemma bib export` command (#59)."""

from pathlib import Path
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from klemma.cli import main as klemma_cli
from klemma.state import StateManager

# --- Fixtures ---


SAMPLE_BIB = """\
@article{smith2020,
  author = {Smith, John},
  title = {A Study of Things},
  journal = {Journal of Things},
  year = {2020},
}

@inproceedings{jones2021,
  author = {Jones, Alice},
  title = {Deep Learning for Something},
  booktitle = {Proceedings of Conf},
  year = {2021},
}

@book{taylor2019,
  author = {Taylor, Bob},
  title = {Classic Textbook},
  publisher = {Academic Press},
  year = {2019},
}
"""


def _make_mock_ctx(state, bib_path=None):
    mock_ctx = MagicMock()
    mock_ctx.state = state
    mock_ctx.config = MagicMock()
    mock_ctx.config.zotero.library_json = str(bib_path) if bib_path else None
    mock_ctx.project = MagicMock()
    mock_ctx.project_root = Path("/tmp/fake_project")
    return mock_ctx


# --- Test: help shows expected options ---


def test_bib_export_help():
    """klemma bib export --help exits 0 and shows expected options."""
    runner = CliRunner()
    result = runner.invoke(klemma_cli, ["bib", "export", "--help"])
    assert result.exit_code == 0
    assert "--citekeys" in result.output
    assert "--section" in result.output
    assert "--output" in result.output


# --- Test: export by citekeys filters bib entries ---


def test_bib_export_by_citekeys(tmp_path):
    """klemma bib export --citekeys @smith2020,@jones2021 outputs only those 2 entries."""
    bib_file = tmp_path / "references.bib"
    bib_file.write_text(SAMPLE_BIB)
    db = tmp_path / "state.db"
    sm = StateManager(db)

    mock_ctx = _make_mock_ctx(sm)
    runner = CliRunner()
    with (
        patch("klemma.cli._get_context", return_value=mock_ctx),
        patch("klemma.cli._init_components", return_value=mock_ctx),
    ):
        result = runner.invoke(
            klemma_cli,
            ["bib", "export", "--citekeys", "@smith2020,@jones2021", "--bib", str(bib_file)],
        )

    assert result.exit_code == 0
    assert "smith2020" in result.output
    assert "jones2021" in result.output
    assert "taylor2019" not in result.output


def test_bib_export_strips_at_prefix(tmp_path):
    """citekeys with and without @ prefix both work."""
    bib_file = tmp_path / "references.bib"
    bib_file.write_text(SAMPLE_BIB)
    db = tmp_path / "state.db"
    sm = StateManager(db)

    mock_ctx = _make_mock_ctx(sm)
    runner = CliRunner()
    with (
        patch("klemma.cli._get_context", return_value=mock_ctx),
        patch("klemma.cli._init_components", return_value=mock_ctx),
    ):
        result = runner.invoke(
            klemma_cli,
            # Mix of @ and bare citekeys
            ["bib", "export", "--citekeys", "smith2020,@taylor2019", "--bib", str(bib_file)],
        )

    assert result.exit_code == 0
    assert "smith2020" in result.output
    assert "taylor2019" in result.output
    assert "jones2021" not in result.output


# --- Test: export by section range uses DB source assignments ---


def test_bib_export_by_section(tmp_path):
    """klemma bib export --section 1..2 exports refs assigned to sections 1.x and 2.x."""
    bib_file = tmp_path / "references.bib"
    bib_file.write_text(SAMPLE_BIB)
    db = tmp_path / "state.db"
    sm = StateManager(db)

    # Register and assign sources to sections
    sm.register_sources(["smith2020", "jones2021", "taylor2019"])
    sm.set_source_sections("smith2020", ["1.1"], [1])
    sm.set_source_sections("jones2021", ["2.1"], [2])
    sm.set_source_sections("taylor2019", ["3.1"], [3])

    mock_ctx = _make_mock_ctx(sm)
    runner = CliRunner()
    with (
        patch("klemma.cli._get_context", return_value=mock_ctx),
        patch("klemma.cli._init_components", return_value=mock_ctx),
    ):
        result = runner.invoke(
            klemma_cli,
            ["bib", "export", "--section", "1..2", "--bib", str(bib_file)],
        )

    assert result.exit_code == 0
    assert "smith2020" in result.output
    assert "jones2021" in result.output
    assert "taylor2019" not in result.output


def test_bib_export_section_single(tmp_path):
    """klemma bib export --section 1 exports only section 1.x sources."""
    bib_file = tmp_path / "references.bib"
    bib_file.write_text(SAMPLE_BIB)
    db = tmp_path / "state.db"
    sm = StateManager(db)

    sm.register_sources(["smith2020", "jones2021"])
    sm.set_source_sections("smith2020", ["1.1"], [1])
    sm.set_source_sections("jones2021", ["2.3"], [2])

    mock_ctx = _make_mock_ctx(sm)
    runner = CliRunner()
    with (
        patch("klemma.cli._get_context", return_value=mock_ctx),
        patch("klemma.cli._init_components", return_value=mock_ctx),
    ):
        result = runner.invoke(
            klemma_cli,
            ["bib", "export", "--section", "1", "--bib", str(bib_file)],
        )

    assert result.exit_code == 0
    assert "smith2020" in result.output
    assert "jones2021" not in result.output


# --- Test: --output writes to file ---


def test_bib_export_writes_to_file(tmp_path):
    """--output writes filtered bib to file; stdout is empty or minimal."""
    bib_file = tmp_path / "references.bib"
    bib_file.write_text(SAMPLE_BIB)
    out_file = tmp_path / "paper.bib"
    db = tmp_path / "state.db"
    sm = StateManager(db)

    mock_ctx = _make_mock_ctx(sm)
    runner = CliRunner()
    with (
        patch("klemma.cli._get_context", return_value=mock_ctx),
        patch("klemma.cli._init_components", return_value=mock_ctx),
    ):
        result = runner.invoke(
            klemma_cli,
            [
                "bib", "export",
                "--citekeys", "@smith2020",
                "--bib", str(bib_file),
                "--output", str(out_file),
            ],
        )

    assert result.exit_code == 0
    assert out_file.exists()
    content = out_file.read_text()
    assert "smith2020" in content
    assert "jones2021" not in content


# --- Test: missing bib file gives clear error ---


def test_bib_export_missing_bib(tmp_path):
    """If bib file does not exist, exit with non-zero and informative message."""
    db = tmp_path / "state.db"
    sm = StateManager(db)
    mock_ctx = _make_mock_ctx(sm)

    runner = CliRunner()
    with (
        patch("klemma.cli._get_context", return_value=mock_ctx),
        patch("klemma.cli._init_components", return_value=mock_ctx),
    ):
        result = runner.invoke(
            klemma_cli,
            ["bib", "export", "--citekeys", "@smith2020", "--bib", str(tmp_path / "nope.bib")],
        )

    assert result.exit_code != 0


# --- Test: no filter flags gives clear error ---


def test_bib_export_requires_filter(tmp_path):
    """Running export with neither --citekeys nor --section gives error."""
    bib_file = tmp_path / "references.bib"
    bib_file.write_text(SAMPLE_BIB)
    db = tmp_path / "state.db"
    sm = StateManager(db)
    mock_ctx = _make_mock_ctx(sm)

    runner = CliRunner()
    with (
        patch("klemma.cli._get_context", return_value=mock_ctx),
        patch("klemma.cli._init_components", return_value=mock_ctx),
    ):
        result = runner.invoke(
            klemma_cli,
            ["bib", "export", "--bib", str(bib_file)],
        )

    assert result.exit_code != 0
