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


def test_init_creates_draft_scaffold_dissertation(monkeypatch, tmp_path):
    """klemma init creates draft/chapter_N.md from chapters in KLEMMA.md (ADR-016)."""
    from pathlib import Path

    runner = CliRunner()
    monkeypatch.setenv("KLEMMA_HOME", str(tmp_path / ".klemma_home"))

    with runner.isolated_filesystem() as td:
        result = runner.invoke(klemma_cli, [
            "init", "--type", "dissertation", "--name", "My Dissertation",
        ])
        assert result.exit_code == 0

        proj = Path(td)
        draft_dir = proj / "draft"
        assert draft_dir.exists(), "draft/ directory should be created"

        # Default dissertation has chapters 1-4 → chapter_1.md .. chapter_4.md
        created = sorted(p.name for p in draft_dir.glob("*.md"))
        assert len(created) >= 1, f"Expected at least one draft file, got: {created}"
        assert all(name.startswith("chapter_") for name in created), f"Unexpected files: {created}"

        # Verify file format: ## heading, no # top-level heading (ADR-016)
        first = (draft_dir / created[0]).read_text(encoding="utf-8")
        assert first.startswith("## "), "Draft file must start with ## heading (ADR-016)"
        assert not first.startswith("# "), "Draft file must NOT have # top-level heading"

        # sections list in KLEMMA.md frontmatter matches created files
        fm, _ = parse_klemma_md(proj / "KLEMMA.md")
        section_ids = {s["id"] for s in fm.get("sections", [])}
        assert section_ids == {p.stem for p in draft_dir.glob("*.md")}, (
            "sections in KLEMMA.md must match created draft files"
        )


def test_init_creates_draft_scaffold_paper(monkeypatch, tmp_path):
    """klemma init paper creates draft/paper.md (ADR-016)."""
    from pathlib import Path

    runner = CliRunner()
    monkeypatch.setenv("KLEMMA_HOME", str(tmp_path / ".klemma_home"))

    with runner.isolated_filesystem() as td:
        result = runner.invoke(klemma_cli, [
            "init", "--type", "paper", "--name", "My Paper",
        ])
        assert result.exit_code == 0

        paper_file = Path(td) / "draft" / "paper.md"
        assert paper_file.exists(), "draft/paper.md should be created for paper projects"

        content = paper_file.read_text(encoding="utf-8")
        assert content.startswith("## "), "paper.md must start with ## heading"


def test_init_draft_scaffold_idempotent(monkeypatch, tmp_path):
    """Running klemma init twice does not overwrite existing draft files."""
    from pathlib import Path

    runner = CliRunner()
    monkeypatch.setenv("KLEMMA_HOME", str(tmp_path / ".klemma_home"))

    with runner.isolated_filesystem() as td:
        runner.invoke(klemma_cli, ["init", "--type", "dissertation", "--name", "Test"])
        proj = Path(td)

        # Write custom content to a draft file
        first_file = sorted((proj / "draft").glob("*.md"))[0]
        first_file.write_text("## Custom Content\n\nMy text.\n", encoding="utf-8")

        # Re-init (KLEMMA.md already exists → skipped, draft files already exist → skipped)
        runner.invoke(klemma_cli, ["init", "--type", "dissertation", "--name", "Test"])

        # Custom content preserved
        assert first_file.read_text(encoding="utf-8") == "## Custom Content\n\nMy text.\n"


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
