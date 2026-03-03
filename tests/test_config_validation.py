"""Tests for config validation warnings (#36)."""

import warnings

from klemma.config import _warn_config_issues


class TestMisplacedKeys:
    """Keys that belong inside a section but sit at root level."""

    def test_task_classes_at_root(self):
        raw = {"ai": {"model": "sonnet"}, "task_classes": {"planner": "haiku"}}
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            _warn_config_issues(raw, "test.yaml")
        msgs = [str(x.message) for x in w]
        assert any("'task_classes' should be inside 'ai:'" in m for m in msgs)

    def test_model_at_root(self):
        raw = {"model": "opus"}
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            _warn_config_issues(raw, "test.yaml")
        msgs = [str(x.message) for x in w]
        # model exists in both ai and embeddings
        assert any("'model' should be inside" in m for m in msgs)

    def test_backend_at_root(self):
        raw = {"backend": "claude"}
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            _warn_config_issues(raw, "test.yaml")
        msgs = [str(x.message) for x in w]
        # backend exists in both ai and embeddings
        assert any("'backend' should be inside" in m for m in msgs)

    def test_vault_path_at_root(self):
        raw = {"vault_path": "/some/path"}
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            _warn_config_issues(raw, "test.yaml")
        msgs = [str(x.message) for x in w]
        assert any("'vault_path' should be inside 'obsidian:'" in m for m in msgs)

    def test_no_warning_for_correctly_nested(self):
        raw = {"ai": {"model": "sonnet", "task_classes": {"planner": "haiku"}}}
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            _warn_config_issues(raw, "test.yaml")
        misplaced = [x for x in w if "should be inside" in str(x.message)]
        assert misplaced == []


class TestUnknownKeys:
    """Keys not recognized at any level."""

    def test_unknown_top_level_key(self):
        raw = {"ai": {"model": "sonnet"}, "foobar": 123}
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            _warn_config_issues(raw, "test.yaml")
        msgs = [str(x.message) for x in w]
        assert any("unknown top-level key 'foobar'" in m for m in msgs)

    def test_unknown_key_inside_section(self):
        raw = {"ai": {"model": "sonnet", "banana": True}}
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            _warn_config_issues(raw, "test.yaml")
        msgs = [str(x.message) for x in w]
        assert any("unknown key 'banana' inside 'ai:'" in m for m in msgs)

    def test_api_keys_not_flagged(self):
        """api_keys is valid in klemmarc, should not trigger warning."""
        raw = {"api_keys": {"anthropic": "sk-..."}}
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            _warn_config_issues(raw, "test.yaml")
        msgs = [str(x.message) for x in w]
        assert not any("api_keys" in m for m in msgs)

    def test_mcp_not_flagged(self):
        raw = {"mcp": {"servers": {}}}
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            _warn_config_issues(raw, "test.yaml")
        msgs = [str(x.message) for x in w]
        assert not any("mcp" in m for m in msgs)

    def test_no_warning_for_valid_config(self):
        raw = {"ai": {"model": "sonnet", "backend": "claude"}, "instance": {"name": "test"}}
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            _warn_config_issues(raw, "test.yaml")
        assert w == []


