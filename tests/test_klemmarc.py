"""Tests for klemmarc loading, provider derivation, and api_keys resolution."""

from pathlib import Path

import yaml

from klemma.config import (
    AIConfig,
    _derive_provider,
    _load_klemmarc,
    resolve_effective_config,
)

# ---------------------------------------------------------------------------
# _load_klemmarc
# ---------------------------------------------------------------------------


class TestLoadKlemmarc:
    def test_loads_klemmarc_yaml(self, tmp_path, monkeypatch):
        klemmarc = tmp_path / ".klemmarc.yaml"
        klemmarc.write_text(
            yaml.dump({"ai": {"model": "gpt-4.1"}, "api_keys": {"openai": "sk-test"}}),
            encoding="utf-8",
        )
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        result = _load_klemmarc()
        assert result["ai"]["model"] == "gpt-4.1"
        assert result["api_keys"]["openai"] == "sk-test"

    def test_loads_klemmarc_yml(self, tmp_path, monkeypatch):
        klemmarc = tmp_path / ".klemmarc.yml"
        klemmarc.write_text(yaml.dump({"ai": {"model": "test"}}), encoding="utf-8")
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        result = _load_klemmarc()
        assert result["ai"]["model"] == "test"

    def test_loads_dotfile_klemmarc(self, tmp_path, monkeypatch):
        klemmarc = tmp_path / ".klemmarc"
        klemmarc.write_text(yaml.dump({"ai": {"timeout": 999}}), encoding="utf-8")
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        result = _load_klemmarc()
        assert result["ai"]["timeout"] == 999

    def test_yaml_takes_priority_over_yml(self, tmp_path, monkeypatch):
        (tmp_path / ".klemmarc.yaml").write_text(
            yaml.dump({"ai": {"model": "yaml"}}), encoding="utf-8"
        )
        (tmp_path / ".klemmarc.yml").write_text(
            yaml.dump({"ai": {"model": "yml"}}), encoding="utf-8"
        )
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        result = _load_klemmarc()
        assert result["ai"]["model"] == "yaml"

    def test_returns_empty_when_missing(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        result = _load_klemmarc()
        assert result == {}


# ---------------------------------------------------------------------------
# _derive_provider
# ---------------------------------------------------------------------------


class TestDeriveProvider:
    def test_litellm_with_prefix(self):
        assert _derive_provider("litellm", "anthropic/claude-sonnet") == "anthropic"

    def test_litellm_bare_model(self):
        assert _derive_provider("litellm", "gpt-4.1") == "openai"

    def test_litellm_openai_prefix(self):
        assert _derive_provider("litellm", "openai/gpt-4.1") == "openai"

    def test_litellm_google_prefix(self):
        assert _derive_provider("litellm", "google/gemini-2.5-pro") == "google"

    def test_openai_backend(self):
        assert _derive_provider("openai", "gpt-4o") == "openai"

    def test_claude_backend(self):
        assert _derive_provider("claude", "opus") == "anthropic"

    def test_litellm_ollama(self):
        assert _derive_provider("litellm", "ollama/llama3") == "ollama"


# ---------------------------------------------------------------------------
# AIConfig.api_key
# ---------------------------------------------------------------------------


class TestAIConfigApiKey:
    def test_api_keys_priority_over_env(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "env-key")
        config = AIConfig(backend="litellm", model="gpt-4.1", api_key_env="OPENAI_API_KEY")
        config._resolved_api_keys = {"openai": "klemmarc-key"}
        assert config.api_key == "klemmarc-key"

    def test_env_fallback_when_no_resolved_key(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "env-key")
        config = AIConfig(backend="litellm", model="gpt-4.1", api_key_env="OPENAI_API_KEY")
        assert config.api_key == "env-key"

    def test_none_when_no_key_at_all(self, monkeypatch):
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        config = AIConfig(backend="litellm", model="gpt-4.1")
        assert config.api_key is None

    def test_anthropic_key_for_litellm_anthropic_model(self):
        config = AIConfig(backend="litellm", model="anthropic/claude-sonnet-4-6")
        config._resolved_api_keys = {"anthropic": "sk-ant-test"}
        assert config.api_key == "sk-ant-test"

    def test_api_keys_not_in_model_dump(self):
        config = AIConfig(backend="litellm", model="gpt-4.1")
        config._resolved_api_keys = {"openai": "secret"}
        dumped = config.model_dump()
        assert "api_keys" not in dumped
        assert "_resolved_api_keys" not in dumped
        assert "secret" not in str(dumped)

    def test_default_backend_is_litellm(self):
        config = AIConfig()
        assert config.backend == "litellm"


# ---------------------------------------------------------------------------
# resolve_effective_config with klemmarc
# ---------------------------------------------------------------------------


class TestResolveEffectiveConfigKlemmarc:
    def test_klemmarc_merged_as_base_layer(self, tmp_path, monkeypatch):
        """klemmarc values are used when no other config provides them."""
        # Setup klemmarc
        klemmarc = tmp_path / ".klemmarc.yaml"
        klemmarc.write_text(
            yaml.dump({
                "ai": {"model": "anthropic/claude-sonnet-4-6", "timeout": 999},
                "api_keys": {"anthropic": "sk-ant-test"},
            }),
            encoding="utf-8",
        )
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        # Setup system home (empty)
        system_home = tmp_path / ".klemma"
        system_home.mkdir()
        (system_home / "config.yaml").write_text("", encoding="utf-8")
        monkeypatch.setattr("klemma.config.get_system_home", lambda: system_home)

        # Setup project
        project = tmp_path / "myproject"
        klemma_dir = project / ".klemma"
        klemma_dir.mkdir(parents=True)
        (klemma_dir / "config.yaml").write_text(
            yaml.dump({"project": {"type": "paper"}}), encoding="utf-8"
        )

        cfg, _, _ = resolve_effective_config([project])
        assert cfg.ai.model == "anthropic/claude-sonnet-4-6"
        assert cfg.ai.timeout == 999
        assert cfg.ai._resolved_api_keys == {"anthropic": "sk-ant-test"}

    def test_project_overrides_klemmarc(self, tmp_path, monkeypatch):
        """Project-level ai settings override klemmarc."""
        klemmarc = tmp_path / ".klemmarc.yaml"
        klemmarc.write_text(
            yaml.dump({"ai": {"model": "anthropic/claude-sonnet-4-6"}}),
            encoding="utf-8",
        )
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        system_home = tmp_path / ".klemma"
        system_home.mkdir()
        (system_home / "config.yaml").write_text("", encoding="utf-8")
        monkeypatch.setattr("klemma.config.get_system_home", lambda: system_home)

        project = tmp_path / "myproject"
        klemma_dir = project / ".klemma"
        klemma_dir.mkdir(parents=True)
        (klemma_dir / "config.yaml").write_text(
            yaml.dump({"ai": {"model": "ollama/llama3"}}), encoding="utf-8"
        )

        cfg, _, _ = resolve_effective_config([project])
        assert cfg.ai.model == "ollama/llama3"

    def test_legacy_fallback_when_no_klemmarc(self, tmp_path, monkeypatch):
        """When no klemmarc exists, legacy system config still works."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        system_home = tmp_path / ".klemma"
        system_home.mkdir()
        (system_home / "config.yaml").write_text(
            yaml.dump({"ai": {"model": "sonnet", "timeout": 300}}),
            encoding="utf-8",
        )
        monkeypatch.setattr("klemma.config.get_system_home", lambda: system_home)

        project = tmp_path / "myproject"
        klemma_dir = project / ".klemma"
        klemma_dir.mkdir(parents=True)
        (klemma_dir / "config.yaml").write_text("", encoding="utf-8")

        cfg, _, _ = resolve_effective_config([project])
        assert cfg.ai.model == "sonnet"
        assert cfg.ai.timeout == 300


# ---------------------------------------------------------------------------
# chmod 600 enforcement
# ---------------------------------------------------------------------------


class TestKlemmarcPermissions:
    def test_check_fixes_world_readable(self, tmp_path, monkeypatch):
        from klemma.config import _check_klemmarc_permissions

        klemmarc = tmp_path / ".klemmarc.yaml"
        klemmarc.write_text("ai:\n  model: test\n", encoding="utf-8")
        klemmarc.chmod(0o644)

        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        _check_klemmarc_permissions()

        mode = klemmarc.stat().st_mode & 0o777
        assert mode == 0o600

    def test_check_leaves_correct_permissions(self, tmp_path, monkeypatch):
        from klemma.config import _check_klemmarc_permissions

        klemmarc = tmp_path / ".klemmarc.yaml"
        klemmarc.write_text("ai:\n  model: test\n", encoding="utf-8")
        klemmarc.chmod(0o600)

        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        _check_klemmarc_permissions()

        mode = klemmarc.stat().st_mode & 0o777
        assert mode == 0o600
