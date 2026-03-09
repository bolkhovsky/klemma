"""Tests for `klemma add` — unified source ingestion command (#122)."""

from pathlib import Path
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from klemma.cli import _detect_input_type
from klemma.cli import main as klemma_cli
from klemma.state import StateManager

# --- _detect_input_type tests ---


def test_detect_url_https():
    assert _detect_input_type("https://arxiv.org/abs/1234.5678") == "url"


def test_detect_url_http():
    assert _detect_input_type("http://example.com/paper.pdf") == "url"


def test_detect_url_doi():
    assert _detect_input_type("doi:10.1234/foo") == "url"


def test_detect_citekey():
    assert _detect_input_type("smith2024ice") == "citekey"


def test_detect_pdf_path(tmp_path):
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF-1.4 test")
    assert _detect_input_type(str(pdf)) == "path"


def test_detect_pdf_nonexistent():
    """A .pdf path that doesn't exist is treated as citekey."""
    assert _detect_input_type("/tmp/nonexistent_paper.pdf") == "citekey"


# --- klemma add: citekey mode ---


def _make_mock_ctx(state, embeddings=None, vault=None):
    mock_ctx = MagicMock()
    mock_ctx.state = state
    mock_ctx.embeddings = embeddings
    mock_ctx.config = MagicMock()
    mock_ctx.config.obsidian.notes_folder = ""
    mock_ctx.library = None
    mock_ctx.vault = vault
    mock_ctx.dissertation_context = ""
    mock_ctx.available_tags = []
    mock_ctx.klemma_home = Path("/tmp/klemma_home")
    mock_ctx.project = MagicMock()
    mock_ctx.project.type = "dissertation"
    return mock_ctx


def test_add_citekey_not_found(tmp_path):
    """klemma add <citekey> fails gracefully when source not in DB."""
    db = tmp_path / "state.db"
    sm = StateManager(db)

    mock_ctx = _make_mock_ctx(sm)
    runner = CliRunner()
    with patch("klemma.cli._get_context", return_value=mock_ctx), \
         patch("klemma.cli._init_components", return_value=mock_ctx), \
         patch("klemma.cli.discover_project_root", return_value=tmp_path):
        result = runner.invoke(klemma_cli, ["add", "nonexistent"])

    assert result.exit_code == 0
    assert "not found" in result.output


def test_add_citekey_auto_registers_from_library(tmp_path):
    """klemma add <citekey> auto-registers if source is in Zotero library but not DB."""
    db = tmp_path / "state.db"
    sm = StateManager(db)

    mock_library = MagicMock()
    mock_library.entries = {"paper1": MagicMock()}

    mock_ctx = _make_mock_ctx(sm, vault=None)
    mock_ctx.library = mock_library

    runner = CliRunner()
    with patch("klemma.cli._get_context", return_value=mock_ctx), \
         patch("klemma.cli._init_components", return_value=mock_ctx), \
         patch("klemma.cli.discover_project_root", return_value=tmp_path), \
         patch("klemma.cli._init_ai", side_effect=Exception("no AI")):
        result = runner.invoke(klemma_cli, ["add", "paper1", "--section", "1.1"])

    assert result.exit_code == 0
    assert "Registering" in result.output
    assert sm.get_source("paper1") is not None
    assert "paper1" in sm.get_section_sources("1.1")


def test_add_citekey_assigns_sections(tmp_path):
    """klemma add <citekey> --section 1.1 assigns section in DB."""
    db = tmp_path / "state.db"
    sm = StateManager(db)
    sm.register_sources(["paper1"])
    sm.mark_completed("paper1", "notes/paper1.md")

    mock_ctx = _make_mock_ctx(sm, vault=None)
    runner = CliRunner()
    with patch("klemma.cli._get_context", return_value=mock_ctx), \
         patch("klemma.cli._init_components", return_value=mock_ctx), \
         patch("klemma.cli.discover_project_root", return_value=tmp_path):
        result = runner.invoke(klemma_cli, ["add", "paper1", "--section", "1.1"])

    assert result.exit_code == 0
    assert "sections: 1.1" in result.output
    assert "Done" in result.output

    # Verify section was saved in DB
    sources = sm.get_section_sources("1.1")
    assert "paper1" in sources


def test_add_citekey_multiple_sections(tmp_path):
    """klemma add <citekey> --section 1.1 --section 2.3 assigns both."""
    db = tmp_path / "state.db"
    sm = StateManager(db)
    sm.register_sources(["paper1"])
    sm.mark_completed("paper1", "notes/paper1.md")

    mock_ctx = _make_mock_ctx(sm, vault=None)
    runner = CliRunner()
    with patch("klemma.cli._get_context", return_value=mock_ctx), \
         patch("klemma.cli._init_components", return_value=mock_ctx), \
         patch("klemma.cli.discover_project_root", return_value=tmp_path):
        result = runner.invoke(klemma_cli, [
            "add", "paper1", "--section", "1.1", "--section", "2.3",
        ])

    assert result.exit_code == 0
    assert "1.1" in result.output
    assert "2.3" in result.output
    assert "paper1" in sm.get_section_sources("1.1")
    assert "paper1" in sm.get_section_sources("2.3")