class TestBareModelNames:
    """Claude shorthands used with litellm backend."""

    def test_bare_model_with_litellm(self):
        raw = {"ai": {"backend": "litellm", "model": "opus"}}
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            _warn_config_issues(raw, "test.yaml")
        msgs = [str(x.message) for x in w]
        assert any("ai.model='opus' is a Claude shorthand" in m for m in msgs)
        assert any("anthropic/claude-opus-4-6" in m for m in msgs)

    def test_bare_model_with_default_backend(self):
        """Default backend is litellm — bare name should warn."""
        raw = {"ai": {"model": "sonnet"}}
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            _warn_config_issues(raw, "test.yaml")
        msgs = [str(x.message) for x in w]
        assert any("ai.model='sonnet' is a Claude shorthand" in m for m in msgs)

    def test_bare_model_with_claude_backend_no_warning(self):
        """Claude backend accepts sonnet/opus — no warning."""
        raw = {"ai": {"backend": "claude", "model": "opus"}}
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            _warn_config_issues(raw, "test.yaml")
        model_warnings = [
            x for x in w if "not supported" in str(x.message)
            or "Claude shorthand" in str(x.message)
        ]
        assert model_warnings == []

    def test_haiku_with_claude_backend_warns(self):
        """Claude CLI doesn't support haiku — should warn."""
        raw = {"ai": {"backend": "claude", "model": "haiku"}}
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            _warn_config_issues(raw, "test.yaml")
        msgs = [str(x.message) for x in w]
        assert any("not supported by Claude CLI" in m for m in msgs)

    def test_haiku_task_class_with_claude_backend_warns(self):
        """task_classes with haiku on claude backend — should warn."""
        raw = {"ai": {"backend": "claude", "model": "opus",
                       "task_classes": {"planner": "haiku"}}}
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            _warn_config_issues(raw, "test.yaml")
        msgs = [str(x.message) for x in w]
        assert any("task_classes.planner='haiku' is not supported" in m for m in msgs)
        assert any("litellm" in m for m in msgs)

    def test_full_model_name_no_warning(self):
        raw = {"ai": {"backend": "litellm", "model": "anthropic/claude-opus-4-6"}}
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            _warn_config_issues(raw, "test.yaml")
        bare_model_warnings = [
            x for x in w if "Claude shorthand" in str(x.message)
        ]
        assert bare_model_warnings == []

    def test_task_class_bare_name_without_map(self):
        raw = {"ai": {"backend": "litellm", "model": "anthropic/claude-sonnet-4-6",
                       "task_classes": {"planner": "haiku"}}}
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            _warn_config_issues(raw, "test.yaml")
        msgs = [str(x.message) for x in w]
        assert any("task_classes.planner='haiku'" in m for m in msgs)
        assert any("class_model_map.litellm.haiku" in m for m in msgs)

    def test_task_class_with_map_no_warning(self):
        raw = {"ai": {
            "backend": "litellm",
            "model": "anthropic/claude-sonnet-4-6",
            "task_classes": {"planner": "haiku"},
            "class_model_map": {"litellm": {"haiku": "anthropic/claude-haiku-4-5-20251001"}},
        }}
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            _warn_config_issues(raw, "test.yaml")
        task_class_warnings = [
            x for x in w if "task_classes" in str(x.message)
        ]
        assert task_class_warnings == []


class TestEdgeCases:
    """Edge cases and robustness."""

    def test_empty_dict(self):
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            _warn_config_issues({}, "test.yaml")
        assert w == []

    def test_none_input(self):
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            _warn_config_issues(None, "test.yaml")
        assert w == []

    def test_non_dict_section(self):
        """Section value is a string instead of dict — should not crash."""
        raw = {"ai": "not-a-dict"}
        with warnings.catch_warnings(record=True):
            warnings.simplefilter("always")
            _warn_config_issues(raw, "test.yaml")
        # Should not crash, may or may not warn

    def test_source_label_appears_in_warning(self):
        raw = {"foobar": 123}
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            _warn_config_issues(raw, "/home/user/.klemma/config.yaml")
        msgs = [str(x.message) for x in w]
        assert any("/home/user/.klemma/config.yaml" in m for m in msgs)

    def test_multiple_issues_all_reported(self):
        raw = {
            "task_classes": {"planner": "haiku"},  # misplaced
            "banana": True,                        # unknown
            "ai": {"model": "opus"},               # bare model
        }
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            _warn_config_issues(raw, "test.yaml")
        msgs = [str(x.message) for x in w]
        assert any("should be inside 'ai:'" in m for m in msgs)
        assert any("unknown top-level key 'banana'" in m for m in msgs)
        assert any("ai.model='opus' is a Claude shorthand" in m for m in msgs)
