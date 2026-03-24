"""Tests for project discovery."""

from __future__ import annotations

import pytest

from klemma_cli.project import discover_project_root, ensure_project_root, get_project_name


class TestDiscoverProjectRoot:
    def test_finds_klemma_dir(self, tmp_path):
        (tmp_path / ".klemma").mkdir()
        result = discover_project_root(tmp_path)
        assert result == tmp_path

    def test_finds_parent_klemma_dir(self, tmp_path):
        (tmp_path / ".klemma").mkdir()
        sub = tmp_path / "sub" / "deep"
        sub.mkdir(parents=True)
        result = discover_project_root(sub)
        assert result == tmp_path

    def test_returns_none_when_not_found(self, tmp_path):
        result = discover_project_root(tmp_path)
        assert result is None


class TestEnsureProjectRoot:
    def test_raises_when_not_found(self, tmp_path):
        with pytest.raises(FileNotFoundError, match="No .klemma/ directory"):
            ensure_project_root(tmp_path)

    def test_returns_path_when_found(self, tmp_path):
        (tmp_path / ".klemma").mkdir()
        result = ensure_project_root(tmp_path)
        assert result == tmp_path


class TestGetProjectName:
    def test_from_klemma_md_frontmatter(self, tmp_path):
        (tmp_path / "KLEMMA.md").write_text("---\nname: My Research\n---\n# Content\n")
        assert get_project_name(tmp_path) == "My Research"

    def test_falls_back_to_dir_name(self, tmp_path):
        assert get_project_name(tmp_path) == tmp_path.name

    def test_handles_missing_klemma_md(self, tmp_path):
        assert get_project_name(tmp_path) == tmp_path.name