# --- klemma add: URL mode ---


def test_add_url_calls_acquire(tmp_path):
    """klemma add <url> --section 1.1 calls acquire_paper_local."""
    db = tmp_path / "state.db"
    sm = StateManager(db)

    mock_acquire_result = MagicMock()
    mock_acquire_result.status = "ok"
    mock_acquire_result.citekey = "smith2024"
    mock_acquire_result.zotero_added = False

    # Register the source so process can find it
    sm.register_sources(["smith2024"])

    mock_ctx = _make_mock_ctx(sm)
    runner = CliRunner()
    with patch("klemma.cli._get_context", return_value=mock_ctx), \
         patch("klemma.cli._init_components", return_value=mock_ctx), \
         patch("klemma.cli.discover_project_root", return_value=tmp_path), \
         patch("klemma.skills.acquirer.acquire_paper_local", return_value=mock_acquire_result) as mock_acq, \
         patch("klemma.cli._init_ai") as mock_ai:
        # Prevent actual AI processing
        mock_ai.side_effect = Exception("no AI")
        result = runner.invoke(klemma_cli, [
            "add", "https://arxiv.org/abs/1234.5678", "--section", "1.1", "--no-process",
        ])

    assert result.exit_code == 0
    assert "@smith2024" in result.output
    assert mock_acq.called
    # Section should be assigned
    assert "smith2024" in sm.get_section_sources("1.1")


def test_add_url_no_pdf(tmp_path):
    """klemma add <url> with ok_no_pdf skips processing."""
    db = tmp_path / "state.db"
    sm = StateManager(db)

    mock_result = MagicMock()
    mock_result.status = "ok_no_pdf"
    mock_result.citekey = "jones2023"
    mock_result.zotero_added = True

    sm.register_sources(["jones2023"])

    mock_ctx = _make_mock_ctx(sm)
    runner = CliRunner()
    with patch("klemma.cli._get_context", return_value=mock_ctx), \
         patch("klemma.cli._init_components", return_value=mock_ctx), \
         patch("klemma.cli.discover_project_root", return_value=tmp_path), \
         patch("klemma.skills.acquirer.acquire_paper_local", return_value=mock_result):
        result = runner.invoke(klemma_cli, [
            "add", "https://example.com/paper", "--section", "2.1",
        ])

    assert result.exit_code == 0
    assert "No open-access PDF" in result.output
    assert "Done" in result.output


# --- klemma add: flags ---


def test_add_no_process_flag(tmp_path):
    """--no-process skips fragment extraction."""
    db = tmp_path / "state.db"
    sm = StateManager(db)

    mock_result = MagicMock()
    mock_result.status = "ok"
    mock_result.citekey = "paper1"
    mock_result.zotero_added = False

    sm.register_sources(["paper1"])

    mock_ctx = _make_mock_ctx(sm)
    runner = CliRunner()
    with patch("klemma.cli._get_context", return_value=mock_ctx), \
         patch("klemma.cli._init_components", return_value=mock_ctx), \
         patch("klemma.cli.discover_project_root", return_value=tmp_path), \
         patch("klemma.skills.acquirer.acquire_paper_local", return_value=mock_result), \
         patch("klemma.cli._process_single") as mock_proc:
        result = runner.invoke(klemma_cli, [
            "add", "https://example.com/p.pdf", "--no-process",
        ])

    assert result.exit_code == 0
    assert not mock_proc.called


def test_add_no_embed_flag(tmp_path):
    """--no-embed is passed through to _process_single."""
    db = tmp_path / "state.db"
    sm = StateManager(db)
    sm.register_sources(["paper1"])
    # Give it a PDF path so processing is attempted
    with sm._conn() as conn:
        conn.execute("UPDATE sources SET pdf_path='/tmp/test.pdf' WHERE id='paper1'")

    mock_ctx = _make_mock_ctx(sm)
    runner = CliRunner()
    with patch("klemma.cli._get_context", return_value=mock_ctx), \
         patch("klemma.cli._init_components", return_value=mock_ctx), \
         patch("klemma.cli.discover_project_root", return_value=tmp_path), \
         patch("klemma.cli._init_ai", return_value=MagicMock()), \
         patch("klemma.cli._process_single", return_value=(5, "ok")) as mock_proc:
        result = runner.invoke(klemma_cli, [
            "add", "paper1", "--section", "1.1", "--no-embed",
        ])

    assert result.exit_code == 0
    assert mock_proc.called
    _, kwargs = mock_proc.call_args
    assert kwargs["no_embed"] is True


def test_add_help_visible():
    """klemma add --help is accessible."""
    runner = CliRunner()
    result = runner.invoke(klemma_cli, ["add", "--help"])
    assert result.exit_code == 0
    assert "URL, citekey, or local PDF" in result.output
