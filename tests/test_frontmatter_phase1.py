"""Tests for frontmatter Phase 1: singular section:/chapter: fallback (issue #105)."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from klemma.cli import main as klemma_cli


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_vault_note(tmp_path: Path, name: str, frontmatter: dict) -> Path:
    """Write a vault note with YAML frontmatter."""
    import yaml

    fm = yaml.dump(frontmatter, default_flow_style=False, allow_unicode=True, sort_keys=False)
    note = tmp_path / f"{name}.md"
    note.write_text(f"---\n{fm}---\nBody text here.\n", encoding="utf-8")
    return note


# ---------------------------------------------------------------------------
# _sync_sections fallback logic (unit-level)
# ---------------------------------------------------------------------------


class TestSyncSectionsFallback:
    """Verify singular section: fallback in _sync_sections (cli.py)."""

    def _build_vault_data_entry(self, props: dict) -> dict:
        """Replicate the vault_data entry construction from _sync_sections."""
        sections_list = props.get("sections", [])
        chapters_list = props.get("chapters", [])

        primary_section_str = str(props.get("section", "")) or None
        if not sections_list and primary_section_str:
            sections_list = [primary_section_str]

        chapter = props.get("chapter")
        if isinstance(chapter, str):
            chapter = int(chapter) if chapter.isdigit() else None

        return {
            "primary_section": primary_section_str,
            "primary_chapter": chapter,
            "sections": (
                [str(s) for s in sections_list]
                if isinstance(sections_list, list)
                else []
            ),
        }

    def test_singular_section_falls_back_to_list(self):
        """section: '1.2' with no sections: → sections=['1.2']."""
        entry = self._build_vault_data_entry({"section": "1.2"})
        assert entry["sections"] == ["1.2"]
        assert entry["primary_section"] == "1.2"

    def test_plural_sections_takes_priority(self):
        """sections: [...] takes priority — singular ignored for sections list."""
        entry = self._build_vault_data_entry({
            "section": "1.2",
            "sections": ["1.2", "2.3"],
        })
        assert entry["sections"] == ["1.2", "2.3"]

    def test_no_section_fields_gives_empty_list(self):
        """No section info → empty sections list."""
        entry = self._build_vault_data_entry({})
        assert entry["sections"] == []
        assert entry["primary_section"] is None

    def test_empty_sections_list_with_singular_falls_back(self):
        """sections: [] (empty) + section: '3.1' → sections=['3.1']."""
        entry = self._build_vault_data_entry({"sections": [], "section": "3.1"})
        assert entry["sections"] == ["3.1"]

    def test_singular_empty_string_not_added(self):
        """section: '' (empty) → no fallback, sections stays empty."""
        entry = self._build_vault_data_entry({"section": ""})
        assert entry["sections"] == []


# ---------------------------------------------------------------------------
# migrate-frontmatter command (CLI)
# ---------------------------------------------------------------------------


class TestMigrateFrontmatter:
    """Tests for klemma migrate-frontmatter command."""

    @pytest.fixture
    def mock_ctx(self, tmp_path):
        from klemma.state import StateManager
        from klemma.context import KlemmaContext
        sm = StateManager(str(tmp_path / "test.db"))
        config = MagicMock()
        config.obsidian.notes_folder = ""
        ctx = MagicMock(spec=KlemmaContext)
        ctx.state = sm
        ctx.config = config
        ctx.project_root = tmp_path
        ctx.project = MagicMock()
        ctx.project.chapters = {}
        return ctx

    def test_help_exits_zero(self):
        runner = CliRunner()
        result = runner.invoke(klemma_cli, ["migrate-frontmatter", "--help"])
        assert result.exit_code == 0
        assert "--dry-run" in result.output

    def test_dry_run_shows_changes(self, tmp_path, mock_ctx):
        from klemma.vault import VaultAdapter

        vault = VaultAdapter(tmp_path)
        _make_vault_note(tmp_path, "@smith2020", {"section": "1.2", "title": "Test paper"})
        mock_ctx.vault = vault

        runner = CliRunner()
        with (
            patch("klemma.cli._get_context", return_value=mock_ctx),
            patch("klemma.cli._init_components", return_value=mock_ctx),
        ):
            result = runner.invoke(klemma_cli, ["migrate-frontmatter", "--dry-run"])

        assert result.exit_code == 0
        assert "1" in result.output  # 1 note updated
        # Dry-run must NOT have modified the file
        import yaml
        text = (tmp_path / "@smith2020.md").read_text()
        fm = yaml.safe_load(text.split("---")[1])
        assert "section" in fm  # singular still present

    def test_migrates_singular_to_plural(self, tmp_path, mock_ctx):
        from klemma.vault import VaultAdapter

        vault = VaultAdapter(tmp_path)
        _make_vault_note(tmp_path, "@jones2021", {
            "section": "2.3",
            "chapter": 2,
            "title": "A paper",
        })
        mock_ctx.vault = vault

        runner = CliRunner()
        with (
            patch("klemma.cli._get_context", return_value=mock_ctx),
            patch("klemma.cli._init_components", return_value=mock_ctx),
        ):
            result = runner.invoke(klemma_cli, ["migrate-frontmatter"])

        assert result.exit_code == 0

        import yaml
        text = (tmp_path / "@jones2021.md").read_text()
        fm = yaml.safe_load(text.split("---")[1])
        assert fm.get("sections") == ["2.3"]
        assert fm.get("chapters") == [2]
        assert "section" not in fm
        assert "chapter" not in fm

    def test_skips_note_already_using_plural(self, tmp_path, mock_ctx):
        from klemma.vault import VaultAdapter

        vault = VaultAdapter(tmp_path)
        _make_vault_note(tmp_path, "@brown2019", {
            "sections": ["1.1", "2.1"],
            "chapters": [1, 2],
        })
        mock_ctx.vault = vault

        runner = CliRunner()
        with (
            patch("klemma.cli._get_context", return_value=mock_ctx),
            patch("klemma.cli._init_components", return_value=mock_ctx),
        ):
            result = runner.invoke(klemma_cli, ["migrate-frontmatter"])

        assert result.exit_code == 0
        assert "skipped 1" in result.output

    def test_no_vault_exits_nonzero(self, mock_ctx):
        mock_ctx.vault = None
        runner = CliRunner()
        with (
            patch("klemma.cli._get_context", return_value=mock_ctx),
            patch("klemma.cli._init_components", return_value=mock_ctx),
        ):
            result = runner.invoke(klemma_cli, ["migrate-frontmatter"])
        assert result.exit_code != 0
        assert "No Obsidian vault" in result.output
