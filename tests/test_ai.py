"""Tests for AI provider abstraction: extract_json, AIProviderBase, factory."""

import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from klemma.ai import (
    AIProvider,
    AIProviderBase,
    ClaudeClient,
    create_ai,
    extract_json,
)
from klemma.config import AIConfig

# ---------------------------------------------------------------------------
# extract_json
# ---------------------------------------------------------------------------

def test_extract_json_valid():
    assert extract_json('{"key": "value"}') == {"key": "value"}


def test_extract_json_with_surrounding_text():
    text = 'Here is the result:\n{"key": "value"}\nDone.'
    assert extract_json(text) == {"key": "value"}


def test_extract_json_markdown_wrapped():
    text = '```json\n{"key": "value"}\n```'
    assert extract_json(text) == {"key": "value"}


def test_extract_json_no_json():
    assert extract_json("no json here") is None


def test_extract_json_invalid_json():
    assert extract_json("{invalid json}") is None


# ---------------------------------------------------------------------------
# AIProviderBase
# ---------------------------------------------------------------------------

def test_base_render_prompt():
    config = AIConfig()
    base = AIProviderBase(config)
    with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
        f.write("Hello {{ name }}!")
        f.flush()
        result = base.render_prompt(Path(f.name), name="World")
    assert result == "Hello World!"


def test_base_call_json_delegates_to_call():
    config = AIConfig()
    base = AIProviderBase(config)
    # Override call to return JSON text
    base.call = lambda system, user, max_tokens=8192, temperature=0.2, timeout=None: '{"result": 42}'
    assert base.call_json("sys", "usr") == {"result": 42}


def test_base_call_json_returns_none_on_empty():
    config = AIConfig()
    base = AIProviderBase(config)
    base.call = lambda system, user, max_tokens=8192, temperature=0.2, timeout=None: None
    assert base.call_json("sys", "usr") is None


def test_base_interactive_available_false():
    config = AIConfig()
    base = AIProviderBase(config)
    assert base.interactive_available is False


# ---------------------------------------------------------------------------
# ClaudeClient + Protocol conformance
# ---------------------------------------------------------------------------

@patch.object(ClaudeClient, "check_cli_available", return_value=True)
def test_claude_client_is_ai_provider(mock_check):
    config = AIConfig()
    client = ClaudeClient(config)
    assert isinstance(client, AIProvider)


@patch.object(ClaudeClient, "check_cli_available", return_value=True)
def test_claude_client_interactive_available(mock_check):
    config = AIConfig()
    client = ClaudeClient(config)
    assert client.interactive_available is True


# ---------------------------------------------------------------------------
# create_ai factory
# ---------------------------------------------------------------------------

@patch.object(ClaudeClient, "check_cli_available", return_value=True)
def test_create_ai_claude_backend(mock_check):
    config = AIConfig(backend="claude")
    ai = create_ai(config)
    assert isinstance(ai, ClaudeClient)


def test_create_ai_unknown_backend():
    config = AIConfig(backend="unknown")
    with pytest.raises(ValueError, match="Unknown AI backend"):
        create_ai(config)
