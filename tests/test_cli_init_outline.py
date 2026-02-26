from click.testing import CliRunner

from klemma.cli import main as klemma_cli


def test_init_outline_skips_when_ai_missing(monkeypatch, tmp_path):
    runner = CliRunner()
    monkeypatch.setenv("KLEMMA_HOME", str(tmp_path / ".klemma_home"))

    def _boom(_cfg):
        raise RuntimeError("AI not configured")

    monkeypatch.setattr("klemma.cli._init_ai", _boom)

    with runner.isolated_filesystem():
        result = runner.invoke(klemma_cli, ["init", "--no-input", "--outline"])

    assert result.exit_code == 0
    assert "Skipping outline: AI backend not configured." in result.output


def test_init_without_outline_does_not_call_ai(monkeypatch, tmp_path):
    runner = CliRunner()
    monkeypatch.setenv("KLEMMA_HOME", str(tmp_path / ".klemma_home"))
    called = []

    def _boom(_cfg):
        called.append(True)
        raise RuntimeError("should not be called")

    monkeypatch.setattr("klemma.cli._init_ai", _boom)

    with runner.isolated_filesystem():
        result = runner.invoke(klemma_cli, ["init", "--no-input"])

    assert result.exit_code == 0
    assert called == []
