"""Smoke tests for `klemma migrate-library` command (ADR-014 Phase 1C)."""

import sqlite3
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from click.testing import CliRunner

from klemma.cli import main as klemma_cli
from klemma.stores import LocalPaperStore, LocalProjectStore, LocalUserLibrary


def _make_mono_db(path: Path) -> None:
    """Create a minimal monolithic klemma.db with one source + one fragment."""
    conn = sqlite3.connect(str(path))
    conn.executescript("""
        CREATE TABLE sources (
            id TEXT PRIMARY KEY,
            title TEXT,
            authors TEXT,
            year INTEGER,
            abstract TEXT,
            doi TEXT,
            status TEXT DEFAULT 'completed',
            pdf_path TEXT,
            quality_score INTEGER
        );
        CREATE TABLE fragments (
            source_id TEXT,
            fragment_text TEXT,
            fragment_type TEXT,
            page_number INTEGER,
            citation_intent TEXT
        );
        CREATE TABLE source_sections (
            source_id TEXT,
            section TEXT
        );
        INSERT INTO sources VALUES (
            'smith2022', 'A Great Paper', 'Smith, J.', 2022,
            'Abstract here.', '10.1234/test', 'completed', NULL, 4
        );  -- quality_score=4
        INSERT INTO fragments VALUES (
            'smith2022', 'Key finding text.', 'key_idea', 3, 'background'
        );
        INSERT INTO source_sections VALUES ('smith2022', '1.1');
    """)
    conn.commit()
    conn.close()


@pytest.fixture
def mock_kctx(tmp_path):
    system_home = tmp_path / "system_home"
    system_home.mkdir()
    klemma_home = tmp_path / "project" / ".klemma"
    (klemma_home / "data").mkdir(parents=True)
    mono_db = klemma_home / "data" / "klemma.db"
    _make_mono_db(mono_db)

    ctx = MagicMock()
    ctx.system_home = system_home
    ctx.klemma_home = klemma_home
    ctx.config.library_db_path = None  # use default system_home/library.db
    return ctx


def test_dry_run_shows_stats(mock_kctx):
    runner = CliRunner()
    with runner.isolated_filesystem():
        with pytest.MonkeyPatch().context() as mp:
            mp.setattr("klemma.commands.manage._get_context", lambda _: mock_kctx)
            result = runner.invoke(klemma_cli, ["migrate-library"])
    assert result.exit_code == 0
    assert "1" in result.output  # 1 source
    assert "Dry run" in result.output
    assert "--apply" in result.output


def test_dry_run_does_not_modify_library(mock_kctx):
    runner = CliRunner()
    lib_db = mock_kctx.system_home / "library.db"
    with runner.isolated_filesystem():
        with pytest.MonkeyPatch().context() as mp:
            mp.setattr("klemma.commands.manage._get_context", lambda _: mock_kctx)
            runner.invoke(klemma_cli, ["migrate-library"])
    # library.db should not exist after dry-run
    assert not lib_db.exists()


def test_apply_migrates_source_to_library(mock_kctx):
    runner = CliRunner()
    with runner.isolated_filesystem():
        with pytest.MonkeyPatch().context() as mp:
            mp.setattr("klemma.commands.manage._get_context", lambda _: mock_kctx)
            result = runner.invoke(klemma_cli, ["migrate-library", "--apply"])
    assert result.exit_code == 0, result.output
    assert "Migration complete" in result.output

    lib_db = mock_kctx.system_home / "library.db"
    assert lib_db.exists()

    paper_store = LocalPaperStore(lib_db)
    paper = paper_store.find_paper(pdf_hash="migrated:smith2022")
    assert paper is not None
    assert paper.title == "A Great Paper"


def test_apply_registers_citekey_in_user_library(mock_kctx):
    runner = CliRunner()
    with runner.isolated_filesystem():
        with pytest.MonkeyPatch().context() as mp:
            mp.setattr("klemma.commands.manage._get_context", lambda _: mock_kctx)
            runner.invoke(klemma_cli, ["migrate-library", "--apply"])

    lib_db = mock_kctx.system_home / "library.db"
    user_lib = LocalUserLibrary(lib_db)
    src = user_lib.get_source_by_citekey("smith2022")
    assert src is not None
    assert src.status == "completed"
    assert src.quality_score == 4


def test_apply_migrates_section_to_project_store(mock_kctx):
    runner = CliRunner()
    with runner.isolated_filesystem():
        with pytest.MonkeyPatch().context() as mp:
            mp.setattr("klemma.commands.manage._get_context", lambda _: mock_kctx)
            runner.invoke(klemma_cli, ["migrate-library", "--apply"])

    project_db = mock_kctx.klemma_home / "data" / "project.db"
    project_store = LocalProjectStore(project_db)
    sections = project_store.get_source_sections("smith2022")
    assert "1.1" in sections


def test_apply_creates_backup(mock_kctx):
    runner = CliRunner()
    mono_db = mock_kctx.klemma_home / "data" / "klemma.db"
    bak = mono_db.with_suffix(".db.bak")
    with runner.isolated_filesystem():
        with pytest.MonkeyPatch().context() as mp:
            mp.setattr("klemma.commands.manage._get_context", lambda _: mock_kctx)
            runner.invoke(klemma_cli, ["migrate-library", "--apply"])
    assert bak.exists()


def test_apply_warns_about_synthetic_hash(mock_kctx):
    runner = CliRunner()
    with runner.isolated_filesystem():
        with pytest.MonkeyPatch().context() as mp:
            mp.setattr("klemma.commands.manage._get_context", lambda _: mock_kctx)
            result = runner.invoke(klemma_cli, ["migrate-library", "--apply"])
    assert "citekey-based deduplication" in result.output
    assert "klemma process --force" in result.output


def test_no_monolithic_db_exits_cleanly(mock_kctx):
    """If klemma.db doesn't exist, command exits cleanly with a message."""
    (mock_kctx.klemma_home / "data" / "klemma.db").unlink()
    runner = CliRunner()
    with runner.isolated_filesystem():
        with pytest.MonkeyPatch().context() as mp:
            mp.setattr("klemma.commands.manage._get_context", lambda _: mock_kctx)
            result = runner.invoke(klemma_cli, ["migrate-library"])
    assert result.exit_code == 0
    assert "No monolithic DB" in result.output
