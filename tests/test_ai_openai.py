"""Tests for OpenAI-compatible AI backend (deprecated — delegates to LiteLLM)."""

import importlib
import sys
import warnings
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from klemma.config import AIConfig

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_mock_response(content="Hello from AI"):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
    )


def _create_client_with_mock(config, mock_litellm=None):
    """Create OpenAIClient with a mocked litellm module (since it delegates)."""
    if mock_litellm is None:
        mock_litellm = MagicMock()
        mock_litellm.completion.return_value = _make_mock_response()

    with patch.dict(sys.modules, {"litellm": mock_litellm}):
        import klemma.ai_litellm as litellm_mod
        import klemma.ai_openai as mod
        importlib.reload(litellm_mod)
        importlib.reload(mod)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            client = mod.OpenAIClient(config)

    return client, mock_litellm


# ---------------------------------------------------------------------------
# Deprecation warning
# ---------------------------------------------------------------------------

def test_openai_emits_deprecation_warning():
    """OpenAIClient should emit DeprecationWarning on construction."""
    mock_litellm = MagicMock()
    mock_litellm.completion.return_value = _make_mock_response()

    with patch.dict(sys.modules, {"litellm": mock_litellm}):
        import klemma.ai_litellm as litellm_mod
        import klemma.ai_openai as mod
        importlib.reload(litellm_mod)
        importlib.reload(mod)

        config = AIConfig(backend="openai", model="gpt-4o")
        with pytest.warns(DeprecationWarning, match="backend: openai is deprecated"):
            mod.OpenAIClient(config)


# ---------------------------------------------------------------------------
# Import guard (litellm required)
# ---------------------------------------------------------------------------

def test_openai_import_error_without_litellm():
    """Clean error when litellm package is not installed."""
    with patch.dict(sys.modules, {"litellm": None}):
        import klemma.ai_litellm as litellm_mod
        import klemma.ai_openai as mod
        importlib.reload(litellm_mod)
        importlib.reload(mod)

        config = AIConfig(backend="openai", model="gpt-4o")
        with pytest.raises(ImportError, match="litellm"):
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", DeprecationWarning)
                mod.OpenAIClient(config)


# ---------------------------------------------------------------------------
# Delegation to LiteLLMClient
# ---------------------------------------------------------------------------

def test_openai_call_delegates_to_litellm():
    config = AIConfig(backend="openai", model="gpt-4o")
    client, mock = _create_client_with_mock(config)

    result = client.call("system prompt", "user prompt", max_tokens=1024, temperature=0.5)
    assert result == "Hello from AI"

    mock.completion.assert_called_once()
    call_kwargs = mock.completion.call_args.kwargs
    # Model should be prefixed with openai/
    assert call_kwargs["model"] == "openai/gpt-4o"


def test_openai_model_already_prefixed():
    """Model with provider/ prefix should not be double-prefixed."""
    config = AIConfig(backend="openai", model="openai/gpt-4o")
    client, mock = _create_client_with_mock(config)

    client.call("sys", "usr")
    call_kwargs = mock.completion.call_args.kwargs
    assert call_kwargs["model"] == "openai/gpt-4o"


def test_openai_call_json_delegates():
    config = AIConfig(backend="openai", model="gpt-4o", json_mode=True)
    mock_litellm = MagicMock()
    mock_litellm.completion.return_value = _make_mock_response('{"key": "value"}')

    client, _ = _create_client_with_mock(config, mock_litellm)

    result = client.call_json("sys", "usr")
    assert result == {"key": "value"}


def test_openai_retry_on_error():
    config = AIConfig(backend="openai", model="gpt-4o", retries=2)
    mock_litellm = MagicMock()
    mock_litellm.completion.side_effect = Exception("API error")

    client, _ = _create_client_with_mock(config, mock_litellm)

    result = client.call("sys", "usr")
    assert result is None
    assert mock_litellm.completion.call_count == 3
