"""Tests for Git-style project discovery, config merging, and context aggregation."""

from pathlib import Path

import yaml

# --- Discovery tests ---


class TestDiscoverProjectRoot:
    def test_finds_klemma_dir(self, tmp_path):
        (tmp_path / ".klemma").mkdir()
        from klemma.config import discover_project_root

        result = discover_project_root(tmp_path)
        assert result == tmp_path

    def test_finds_klemma_dir_from_subdirectory(self, tmp_path):
        (tmp_path / ".klemma").mkdir()
        subdir = tmp_path / "src" / "analysis"
        subdir.mkdir(parents=True)
        from klemma.config import discover_project_root

        result = discover_project_root(subdir)
        assert result == tmp_path

    def test_returns_none_when_no_project(self, tmp_path):
        subdir = tmp_path / "empty" / "dir"
        subdir.mkdir(parents=True)
        from klemma.config import discover_project_root

        result = discover_project_root(subdir)
        assert result is None

    def test_finds_nearest_klemma_dir(self, tmp_path):
        """When nested, finds the nearest (child) .klemma/ first."""
        parent = tmp_path / "thesis"
        child = parent / "paper1"
        (parent / ".klemma").mkdir(parents=True)
        (child / ".klemma").mkdir(parents=True)

        from klemma.config import discover_project_root

        result = discover_project_root(child)
        assert result == child

    def test_uses_cwd_when_no_start(self, tmp_path, monkeypatch):
        (tmp_path / ".klemma").mkdir()
        monkeypatch.chdir(tmp_path)
        from klemma.config import discover_project_root

        result = discover_project_root()
        assert result == tmp_path


    def test_skips_system_home_directory(self, tmp_path, monkeypatch):
        """discover_project_root must not treat ~/.klemma/ (system home) as a project."""
        # Simulate: tmp_path acts as $HOME, has .klemma/ (system dir)
        system_home = tmp_path / ".klemma"
        system_home.mkdir()
        # Start search from a subdirectory under tmp_path (no project .klemma/)
        subdir = tmp_path / "projects" / "myproject"
        subdir.mkdir(parents=True)

        from klemma.config import discover_project_root

        monkeypatch.setattr("klemma.config.get_system_home", lambda: system_home)

        result = discover_project_root(subdir)
        assert result is None  # should NOT find tmp_path as a project

    def test_finds_project_even_when_system_home_exists(self, tmp_path, monkeypatch):
        """A real project .klemma/ should still be found even with system home present."""
        system_home = tmp_path / ".klemma"
        system_home.mkdir()
        # Create a real project below
        project = tmp_path / "projects" / "diss"
        (project / ".klemma").mkdir(parents=True)

        from klemma.config import discover_project_root

        monkeypatch.setattr("klemma.config.get_system_home", lambda: system_home)

        result = discover_project_root(project)
        assert result == project


class TestDiscoverProjectChain:
    def test_single_project(self, tmp_path):
        (tmp_path / ".klemma").mkdir()
        from klemma.config import discover_project_chain

        chain = discover_project_chain(tmp_path)
        assert chain == [tmp_path]

    def test_nested_projects(self, tmp_path):
        parent = tmp_path / "thesis"
        child = parent / "paper1"
        (parent / ".klemma").mkdir(parents=True)
        (child / ".klemma").mkdir(parents=True)

        from klemma.config import discover_project_chain

        chain = discover_project_chain(child)
        assert chain == [child, parent]

    def test_deeply_nested_max_depth(self, tmp_path):
        """Max 3 levels of nesting."""
        l1 = tmp_path / "l1"
        l2 = l1 / "l2"
        l3 = l2 / "l3"
        l4 = l3 / "l4"
        for d in [l1, l2, l3, l4]:
            (d / ".klemma").mkdir(parents=True)

        from klemma.config import discover_project_chain

        chain = discover_project_chain(l4)
        assert len(chain) == 3  # max depth
        assert chain[0] == l4  # child first

    def test_no_project_returns_empty(self, tmp_path):
        subdir = tmp_path / "no_project"
        subdir.mkdir()
        from klemma.config import discover_project_chain

        chain = discover_project_chain(subdir)
        assert chain == []


