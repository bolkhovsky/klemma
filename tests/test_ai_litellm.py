"""Tests for LiteLLM AI backend."""

import importlib
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from klemma.config import AIConfig

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_mock_response(content="Hello from LiteLLM"):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
    )


def _create_client_with_mock(config, mock_litellm=None):
    """Create LiteLLMClient with a mocked litellm module."""
    if mock_litellm is None:
        mock_litellm = MagicMock()
        mock_litellm.completion.return_value = _make_mock_response()

    with patch.dict(sys.modules, {"litellm": mock_litellm}):
        import klemma.ai_litellm as mod
        importlib.reload(mod)
        client = mod.LiteLLMClient(config)

    return client, mock_litellm


# ---------------------------------------------------------------------------
# Import guard
# ---------------------------------------------------------------------------

def test_litellm_import_error():
    """Clean error when litellm package is not installed."""
    with patch.dict(sys.modules, {"litellm": None}):
        import klemma.ai_litellm as mod
        importlib.reload(mod)

        config = AIConfig(backend="litellm", model="anthropic/claude-sonnet-4-6")
        with pytest.raises(ImportError, match="litellm"):
            mod.LiteLLMClient(config)


# ---------------------------------------------------------------------------
# Basic call
# ---------------------------------------------------------------------------

def test_litellm_call():
    config = AIConfig(backend="litellm", model="anthropic/claude-sonnet-4-6")
    client, mock = _create_client_with_mock(config)

    result = client.call("system prompt", "user prompt", max_tokens=1024, temperature=0.5)
    assert result == "Hello from LiteLLM"

    mock.completion.assert_called_once()
    call_kwargs = mock.completion.call_args.kwargs
    assert call_kwargs["model"] == "anthropic/claude-sonnet-4-6"
    assert call_kwargs["max_tokens"] == 1024
    assert call_kwargs["temperature"] == 0.5


# ---------------------------------------------------------------------------
# JSON mode
# ---------------------------------------------------------------------------

def test_litellm_call_json_with_json_mode():
    config = AIConfig(backend="litellm", model="gpt-4.1", json_mode=True)
    mock_litellm = MagicMock()
    mock_litellm.completion.return_value = _make_mock_response('{"key": "value"}')

    client, _ = _create_client_with_mock(config, mock_litellm)

    result = client.call_json("sys", "usr")
    assert result == {"key": "value"}

    call_kwargs = mock_litellm.completion.call_args.kwargs
    assert call_kwargs["response_format"] == {"type": "json_object"}


def test_litellm_call_json_without_json_mode():
    """Without json_mode, call_json uses extract_json from base class."""
    config = AIConfig(backend="litellm", model="gpt-4.1", json_mode=False)
    mock_litellm = MagicMock()
    mock_litellm.completion.return_value = _make_mock_response(
        'Some text\n{"key": "val"}'
    )

    client, _ = _create_client_with_mock(config, mock_litellm)

    result = client.call_json("sys", "usr")
    assert result == {"key": "val"}


# ---------------------------------------------------------------------------
# base_url passthrough
# ---------------------------------------------------------------------------

def test_litellm_base_url():
    config = AIConfig(
        backend="litellm", model="ollama/llama3",
        base_url="http://localhost:11434",
    )
    client, mock = _create_client_with_mock(config)

    client.call("sys", "usr")
    call_kwargs = mock.completion.call_args.kwargs
    assert call_kwargs["base_url"] == "http://localhost:11434"


# ---------------------------------------------------------------------------
# api_key passthrough
# ---------------------------------------------------------------------------

def test_litellm_api_key():
    config = AIConfig(backend="litellm", model="anthropic/claude-sonnet-4-6")
    config._resolved_api_keys = {"anthropic": "sk-ant-test"}
    client, mock = _create_client_with_mock(config)

    client.call("sys", "usr")
    call_kwargs = mock.completion.call_args.kwargs
    assert call_kwargs["api_key"] == "sk-ant-test"


def test_litellm_no_api_key_in_kwargs():
    config = AIConfig(backend="litellm", model="ollama/llama3")
    client, mock = _create_client_with_mock(config)

    client.call("sys", "usr")
    call_kwargs = mock.completion.call_args.kwargs
    assert "api_key" not in call_kwargs


# ---------------------------------------------------------------------------
# Reasoning model detection
# ---------------------------------------------------------------------------

def test_litellm_reasoning_model_o3():
    config = AIConfig(backend="litellm", model="openai/o3-mini")
    client, mock = _create_client_with_mock(config)

    client.call("sys", "usr", max_tokens=4096, temperature=0.5)
    call_kwargs = mock.completion.call_args.kwargs
    assert "max_completion_tokens" in call_kwargs
    assert "max_tokens" not in call_kwargs
    assert "temperature" not in call_kwargs


def test_litellm_reasoning_model_gpt5():
    config = AIConfig(backend="litellm", model="gpt-5")
    client, mock = _create_client_with_mock(config)

    client.call("sys", "usr")
    call_kwargs = mock.completion.call_args.kwargs
    assert "max_completion_tokens" in call_kwargs


def test_litellm_non_reasoning_model():
    config = AIConfig(backend="litellm", model="anthropic/claude-sonnet-4-6")
    client, mock = _create_client_with_mock(config)

    client.call("sys", "usr", max_tokens=2048, temperature=0.3)
    call_kwargs = mock.completion.call_args.kwargs
    assert call_kwargs["max_tokens"] == 2048
    assert call_kwargs["temperature"] == 0.3
    assert "max_completion_tokens" not in call_kwargs


# ---------------------------------------------------------------------------
# Retry on error
# ---------------------------------------------------------------------------

def test_litellm_retry_on_error():
    config = AIConfig(backend="litellm", model="gpt-4.1", retries=2)
    mock_litellm = MagicMock()
    mock_litellm.completion.side_effect = Exception("API error")

    client, _ = _create_client_with_mock(config, mock_litellm)

    result = client.call("sys", "usr")
    assert result is None
    assert mock_litellm.completion.call_count == 3
