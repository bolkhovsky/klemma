from pathlib import Path

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