# --- Config merging tests ---


def _write_config(path: Path, data: dict):
    """Helper: write YAML config."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        yaml.dump(data, f)


class TestResolveEffectiveConfig:
    def test_single_project(self, tmp_path):
        _write_config(tmp_path / ".klemma" / "config.yaml", {
            "obsidian": {"vault_path": "/vault"},
            "project": {"type": "dissertation", "title": "Test"},
        })
        from klemma.config import resolve_effective_config

        cfg, project, root = resolve_effective_config([tmp_path])
        assert project.title == "Test"
        assert cfg.obsidian.vault_path == "/vault"
        assert root == tmp_path

    def test_child_inherits_shared_from_parent(self, tmp_path):
        """Child inherits vault, zotero, ai, mcp from parent."""
        parent = tmp_path / "thesis"
        child = parent / "paper1"

        _write_config(parent / ".klemma" / "config.yaml", {
            "obsidian": {"vault_path": "/shared_vault", "notes_folder": "Refs"},
            "zotero": {"library_json": "/shared_bbt.json"},
            "ai": {"model": "opus"},
        })
        _write_config(child / ".klemma" / "config.yaml", {
            "project": {"type": "paper", "title": "Paper 1"},
        })

        from klemma.config import resolve_effective_config

        cfg, project, root = resolve_effective_config([child, parent])
        assert project.title == "Paper 1"
        assert cfg.obsidian.vault_path == "/shared_vault"
        assert cfg.zotero.library_json == "/shared_bbt.json"
        assert cfg.ai.model == "opus"
        assert root == child

    def test_child_overrides_shared(self, tmp_path):
        """Child can override inherited values."""
        parent = tmp_path / "thesis"
        child = parent / "paper1"

        _write_config(parent / ".klemma" / "config.yaml", {
            "obsidian": {"vault_path": "/parent_vault"},
            "ai": {"model": "opus"},
        })
        _write_config(child / ".klemma" / "config.yaml", {
            "obsidian": {"vault_path": "/child_vault"},
            "project": {"type": "paper", "title": "Paper 1"},
        })

        from klemma.config import resolve_effective_config

        cfg, _, _ = resolve_effective_config([child, parent])
        assert cfg.obsidian.vault_path == "/child_vault"
        assert cfg.ai.model == "opus"  # inherited

    def test_project_not_inherited(self, tmp_path):
        """project:, tags:, state: are NOT inherited from parent."""
        parent = tmp_path / "thesis"
        child = parent / "paper1"

        _write_config(parent / ".klemma" / "config.yaml", {
            "obsidian": {"vault_path": "/vault"},
            "project": {"type": "dissertation", "title": "Thesis", "chapters": {1: "Ch1", 2: "Ch2"}},
            "state": {"db_path": "./data/thesis.db"},
        })
        _write_config(child / ".klemma" / "config.yaml", {
            "project": {"type": "paper", "title": "Paper"},
        })

        from klemma.config import resolve_effective_config

        cfg, project, _ = resolve_effective_config([child, parent])
        # Project should be from child, not parent
        assert project.title == "Paper"
        assert project.type == "paper"
        assert len(project.chapters) == 0  # not inherited
        # state is also not inherited (uses default)
        assert cfg.state.db_path == "./data/klemma.db"

    def test_system_provides_defaults(self, tmp_path, monkeypatch):
        """System config provides defaults for unset keys."""
        system_home = tmp_path / "system"
        system_home.mkdir()
        _write_config(system_home / "config.yaml", {
            "ai": {"model": "haiku", "language": "en"},
        })
        monkeypatch.setenv("KLEMMA_HOME", str(system_home))

        project = tmp_path / "project"
        _write_config(project / ".klemma" / "config.yaml", {
            "obsidian": {"vault_path": "/vault"},
            "project": {"type": "paper", "title": "Test"},
        })

        from klemma.config import resolve_effective_config

        cfg, _, _ = resolve_effective_config([project])
        # AI from system defaults, overridden by project
        assert cfg.ai.model == "haiku"  # from system (project didn't set ai)

    def test_cli_override_wins(self, tmp_path):
        """CLI --config override wins over everything."""
        _write_config(tmp_path / ".klemma" / "config.yaml", {
            "obsidian": {"vault_path": "/vault"},
            "ai": {"model": "sonnet"},
        })
        override = tmp_path / "override.yaml"
        _write_config(override, {
            "ai": {"model": "opus"},
        })

        from klemma.config import resolve_effective_config

        cfg, _, _ = resolve_effective_config([tmp_path], config_override=override)
        assert cfg.ai.model == "opus"

    def test_db_path_stays_per_project(self, tmp_path):
        """Each project's db_path is resolved relative to its own .klemma/."""
        parent = tmp_path / "thesis"
        child = parent / "paper1"

        _write_config(parent / ".klemma" / "config.yaml", {
            "obsidian": {"vault_path": "/vault"},
            "state": {"db_path": "./data/thesis.db"},
        })
        _write_config(child / ".klemma" / "config.yaml", {
            "state": {"db_path": "./data/paper.db"},
        })

        from klemma.config import resolve_effective_config

        cfg, _, root = resolve_effective_config([child, parent])
        # state is NOT inherited, so child's db_path is used
        assert cfg.state.db_path == "./data/paper.db"
        assert root == child


