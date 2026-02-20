"""Tests for OpenAI-compatible AI backend."""

import importlib
import sys
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


def _create_client_with_mock(config, mock_client=None):
    """Create OpenAIClient with a mocked openai SDK."""
    if mock_client is None:
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = _make_mock_response()

    mock_openai_module = MagicMock()
    mock_openai_module.OpenAI.return_value = mock_client

    with patch.dict(sys.modules, {"openai": mock_openai_module}):
        import klemma.ai_openai as mod
        importlib.reload(mod)
        client = mod.OpenAIClient(config)

    return client


# ---------------------------------------------------------------------------
# Import guard
# ---------------------------------------------------------------------------

def test_openai_import_error():
    """Clean error when openai package is not installed."""
    with patch.dict(sys.modules, {"openai": None}):
        import klemma.ai_openai as mod
        importlib.reload(mod)

        config = AIConfig(backend="openai", model="gpt-4o")
        with pytest.raises(ImportError, match="openai"):
            mod.OpenAIClient(config)


# ---------------------------------------------------------------------------
# OpenAIClient with mocked SDK
# ---------------------------------------------------------------------------

def test_openai_call():
    config = AIConfig(backend="openai", model="gpt-4o", base_url="http://localhost:8000/v1")
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = _make_mock_response("Hello from AI")

    client = _create_client_with_mock(config, mock_client)

    result = client.call("system prompt", "user prompt", max_tokens=1024, temperature=0.5)
    assert result == "Hello from AI"

    mock_client.chat.completions.create.assert_called_once_with(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": "system prompt"},
            {"role": "user", "content": "user prompt"},
        ],
        max_tokens=1024,
        temperature=0.5,
    )


def test_openai_json_mode():
    config = AIConfig(backend="openai", model="gpt-4o", json_mode=True)
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = _make_mock_response('{"key": "value"}')

    client = _create_client_with_mock(config, mock_client)

    result = client.call_json("sys", "usr")
    assert result == {"key": "value"}

    call_kwargs = mock_client.chat.completions.create.call_args
    assert call_kwargs.kwargs.get("response_format") == {"type": "json_object"}


def test_openai_call_json_fallback_no_json_mode():
    """Without json_mode, call_json uses extract_json from base class."""
    config = AIConfig(backend="openai", model="gpt-4o", json_mode=False)
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = _make_mock_response(
        'Some text\n{"key": "val"}'
    )

    client = _create_client_with_mock(config, mock_client)

    result = client.call_json("sys", "usr")
    assert result == {"key": "val"}


def test_openai_retry_on_error():
    config = AIConfig(backend="openai", model="gpt-4o", retries=2)
    mock_client = MagicMock()
    mock_client.chat.completions.create.side_effect = Exception("API error")

    client = _create_client_with_mock(config, mock_client)

    result = client.call("sys", "usr")
    assert result is None
    # Should have been called retries + 1 = 3 times
    assert mock_client.chat.completions.create.call_count == 3
