"""Tests for parse_klemma_md() and save_klemma_md() (ADR-013)."""

from klemma.config import parse_klemma_md, save_klemma_md


class TestParseKlemmamd:
    """parse_klemma_md() — split YAML frontmatter from markdown body."""

    def test_no_frontmatter_returns_empty_dict(self, tmp_path):
        p = tmp_path / "KLEMMA.md"
        p.write_text("# Project Context\n\nSome prose.\n", encoding="utf-8")
        fm, body = parse_klemma_md(p)
        assert fm == {}
        assert "Some prose" in body

    def test_with_frontmatter_parses_correctly(self, tmp_path):
        p = tmp_path / "KLEMMA.md"
        p.write_text(
            "---\ntitle: My Project\ntype: dissertation\n---\n# Body\n",
            encoding="utf-8",
        )
        fm, body = parse_klemma_md(p)
        assert fm["title"] == "My Project"
        assert fm["type"] == "dissertation"
        assert "# Body" in body

    def test_integer_keys_in_chapters(self, tmp_path):
        p = tmp_path / "KLEMMA.md"
        p.write_text(
            "---\nchapters:\n  1: Introduction\n  2: Methodology\n---\n",
            encoding="utf-8",
        )
        fm, _ = parse_klemma_md(p)
        assert isinstance(list(fm["chapters"].keys())[0], int)
        assert fm["chapters"][1] == "Introduction"
        assert fm["chapters"][2] == "Methodology"

    def test_missing_file_returns_empty(self, tmp_path):
        p = tmp_path / "nonexistent.md"
        fm, body = parse_klemma_md(p)
        assert fm == {}
        assert body == ""

    def test_empty_file_returns_empty(self, tmp_path):
        p = tmp_path / "KLEMMA.md"
        p.write_text("", encoding="utf-8")
        fm, body = parse_klemma_md(p)
        assert fm == {}
        assert body == ""

    def test_frontmatter_only(self, tmp_path):
        p = tmp_path / "KLEMMA.md"
        p.write_text("---\ntitle: Minimal\n---\n", encoding="utf-8")
        fm, body = parse_klemma_md(p)
        assert fm["title"] == "Minimal"
        assert body == ""

    def test_midfile_dashes_not_parsed_as_frontmatter(self, tmp_path):
        """Horizontal rules (---) in the body are NOT parsed as frontmatter."""
        p = tmp_path / "KLEMMA.md"
        p.write_text("# Body\n\n---\n\nMore text\n", encoding="utf-8")
        fm, body = parse_klemma_md(p)
        assert fm == {}
        assert "# Body" in body

    def test_roundtrip(self, tmp_path):
        p = tmp_path / "KLEMMA.md"
        fm_in = {"type": "paper", "title": "Test", "chapters": {1: "Intro", 2: "Methods"}}
        body_in = "# Body\n\nSome text.\n"
        save_klemma_md(p, fm_in, body_in)
        fm_out, body_out = parse_klemma_md(p)
        assert fm_out["type"] == "paper"
        assert fm_out["title"] == "Test"
        assert isinstance(list(fm_out["chapters"].keys())[0], int)
        assert body_out == body_in


class TestSaveKlemmamd:
    """save_klemma_md() — write frontmatter + body."""

    def test_creates_file_with_correct_format(self, tmp_path):
        p = tmp_path / "KLEMMA.md"
        save_klemma_md(p, {"title": "T"}, "# Body\n")
        text = p.read_text(encoding="utf-8")
        assert text.startswith("---\n")
        assert "title: T\n" in text
        assert "---\n# Body\n" in text

    def test_overwrites_existing_file(self, tmp_path):
        p = tmp_path / "KLEMMA.md"
        p.write_text("old content", encoding="utf-8")
        save_klemma_md(p, {"v": 2}, "new body")
        text = p.read_text(encoding="utf-8")
        assert "old content" not in text
        assert "v: 2" in text


class TestResolveEffectiveConfigFrontmatter:
    """resolve_effective_config() reads KLEMMA.md frontmatter as ProjectConfig."""

    def test_klemma_md_frontmatter_wins_over_config_yaml(self, tmp_path):
        from klemma.config import resolve_effective_config

        klemma_dir = tmp_path / ".klemma"
        klemma_dir.mkdir()
        (klemma_dir / "config.yaml").write_text(
            "project:\n  type: paper\n  title: From Config\n",
            encoding="utf-8",
        )
        (tmp_path / "KLEMMA.md").write_text(
            "---\ntype: dissertation\ntitle: From KLEMMA.md\nchapters:\n  1: Intro\n---\n# Body\n",
            encoding="utf-8",
        )
        system_dir = tmp_path / "system"
        system_dir.mkdir()
        (system_dir / "config.yaml").write_text("ai:\n  model: sonnet\n", encoding="utf-8")

        import os
        old_home = os.environ.get("KLEMMA_HOME")
        os.environ["KLEMMA_HOME"] = str(system_dir)
        try:
            _, project, _ = resolve_effective_config([tmp_path])
        finally:
            if old_home:
                os.environ["KLEMMA_HOME"] = old_home
            else:
                os.environ.pop("KLEMMA_HOME", None)

        assert project.type == "dissertation"
        assert project.title == "From KLEMMA.md"
        assert 1 in project.chapters

    def test_no_frontmatter_falls_back_to_config_yaml(self, tmp_path):
        from klemma.config import resolve_effective_config

        klemma_dir = tmp_path / ".klemma"
        klemma_dir.mkdir()
        (klemma_dir / "config.yaml").write_text(
            "project:\n  type: paper\n  title: Config Title\n",
            encoding="utf-8",
        )
        # KLEMMA.md without frontmatter
        (tmp_path / "KLEMMA.md").write_text("# Body only\n", encoding="utf-8")

        system_dir = tmp_path / "system"
        system_dir.mkdir()
        (system_dir / "config.yaml").write_text("", encoding="utf-8")

        import os
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
        assert project.title == "Config Title"


class TestLoadProjectContextStripsYAML:
    """load_project_context() strips YAML frontmatter — AI sees only prose body."""

    def test_body_returned_not_frontmatter(self, tmp_path):
        from klemma.config import load_project_context

        (tmp_path / ".klemma").mkdir()
        (tmp_path / "KLEMMA.md").write_text(
            "---\ntitle: Secret Config\ntype: paper\n---\n# Project Context\n\nMy research.\n",
            encoding="utf-8",
        )
        context = load_project_context([tmp_path])
        assert "My research" in context
        assert "Secret Config" not in context  # frontmatter not in AI context
        assert "---" not in context