# --- Context aggregation tests ---


class TestLoadProjectContext:
    def test_single_klemma_md(self, tmp_path):
        (tmp_path / ".klemma").mkdir()
        (tmp_path / "KLEMMA.md").write_text("# My Dissertation\nTopic: ice forecasting")

        from klemma.config import load_project_context

        result = load_project_context([tmp_path])
        assert "My Dissertation" in result
        assert "ice forecasting" in result

    def test_nested_child_only(self, tmp_path):
        """Child project uses only its own context, not parent's (ADR-012)."""
        parent = tmp_path / "thesis"
        child = parent / "paper1"
        (parent / ".klemma").mkdir(parents=True)
        (child / ".klemma").mkdir(parents=True)
        (parent / "KLEMMA.md").write_text("# Dissertation\nBig picture")
        (child / "KLEMMA.md").write_text("# Paper 1\nSpecific topic")

        from klemma.config import load_project_context

        result = load_project_context([child, parent])
        # Child context only — parent excluded (ADR-012)
        assert "Paper 1" in result
        assert "Dissertation" not in result
        assert "---" not in result  # no separator

    def test_missing_child_klemma_md_falls_back_to_config(self, tmp_path):
        """When child has no KLEMMA.md, fall back to config fields, not parent."""
        parent = tmp_path / "thesis"
        child = parent / "paper1"
        (parent / ".klemma").mkdir(parents=True)
        (child / ".klemma").mkdir(parents=True)
        # Only parent has KLEMMA.md
        (parent / "KLEMMA.md").write_text("# Dissertation")

        from klemma.config import KlemmaConfig, load_project_context

        cfg = KlemmaConfig.model_validate({
            "obsidian": {"vault_path": "/v"},
            "project": {"type": "paper", "title": "My Paper"},
        })
        result = load_project_context([child, parent], config=cfg)
        # Should fall back to config, not use parent
        assert "My Paper" in result
        assert "Dissertation" not in result

    def test_legacy_context_md_fallback(self, tmp_path):
        (tmp_path / ".klemma").mkdir()
        (tmp_path / ".klemma" / "context.md").write_text("Legacy context")

        from klemma.config import load_project_context

        result = load_project_context([tmp_path])
        assert "Legacy context" in result

    def test_fallback_to_config_fields(self, tmp_path):
        (tmp_path / ".klemma").mkdir()
        # No KLEMMA.md, no context.md — build from config

        from klemma.config import KlemmaConfig, load_project_context

        cfg = KlemmaConfig.model_validate({
            "obsidian": {"vault_path": "/v"},
            "project": {"type": "dissertation", "title": "My Topic", "chapters": {1: "Intro"}},
        })

        result = load_project_context([tmp_path], config=cfg)
        assert "My Topic" in result


# --- Prompt resolution tests ---


