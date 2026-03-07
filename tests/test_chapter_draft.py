"""Tests for _load_chapter_draft project_root-first convention."""

from unittest.mock import MagicMock

import pytest

from klemma.skills.researcher import _load_chapter_draft


@pytest.fixture
def config():
    cfg = MagicMock()
    cfg.dissertation.chapter_draft_pattern = "Глава_{chapter}"
    return cfg


@pytest.fixture
def vault():
    return MagicMock()


class TestLoadChapterDraft:
    """Chapter draft loading: project_root first, vault fallback."""

    def test_reads_md_from_project_root(self, tmp_path, config, vault):
        draft = tmp_path / "Глава_1.md"
        draft.write_text("# Глава 1\nТекст черновика", encoding="utf-8")

        result = _load_chapter_draft(1, config, vault, project_root=tmp_path)

        assert result == "# Глава 1\nТекст черновика"
        vault.read_note.assert_not_called()

    def test_reads_tex_from_project_root(self, tmp_path, config, vault):
        draft = tmp_path / "Глава_2.tex"
        draft.write_text("\\chapter{Глава 2}", encoding="utf-8")

        result = _load_chapter_draft(2, config, vault, project_root=tmp_path)

        assert result == "\\chapter{Глава 2}"
        vault.read_note.assert_not_called()

    def test_reads_bare_name_from_project_root(self, tmp_path, config, vault):
        draft = tmp_path / "Глава_3"
        draft.write_text("bare file", encoding="utf-8")

        result = _load_chapter_draft(3, config, vault, project_root=tmp_path)

        assert result == "bare file"
        vault.read_note.assert_not_called()

    def test_md_preferred_over_tex(self, tmp_path, config, vault):
        (tmp_path / "Глава_1.md").write_text("markdown", encoding="utf-8")
        (tmp_path / "Глава_1.tex").write_text("latex", encoding="utf-8")

        result = _load_chapter_draft(1, config, vault, project_root=tmp_path)

        assert result == "markdown"

    def test_no_vault_fallback_with_project_root(self, tmp_path, config, vault):
        """When project_root is set but draft not found, return None — don't
        fall back to vault (avoids loading parent's draft in child projects)."""
        result = _load_chapter_draft(1, config, vault, project_root=tmp_path)

        assert result is None
        vault.read_note.assert_not_called()

    def test_vault_only_when_no_project_root(self, config, vault):
        vault.read_note.return_value = "from vault"

        result = _load_chapter_draft(1, config, vault)

        assert result == "from vault"
        vault.read_note.assert_called_once_with("Глава_1")

    def test_returns_none_when_not_found_anywhere(self, config, vault):
        """No project_root, vault returns None — result is None."""
        vault.read_note.return_value = None

        result = _load_chapter_draft(1, config, vault)

        assert result is None

    def test_uses_project_pattern(self, tmp_path, config, vault):
        project = MagicMock()
        project.chapter_draft_pattern = "Chapter_{chapter}"
        (tmp_path / "Chapter_1.md").write_text("english", encoding="utf-8")

        result = _load_chapter_draft(1, config, vault, project=project, project_root=tmp_path)

        assert result == "english"
