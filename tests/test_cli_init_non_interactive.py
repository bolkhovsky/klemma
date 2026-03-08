"""Tests for klemma init --non-interactive mode (#54).

Verifies CLI flags (--name, --description, --keywords, --language)
create projects without TTY interaction.

Content fields (type, title, chapters, etc.) now live in KLEMMA.md frontmatter.
config.yaml contains only infrastructure (ai, zotero, obsidian, state).
"""

import yaml
from click.testing import CliRunner

from klemma.cli import main as klemma_cli
from klemma.config import parse_klemma_md


def test_init_with_name_creates_project(monkeypatch, tmp_path):
    """--name flag auto-implies non-interactive and sets title."""
    runner = CliRunner()
    monkeypatch.setenv("KLEMMA_HOME", str(tmp_path / ".klemma_home"))

    with runner.isolated_filesystem():
        result = runner.invoke(klemma_cli, [
            "init", "--type", "paper", "--name", "My Paper",
        ])

    assert result.exit_code == 0
    assert "Initialized klemma paper project" in result.output


def test_init_non_interactive_creates_config_with_values(monkeypatch, tmp_path):
    """CLI flags populate KLEMMA.md frontmatter correctly; config.yaml has no content fields."""
    runner = CliRunner()
    monkeypatch.setenv("KLEMMA_HOME", str(tmp_path / ".klemma_home"))

    with runner.isolated_filesystem() as td:
        result = runner.invoke(klemma_cli, [
            "init", "--type", "paper",
            "--name", "Ice Sheet Analysis",
            "--description", "Modeling ice sheet dynamics",
            "--keywords", "ice sheets, climate, GrIS",
            "--language", "en",
        ])
        assert result.exit_code == 0

        from pathlib import Path

        # Content fields are now in KLEMMA.md frontmatter
        fm, _ = parse_klemma_md(Path(td) / "KLEMMA.md")
        assert fm["type"] == "paper"
        assert fm["title"] == "Ice Sheet Analysis"
        assert fm["description"] == "Modeling ice sheet dynamics"
        assert fm["priority_terms"] == ["ice sheets", "climate", "GrIS"]

        # config.yaml has no project: section (infrastructure only)
        cfg = yaml.safe_load((Path(td) / ".klemma" / "config.yaml").read_text())
        assert "project" not in cfg
        assert cfg["ai"]["language"] == "en"


def test_init_non_interactive_creates_klemma_md(monkeypatch, tmp_path):
    """CLI flags populate KLEMMA.md with title and keywords."""
    runner = CliRunner()
    monkeypatch.setenv("KLEMMA_HOME", str(tmp_path / ".klemma_home"))

    with runner.isolated_filesystem() as td:
        runner.invoke(klemma_cli, [
            "init", "--type", "paper",
            "--name", "My Research Paper",
            "--keywords", "NLP, transformers",
        ])

        from pathlib import Path

        md = (Path(td) / "KLEMMA.md").read_text()
        assert "My Research Paper" in md
        assert "NLP, transformers" in md


def test_init_non_interactive_flag_alias(monkeypatch, tmp_path):
    """--non-interactive is an alias for --no-input."""
    runner = CliRunner()
    monkeypatch.setenv("KLEMMA_HOME", str(tmp_path / ".klemma_home"))

    with runner.isolated_filesystem():
        result = runner.invoke(klemma_cli, [
            "init", "--non-interactive", "--type", "thesis",
        ])

    assert result.exit_code == 0
    assert "Initialized klemma thesis project" in result.output


def test_init_non_interactive_minimal(monkeypatch, tmp_path):
    """--name alone is sufficient for non-interactive init."""
    runner = CliRunner()
    monkeypatch.setenv("KLEMMA_HOME", str(tmp_path / ".klemma_home"))

    with runner.isolated_filesystem() as td:
        result = runner.invoke(klemma_cli, [
            "init", "--name", "Quick Project",
        ])
        assert result.exit_code == 0

        from pathlib import Path

        # Title is in KLEMMA.md frontmatter
        fm, _ = parse_klemma_md(Path(td) / "KLEMMA.md")
        assert fm["title"] == "Quick Project"

        # config.yaml has no project: section; language is in ai:
        cfg = yaml.safe_load((Path(td) / ".klemma" / "config.yaml").read_text())
        assert "project" not in cfg
        assert cfg["ai"]["language"] == "ru"


def test_init_non_interactive_no_wizard_prompts(monkeypatch, tmp_path):
    """Value flags must not trigger interactive prompts."""
    runner = CliRunner()
    monkeypatch.setenv("KLEMMA_HOME", str(tmp_path / ".klemma_home"))

    # Run without TTY input — should succeed without prompting
    with runner.isolated_filesystem():
        result = runner.invoke(klemma_cli, [
            "init", "--type", "paper", "--name", "Test",
        ], input=None)

    assert result.exit_code == 0
    # No wizard output
    assert "Project type" not in result.output
    assert "Project title" not in result.output