class TestResolvePrompt:
    def test_shipped_prompt_used_when_no_overrides(self, tmp_path):
        klemma_home = tmp_path / ".klemma"
        klemma_home.mkdir()
        from klemma.config import resolve_prompt

        result = resolve_prompt("extract.md", klemma_home)
        # Should fall back to shipped prompts
        assert "prompts" in str(result)

    def test_project_prompt_overrides_shipped(self, tmp_path):
        klemma_home = tmp_path / ".klemma"
        (klemma_home / "prompts").mkdir(parents=True)
        (klemma_home / "prompts" / "extract.md").write_text("custom")

        from klemma.config import resolve_prompt

        result = resolve_prompt("extract.md", klemma_home)
        assert result == klemma_home / "prompts" / "extract.md"

    def test_system_prompt_overrides_shipped(self, tmp_path, monkeypatch):
        system_home = tmp_path / "system_home"
        (system_home / "prompts").mkdir(parents=True)
        (system_home / "prompts" / "extract.md").write_text("global override")
        monkeypatch.setenv("KLEMMA_HOME", str(system_home))

        # Project .klemma/ without prompt override
        project = tmp_path / "project"
        klemma_home = project / ".klemma"
        klemma_home.mkdir(parents=True)

        from klemma.config import resolve_prompt

        result = resolve_prompt("extract.md", klemma_home)
        assert result == system_home / "prompts" / "extract.md"


# --- Tags tests ---


class TestLoadAvailableTags:
    def test_loads_from_yaml(self, tmp_path):
        klemma_home = tmp_path / ".klemma"
        klemma_home.mkdir()
        (klemma_home / "tags.yaml").write_text("- Review\n- Methodology\n- Dataset")

        from klemma.config import KlemmaConfig, load_available_tags

        cfg = KlemmaConfig.model_validate({"obsidian": {"vault_path": "/v"}})
        tags = load_available_tags(klemma_home, cfg)
        assert tags == ["Review", "Methodology", "Dataset"]

    def test_fallback_to_auto_mapping(self, tmp_path):
        klemma_home = tmp_path / ".klemma"
        klemma_home.mkdir()

        from klemma.config import KlemmaConfig, load_available_tags

        cfg = KlemmaConfig.model_validate({
            "obsidian": {"vault_path": "/v"},
            "tags": {"auto_mapping": [
                {"pattern": "review", "tag": "Review"},
                {"pattern": "method", "tag": "Method"},
            ]},
        })
        tags = load_available_tags(klemma_home, cfg)
        assert tags == ["Review", "Method"]


# --- Init tests ---


class TestInitProject:
    def test_creates_klemma_dir(self, tmp_path):
        from klemma.setup import init_project

        result = init_project(tmp_path)
        assert (tmp_path / ".klemma").is_dir()
        assert (tmp_path / ".klemma" / "config.yaml").exists()
        assert (tmp_path / ".klemma" / "tags.yaml").exists()
        assert (tmp_path / ".klemma" / "data").is_dir()
        assert (tmp_path / "KLEMMA.md").exists()
        assert (tmp_path / ".gitignore").exists()
        assert ".klemma/config.yaml" in result["created"]
        assert "KLEMMA.md" in result["created"]

    def test_does_not_overwrite_existing(self, tmp_path):
        (tmp_path / ".klemma").mkdir()
        (tmp_path / ".klemma" / "data").mkdir()
        (tmp_path / ".klemma" / "config.yaml").write_text("existing")
        (tmp_path / "KLEMMA.md").write_text("existing context")

        from klemma.setup import init_project

        result = init_project(tmp_path)
        assert ".klemma/config.yaml" in result["skipped"]
        assert "KLEMMA.md" in result["skipped"]
        assert (tmp_path / ".klemma" / "config.yaml").read_text() == "existing"

    def test_project_type_in_config(self, tmp_path):
        from klemma.setup import init_project

        init_project(tmp_path, project_type="paper")
        config_text = (tmp_path / ".klemma" / "config.yaml").read_text()
        assert "paper" in config_text

    def test_gitignore_updated(self, tmp_path):
        (tmp_path / ".gitignore").write_text("*.pyc\n")
        from klemma.setup import init_project

        init_project(tmp_path)
        content = (tmp_path / ".gitignore").read_text()
        assert ".klemma/data/" in content
        assert "*.pyc" in content


    def test_child_project_skips_ai_defaults(self, tmp_path):
        """Child project config should not write backend/model defaults (ADR-012)."""
        from klemma.setup import InitValues, init_project

        values = InitValues(
            title="My Paper",
            description="A paper",
            language="en",
            project_type="paper",
        )
        init_project(tmp_path, project_type="paper", values=values, has_parent=True)
        import yaml
        cfg = yaml.safe_load((tmp_path / ".klemma" / "config.yaml").read_text())
        ai = cfg.get("ai", {})
        assert ai.get("language") == "en"
        assert "backend" not in ai  # inherited from parent
        assert "model" not in ai  # inherited from parent

    def test_root_project_writes_ai_defaults(self, tmp_path):
        """Root project config should write backend/model defaults."""
        from klemma.setup import InitValues, init_project

        values = InitValues(
            title="My Dissertation",
            description="A dissertation",
            language="ru",
            project_type="dissertation",
            backend="litellm",
        )
        init_project(tmp_path, project_type="dissertation", values=values, has_parent=False)
        import yaml
        cfg = yaml.safe_load((tmp_path / ".klemma" / "config.yaml").read_text())
        ai = cfg.get("ai", {})
        assert ai.get("backend") == "litellm"
        assert "model" in ai  # default model written


