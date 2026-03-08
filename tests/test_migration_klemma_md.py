"""Tests for migrate_content_to_klemma_md() (ADR-013 Step 4)."""

import yaml

from klemma.config import parse_klemma_md
from klemma.setup import migrate_content_to_klemma_md


class TestMigrateContentToKlemmamd:
    """migrate_content_to_klemma_md() moves content fields from config.yaml to KLEMMA.md."""

    def _make_project(self, tmp_path, config_content, klemma_body=None):
        klemma_dir = tmp_path / ".klemma"
        klemma_dir.mkdir()
        (klemma_dir / "config.yaml").write_text(config_content, encoding="utf-8")
        if klemma_body is not None:
            (tmp_path / "KLEMMA.md").write_text(klemma_body, encoding="utf-8")
        return tmp_path

    def test_migrates_project_section(self, tmp_path):
        config = (
            "project:\n"
            "  type: paper\n"
            "  title: My Paper\n"
            "  chapters:\n"
            "    1: Introduction\n"
            "    2: Methods\n"
            "ai:\n"
            "  language: ru\n"
        )
        self._make_project(tmp_path, config, klemma_body="# Body\n\nContent.\n")

        result = migrate_content_to_klemma_md(tmp_path)

        assert "project.type" in result["migrated_fields"]
        assert "project.title" in result["migrated_fields"]
        assert "project.chapters" in result["migrated_fields"]

        fm, body = parse_klemma_md(tmp_path / "KLEMMA.md")
        assert fm["type"] == "paper"
        assert fm["title"] == "My Paper"
        assert fm["chapters"][1] == "Introduction"
        assert "Content." in body

    def test_config_yaml_stripped_of_content(self, tmp_path):
        config = (
            "project:\n"
            "  type: dissertation\n"
            "  title: Diss\n"
            "ai:\n"
            "  language: ru\n"
            "state:\n"
            "  db_path: ./data/klemma.db\n"
        )
        self._make_project(tmp_path, config, klemma_body="# Body\n")

        migrate_content_to_klemma_md(tmp_path)

        raw = yaml.safe_load((tmp_path / ".klemma" / "config.yaml").read_text())
        assert "project" not in raw
        assert "ai" in raw
        assert "state" in raw

    def test_creates_klemma_md_if_missing(self, tmp_path):
        config = "project:\n  type: paper\n  title: New\n"
        self._make_project(tmp_path, config)  # no KLEMMA.md

        migrate_content_to_klemma_md(tmp_path)

        assert (tmp_path / "KLEMMA.md").exists()
        fm, _ = parse_klemma_md(tmp_path / "KLEMMA.md")
        assert fm["title"] == "New"

    def test_preserves_existing_body(self, tmp_path):
        config = "project:\n  type: paper\n  title: T\n"
        existing_body = "# Project Context\n\nExisting user prose.\n"
        self._make_project(tmp_path, config, klemma_body=existing_body)

        migrate_content_to_klemma_md(tmp_path)

        _, body = parse_klemma_md(tmp_path / "KLEMMA.md")
        assert "Existing user prose." in body

    def test_migrates_dissertation_section(self, tmp_path):
        config = (
            "dissertation:\n"
            "  title: My Dissertation\n"
            "  current_section: '2.1'\n"
            "  chapters:\n"
            "    1: Literature Review\n"
        )
        self._make_project(tmp_path, config, klemma_body="# Body\n")

        result = migrate_content_to_klemma_md(tmp_path)

        assert any("dissertation." in f for f in result["migrated_fields"])
        fm, _ = parse_klemma_md(tmp_path / "KLEMMA.md")
        assert fm["title"] == "My Dissertation"
        assert fm["current_focus"] == "2.1"

    def test_no_content_returns_warning(self, tmp_path):
        config = "ai:\n  language: ru\n"
        self._make_project(tmp_path, config, klemma_body="# Body\n")

        result = migrate_content_to_klemma_md(tmp_path)

        assert result["migrated_fields"] == []
        assert any("No content fields" in w for w in result["warnings"])

    def test_backward_compat_unmigrated_project(self, tmp_path):
        """Unmigrated project (content in config.yaml) still resolves ProjectConfig correctly."""
        import os  # noqa: I001
        from klemma.config import resolve_effective_config

        klemma_dir = tmp_path / ".klemma"
        klemma_dir.mkdir()
        (klemma_dir / "config.yaml").write_text(
            "project:\n  type: paper\n  title: Legacy\n  chapters:\n    1: Intro\n",
            encoding="utf-8",
        )
        (tmp_path / "KLEMMA.md").write_text("# Body only\n", encoding="utf-8")

        system_dir = tmp_path / "system"
        system_dir.mkdir()
        (system_dir / "config.yaml").write_text("", encoding="utf-8")

        import warnings
        old_home = os.environ.get("KLEMMA_HOME")
        os.environ["KLEMMA_HOME"] = str(system_dir)
        try:
            with warnings.catch_warnings(record=True):
                _, project, _ = resolve_effective_config([tmp_path])
        finally:
            if old_home:
                os.environ["KLEMMA_HOME"] = old_home
            else:
                os.environ.pop("KLEMMA_HOME", None)

        assert project.type == "paper"
        assert project.title == "Legacy"
