"""Tests for LiteLLM AI backend."""

import importlib
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from klemma.ai import AICallResult
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


def test_litellm_call_with_meta_passes_response_format_when_json_mode():
    """Regression for #381: call_with_meta() must honor self._json_mode.

    Chunked extraction calls call_with_meta() (not call_json) for token
    accounting. Before #381 that path silently dropped json_mode, leaving
    the chunked extraction without structured-JSON enforcement.
    """
    config = AIConfig(backend="litellm", model="gpt-4.1", json_mode=True)
    mock_litellm = MagicMock()
    mock_litellm.completion.return_value = _make_mock_response('{"k": "v"}')

    client, _ = _create_client_with_mock(config, mock_litellm)

    result = client.call_with_meta("sys", "usr")
    assert result.text == '{"k": "v"}'

    call_kwargs = mock_litellm.completion.call_args.kwargs
    assert call_kwargs["response_format"] == {"type": "json_object"}


def test_litellm_call_with_meta_no_response_format_when_json_mode_off():
    """Without json_mode, call_with_meta() must not pass response_format
    (otherwise free-form draft/research generation breaks)."""
    config = AIConfig(backend="litellm", model="gpt-4.1", json_mode=False)
    mock_litellm = MagicMock()
    mock_litellm.completion.return_value = _make_mock_response("free-form text")

    client, _ = _create_client_with_mock(config, mock_litellm)

    client.call_with_meta("sys", "usr")
    call_kwargs = mock_litellm.completion.call_args.kwargs
    assert "response_format" not in call_kwargs


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


# ---------------------------------------------------------------------------
# call_with_meta
# ---------------------------------------------------------------------------


def _make_mock_response_with_usage(content="Hello", input_tokens=50, output_tokens=20):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
        usage=SimpleNamespace(prompt_tokens=input_tokens, completion_tokens=output_tokens),
    )


def test_litellm_call_with_meta_returns_result():
    config = AIConfig(backend="litellm", model="gpt-4.1")
    mock_litellm = MagicMock()
    mock_litellm.completion.return_value = _make_mock_response_with_usage(
        "response", input_tokens=100, output_tokens=50,
    )
    # Need to define exception types so they exist on the mock
    mock_litellm.Timeout = type("Timeout", (Exception,), {})
    mock_litellm.RateLimitError = type("RateLimitError", (Exception,), {})
    mock_litellm.AuthenticationError = type("AuthenticationError", (Exception,), {})
    client, _ = _create_client_with_mock(config, mock_litellm)

    result = client.call_with_meta("sys", "usr")
    assert isinstance(result, AICallResult)
    assert result.text == "response"
    assert result.input_tokens == 100
    assert result.output_tokens == 50
    assert result.duration_ms >= 0
    assert result.error is None
    assert result.model == "gpt-4.1"


def test_litellm_call_with_meta_timeout():
    config = AIConfig(backend="litellm", model="gpt-4.1", retries=0)
    mock_litellm = MagicMock()
    mock_litellm.Timeout = type("Timeout", (Exception,), {})
    mock_litellm.RateLimitError = type("RateLimitError", (Exception,), {})
    mock_litellm.AuthenticationError = type("AuthenticationError", (Exception,), {})
    mock_litellm.completion.side_effect = mock_litellm.Timeout("timed out")

    client, _ = _create_client_with_mock(config, mock_litellm)
    result = client.call_with_meta("sys", "usr")
    assert result.text is None
    assert "timeout" in result.error.lower()


def test_litellm_call_with_meta_rate_limit():
    config = AIConfig(backend="litellm", model="gpt-4.1", retries=0)
    mock_litellm = MagicMock()
    mock_litellm.Timeout = type("Timeout", (Exception,), {})
    mock_litellm.RateLimitError = type("RateLimitError", (Exception,), {})
    mock_litellm.AuthenticationError = type("AuthenticationError", (Exception,), {})
    mock_litellm.completion.side_effect = mock_litellm.RateLimitError("429")

    client, _ = _create_client_with_mock(config, mock_litellm)
    result = client.call_with_meta("sys", "usr")
    assert result.text is None
    assert "rate" in result.error.lower()


def test_litellm_call_with_meta_auth_error():
    config = AIConfig(backend="litellm", model="gpt-4.1", retries=2)
    mock_litellm = MagicMock()
    mock_litellm.Timeout = type("Timeout", (Exception,), {})
    mock_litellm.RateLimitError = type("RateLimitError", (Exception,), {})
    mock_litellm.AuthenticationError = type("AuthenticationError", (Exception,), {})
    mock_litellm.completion.side_effect = mock_litellm.AuthenticationError("bad key")

    client, _ = _create_client_with_mock(config, mock_litellm)
    result = client.call_with_meta("sys", "usr")
    assert result.text is None
    assert "auth" in result.error.lower()
    # Auth errors should NOT retry
    assert mock_litellm.completion.call_count == 1


def test_litellm_call_with_meta_retries_counted():
    config = AIConfig(backend="litellm", model="gpt-4.1", retries=2)
    mock_litellm = MagicMock()
    mock_litellm.Timeout = type("Timeout", (Exception,), {})
    mock_litellm.RateLimitError = type("RateLimitError", (Exception,), {})
    mock_litellm.AuthenticationError = type("AuthenticationError", (Exception,), {})
    # Fail twice, succeed on third
    mock_litellm.completion.side_effect = [
        Exception("err1"),
        Exception("err2"),
        _make_mock_response_with_usage("ok"),
    ]

    client, _ = _create_client_with_mock(config, mock_litellm)
    result = client.call_with_meta("sys", "usr")
    assert result.text == "ok"
    assert result.retries_used == 2


# ---------------------------------------------------------------------------
# finish_reason (plan C1) — the chunked engine splits on "max_tokens"
# ---------------------------------------------------------------------------


def test_call_with_meta_maps_length_to_max_tokens():
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    from klemma.ai_litellm import LiteLLMClient
    from klemma.config import AIConfig

    client = LiteLLMClient(AIConfig(backend="litellm", model="openai/gpt-4o-mini"))
    mock_litellm = MagicMock()
    mock_litellm.completion.return_value = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="{}"), finish_reason="length")],
        usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1),
    )
    client._litellm = mock_litellm
    assert client.call_with_meta("s", "u").finish_reason == "max_tokens"


def test_call_with_meta_reports_unknown_without_finish_reason():
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    from klemma.ai_litellm import LiteLLMClient
    from klemma.config import AIConfig

    client = LiteLLMClient(AIConfig(backend="litellm", model="openai/gpt-4o-mini"))
    mock_litellm = MagicMock()
    mock_litellm.completion.return_value = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="{}"))],
    )
    client._litellm = mock_litellm
    assert client.call_with_meta("s", "u").finish_reason == "unknown"
