"""Tests for class-based model routing (#36)."""

from unittest.mock import MagicMock, patch

from klemma.ai import resolve_task_model
from klemma.config import AIConfig

# ---------------------------------------------------------------------------
# resolve_task_model
# ---------------------------------------------------------------------------

def test_no_task_classes_returns_none():
    cfg = AIConfig()
    assert resolve_task_model("planner", cfg) is None


def test_task_not_in_classes_returns_none():
    cfg = AIConfig(task_classes={"research": "opus"})
    assert resolve_task_model("planner", cfg) is None


def test_claude_backend_returns_class_name():
    cfg = AIConfig(backend="claude", task_classes={"planner": "haiku"})
    assert resolve_task_model("planner", cfg) == "haiku"


def test_claude_backend_class_model_map_takes_priority():
    cfg = AIConfig(
        backend="claude",
        task_classes={"planner": "haiku"},
        class_model_map={"claude": {"haiku": "claude-haiku-4-5-20251001"}},
    )
    assert resolve_task_model("planner", cfg) == "claude-haiku-4-5-20251001"


def test_litellm_backend_with_map():
    cfg = AIConfig(
        backend="litellm",
        task_classes={"research": "opus"},
        class_model_map={"litellm": {"opus": "anthropic/claude-opus-4-6"}},
    )
    assert resolve_task_model("research", cfg) == "anthropic/claude-opus-4-6"


def test_litellm_backend_without_map_returns_none():
    """LiteLLM can't use bare class names — no map means no override."""
    cfg = AIConfig(
        backend="litellm",
        task_classes={"research": "opus"},
    )
    assert resolve_task_model("research", cfg) is None


def test_openai_backend_with_map():
    cfg = AIConfig(
        backend="openai",
        task_classes={"extract": "sonnet"},
        class_model_map={"openai": {"sonnet": "gpt-4o-mini"}},
    )
    assert resolve_task_model("extract", cfg) == "gpt-4o-mini"


# ---------------------------------------------------------------------------
# model_override forwarding — ClaudeClient
# ---------------------------------------------------------------------------

def test_claude_client_model_override():
    """ClaudeClient passes model_override to subprocess."""
    from klemma.ai import ClaudeClient

    cfg = AIConfig(backend="claude", model="sonnet")
    with patch("klemma.ai.ClaudeClient.check_cli_available", return_value=True):
        client = ClaudeClient(cfg)

    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = "response"

    with patch("subprocess.run", return_value=mock_result) as mock_run:
        result = client.call("sys", "usr", model_override="haiku")
        assert result == "response"
        # Verify --model haiku was passed
        args = mock_run.call_args[0][0]
        model_idx = args.index("--model")
        assert args[model_idx + 1] == "haiku"


def test_claude_client_default_model_without_override():
    """Without override, default model is used."""
    from klemma.ai import ClaudeClient

    cfg = AIConfig(backend="claude", model="sonnet")
    with patch("klemma.ai.ClaudeClient.check_cli_available", return_value=True):
        client = ClaudeClient(cfg)

    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = "response"

    with patch("subprocess.run", return_value=mock_result) as mock_run:
        client.call("sys", "usr")
        args = mock_run.call_args[0][0]
        model_idx = args.index("--model")
        assert args[model_idx + 1] == "sonnet"


# ---------------------------------------------------------------------------
# model_override forwarding — LiteLLMClient
# ---------------------------------------------------------------------------

def test_litellm_client_model_override():
    """LiteLLMClient passes model_override to litellm.completion()."""
    mock_litellm = MagicMock()
    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = "response"
    mock_litellm.completion.return_value = mock_response

    cfg = AIConfig(backend="litellm", model="anthropic/claude-sonnet-4-6")
    with patch.dict("sys.modules", {"litellm": mock_litellm}):
        from klemma.ai_litellm import LiteLLMClient
        client = LiteLLMClient(cfg)
        client._litellm = mock_litellm

    result = client.call("sys", "usr", model_override="anthropic/claude-haiku-4-5-20251001")
    assert result == "response"
    call_kwargs = mock_litellm.completion.call_args[1]
    assert call_kwargs["model"] == "anthropic/claude-haiku-4-5-20251001"


def test_litellm_client_default_model_without_override():
    mock_litellm = MagicMock()
    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = "response"
    mock_litellm.completion.return_value = mock_response

    cfg = AIConfig(backend="litellm", model="anthropic/claude-sonnet-4-6")
    with patch.dict("sys.modules", {"litellm": mock_litellm}):
        from klemma.ai_litellm import LiteLLMClient
        client = LiteLLMClient(cfg)
        client._litellm = mock_litellm

    client.call("sys", "usr")
    call_kwargs = mock_litellm.completion.call_args[1]
    assert call_kwargs["model"] == "anthropic/claude-sonnet-4-6"


# ---------------------------------------------------------------------------
# Config parsing
# ---------------------------------------------------------------------------

def test_ai_config_task_classes_default():
    cfg = AIConfig()
    assert cfg.task_classes == {}
    assert cfg.class_model_map == {}


def test_ai_config_task_classes_from_dict():
    cfg = AIConfig(
        task_classes={"planner": "haiku", "research": "opus"},
        class_model_map={"openai": {"opus": "gpt-4o"}},
    )
    assert cfg.task_classes["planner"] == "haiku"
    assert cfg.class_model_map["openai"]["opus"] == "gpt-4o"
