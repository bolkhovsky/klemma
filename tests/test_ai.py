"""Tests for AI provider abstraction: extract_json, AIProviderBase, factory."""

import subprocess
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from klemma.ai import (
    AICallResult,
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


def test_extract_json_rejects_deeply_nested():
    """Deeply nested JSON (>20 levels) is rejected to prevent stack issues."""
    # Build 25 levels of nesting
    nested = '{"a":' * 25 + '1' + '}' * 25
    assert extract_json(nested) is None


def test_extract_json_rejects_oversized():
    """JSON responses over 512KB are rejected."""
    huge = '{"data": "' + "x" * 600_000 + '"}'
    assert extract_json(huge) is None


def test_extract_json_accepts_reasonable_nesting():
    """Normal nesting (≤20 levels) is accepted."""
    nested = '{"a": {"b": {"c": 1}}}'
    result = extract_json(nested)
    assert result == {"a": {"b": {"c": 1}}}


def test_extract_json_accepts_reasonable_size():
    """Moderately sized JSON (< 512KB) is accepted."""
    data = '{"data": "' + "x" * 100_000 + '"}'
    result = extract_json(data)
    assert result is not None
    assert len(result["data"]) == 100_000


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


# ---------------------------------------------------------------------------
# AICallResult
# ---------------------------------------------------------------------------

def test_aicallresult_fields():
    r = AICallResult(
        text="hello",
        duration_ms=150,
        input_tokens=10,
        output_tokens=5,
        retries_used=0,
        model="gpt-4.1",
    )
    assert r.text == "hello"
    assert r.duration_ms == 150
    assert r.input_tokens == 10
    assert r.output_tokens == 5
    assert r.retries_used == 0
    assert r.model == "gpt-4.1"
    assert r.error is None


def test_aicallresult_failed():
    r = AICallResult(text=None, duration_ms=3000, model="gpt-4.1", error="timeout")
    assert r.text is None
    assert r.error == "timeout"


def test_aicallresult_bool():
    """Truthy when text is present, falsy when None."""
    assert bool(AICallResult(text="ok", duration_ms=1, model="m"))
    assert not bool(AICallResult(text=None, duration_ms=1, model="m"))


def test_base_call_with_meta_delegates():
    """AIProviderBase.call_with_meta() wraps call() with timing."""
    config = AIConfig()
    base = AIProviderBase(config)
    base.call = lambda system, user, max_tokens=8192, temperature=0.3, timeout=None: "response text"

    result = base.call_with_meta("sys", "usr")
    assert isinstance(result, AICallResult)
    assert result.text == "response text"
    assert result.duration_ms >= 0
    assert result.retries_used == 0
    assert result.model == config.model


def test_base_call_with_meta_on_failure():
    config = AIConfig()
    base = AIProviderBase(config)
    base.call = lambda system, user, max_tokens=8192, temperature=0.3, timeout=None: None

    result = base.call_with_meta("sys", "usr")
    assert result.text is None
    assert result.error == "all retries exhausted"


# ---------------------------------------------------------------------------
# ClaudeClient.call_with_meta()
# ---------------------------------------------------------------------------

@patch.object(ClaudeClient, "check_cli_available", return_value=True)
def test_claude_call_with_meta_success(mock_check):
    config = AIConfig(backend="claude")
    client = ClaudeClient(config)

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="response text", stderr="",
        )
        result = client.call_with_meta("sys", "usr")

    assert isinstance(result, AICallResult)
    assert result.text == "response text"
    assert result.duration_ms >= 0
    assert result.error is None


@patch.object(ClaudeClient, "check_cli_available", return_value=True)
def test_claude_call_with_meta_timeout(mock_check):
    config = AIConfig(backend="claude", retries=0)
    client = ClaudeClient(config)

    with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("claude", 180)):
        result = client.call_with_meta("sys", "usr")

    assert result.text is None
    assert "timeout" in result.error.lower()


@patch.object(ClaudeClient, "check_cli_available", return_value=True)
def test_claude_call_with_meta_cli_error(mock_check):
    config = AIConfig(backend="claude", retries=0)
    client = ClaudeClient(config)

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="error output",
        )
        result = client.call_with_meta("sys", "usr")

    assert result.text is None
    assert result.error is not None