class TestInitSystem:
    def test_creates_system_dir(self, tmp_path):
        system_home = tmp_path / ".klemma"
        from klemma.setup import init_system

        result = init_system(system_home)
        assert system_home.is_dir()
        assert (system_home / "config.yaml").exists()
        assert "config.yaml" in result["created"]

    def test_does_not_overwrite(self, tmp_path):
        system_home = tmp_path / ".klemma"
        system_home.mkdir()
        (system_home / "config.yaml").write_text("existing")

        from klemma.setup import init_system

        result = init_system(system_home)
        assert "config.yaml" in result["skipped"]


# --- Deep merge tests ---


class TestDeepMerge:
    def test_basic_merge(self):
        from klemma.config import _deep_merge

        result = _deep_merge({"a": 1, "b": 2}, {"b": 3, "c": 4})
        assert result == {"a": 1, "b": 3, "c": 4}

    def test_nested_merge(self):
        from klemma.config import _deep_merge

        base = {"ai": {"model": "sonnet", "timeout": 300}}
        override = {"ai": {"model": "opus"}}
        result = _deep_merge(base, override)
        assert result == {"ai": {"model": "opus", "timeout": 300}}

    def test_override_wins(self):
        from klemma.config import _deep_merge

        result = _deep_merge({"x": {"a": 1}}, {"x": "replaced"})
        assert result == {"x": "replaced"}


# --- Config override with empty chain tests ---


class TestResolveEffectiveConfigOverride:
    def test_config_override_with_empty_chain(self, tmp_path, monkeypatch):
        """When no project found but --config given, still merges system defaults."""
        system_home = tmp_path / "system"
        system_home.mkdir()
        _write_config(system_home / "config.yaml", {
            "ai": {"model": "haiku", "language": "en"},
        })
        monkeypatch.setenv("KLEMMA_HOME", str(system_home))

        override = tmp_path / "custom" / ".klemma" / "config.yaml"
        _write_config(override, {
            "obsidian": {"vault_path": "/vault"},
            "project": {"type": "paper", "title": "Standalone"},
        })

        from klemma.config import resolve_effective_config

        cfg, project, root = resolve_effective_config([], config_override=override)
        assert project.title == "Standalone"
        assert cfg.ai.model == "haiku"  # from system defaults
        assert cfg.ai.language == "en"
        assert root == tmp_path / "custom"

    def test_config_override_with_chain_merges_all(self, tmp_path, monkeypatch):
        """--config override works together with project chain and system defaults."""
        system_home = tmp_path / "system"
        system_home.mkdir()
        _write_config(system_home / "config.yaml", {
            "ai": {"model": "haiku", "timeout": 300},
        })
        monkeypatch.setenv("KLEMMA_HOME", str(system_home))

        project = tmp_path / "project"
        _write_config(project / ".klemma" / "config.yaml", {
            "obsidian": {"vault_path": "/vault"},
            "ai": {"model": "sonnet"},
        })

        override = tmp_path / "override.yaml"
        _write_config(override, {
            "ai": {"model": "opus"},
        })

        from klemma.config import resolve_effective_config

        cfg, _, _ = resolve_effective_config([project], config_override=override)
        assert cfg.ai.model == "opus"  # override wins
        assert cfg.ai.timeout == 300  # system default preserved
        assert cfg.obsidian.vault_path == "/vault"  # project preserved


# --- Tags inheritance tests ---


class TestTagsInheritance:
    def test_tags_from_parent_when_child_has_none(self, tmp_path):
        """Child without tags.yaml falls back to parent's tags."""
        parent = tmp_path / "thesis"
        child = parent / "paper1"
        (parent / ".klemma").mkdir(parents=True)
        (child / ".klemma").mkdir(parents=True)
        (parent / ".klemma" / "tags.yaml").write_text("- Review\n- Theory\n- Method")

        from klemma.config import KlemmaConfig, load_available_tags

        cfg = KlemmaConfig.model_validate({"obsidian": {"vault_path": "/v"}})
        tags = load_available_tags(child / ".klemma", cfg, project_chain=[child, parent])
        assert tags == ["Review", "Theory", "Method"]

    def test_child_tags_override_parent(self, tmp_path):
        """Child with own tags.yaml does not inherit from parent."""
        parent = tmp_path / "thesis"
        child = parent / "paper1"
        (parent / ".klemma").mkdir(parents=True)
        (child / ".klemma").mkdir(parents=True)
        (parent / ".klemma" / "tags.yaml").write_text("- Review\n- Theory")
        (child / ".klemma" / "tags.yaml").write_text("- Dataset\n- Algorithm")

        from klemma.config import KlemmaConfig, load_available_tags

        cfg = KlemmaConfig.model_validate({"obsidian": {"vault_path": "/v"}})
        tags = load_available_tags(child / ".klemma", cfg, project_chain=[child, parent])
        assert tags == ["Dataset", "Algorithm"]

    def test_no_chain_falls_back_to_auto_mapping(self, tmp_path):
        """Without project_chain, falls back to auto_mapping as before."""
        klemma_home = tmp_path / ".klemma"
        klemma_home.mkdir()

        from klemma.config import KlemmaConfig, load_available_tags

        cfg = KlemmaConfig.model_validate({
            "obsidian": {"vault_path": "/v"},
            "tags": {"auto_mapping": [
                {"pattern": "review", "tag": "Review"},
            ]},
        })
        tags = load_available_tags(klemma_home, cfg)
        assert tags == ["Review"]


# --- Prompt resolution with project_chain tests ---


class TestResolvePromptWithChain:
    def test_parent_prompt_override(self, tmp_path):
        """When child has no prompt but parent does, parent wins over system."""
        parent = tmp_path / "thesis"
        child = parent / "paper1"
        (parent / ".klemma" / "prompts").mkdir(parents=True)
        (child / ".klemma" / "prompts").mkdir(parents=True)
        (parent / ".klemma" / "prompts" / "extract.md").write_text("parent prompt")

        from klemma.config import resolve_prompt

        result = resolve_prompt("extract.md", child / ".klemma", project_chain=[child, parent])
        assert result == parent / ".klemma" / "prompts" / "extract.md"

    def test_child_prompt_wins_over_parent(self, tmp_path):
        """Child prompt overrides parent prompt."""
        parent = tmp_path / "thesis"
        child = parent / "paper1"
        (parent / ".klemma" / "prompts").mkdir(parents=True)
        (child / ".klemma" / "prompts").mkdir(parents=True)
        (parent / ".klemma" / "prompts" / "extract.md").write_text("parent")
        (child / ".klemma" / "prompts" / "extract.md").write_text("child")

        from klemma.config import resolve_prompt

        result = resolve_prompt("extract.md", child / ".klemma", project_chain=[child, parent])
        assert result == child / ".klemma" / "prompts" / "extract.md"
