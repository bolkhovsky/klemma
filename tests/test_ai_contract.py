"""Parametrized contract tests for all AI backends.

Proves that Claude, LiteLLM, and OpenAI (deprecated) backends all satisfy
the same behavioral contract: return types, error handling, retry semantics.
"""

import importlib
import subprocess
import sys
import warnings
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from klemma.ai import AICallResult, AIProvider, ClaudeClient
from klemma.config import AIConfig

# ---------------------------------------------------------------------------
# Backend fixtures
# ---------------------------------------------------------------------------

def _make_litellm_response(content="ok", input_tokens=10, output_tokens=5):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
        usage=SimpleNamespace(prompt_tokens=input_tokens, completion_tokens=output_tokens),
    )


@pytest.fixture
def claude_client():
    """ClaudeClient with mocked subprocess."""
    with patch.object(ClaudeClient, "check_cli_available", return_value=True):
        config = AIConfig(backend="claude", retries=1)
        client = ClaudeClient(config)
    return client


@pytest.fixture
def litellm_client():
    """LiteLLMClient with mocked litellm module."""
    mock = MagicMock()
    mock.completion.return_value = _make_litellm_response()
    mock.Timeout = type("Timeout", (Exception,), {})
    mock.RateLimitError = type("RateLimitError", (Exception,), {})
    mock.AuthenticationError = type("AuthenticationError", (Exception,), {})
    with patch.dict(sys.modules, {"litellm": mock}):
        import klemma.ai_litellm as mod
        importlib.reload(mod)
        config = AIConfig(backend="litellm", model="gpt-4.1", retries=1)
        client = mod.LiteLLMClient(config)
    client._mock = mock
    return client


@pytest.fixture
def openai_client():
    """OpenAIClient (deprecated) with mocked litellm."""
    mock = MagicMock()
    mock.completion.return_value = _make_litellm_response()
    mock.Timeout = type("Timeout", (Exception,), {})
    mock.RateLimitError = type("RateLimitError", (Exception,), {})
    mock.AuthenticationError = type("AuthenticationError", (Exception,), {})
    with patch.dict(sys.modules, {"litellm": mock}):
        import klemma.ai_litellm as litellm_mod
        import klemma.ai_openai as mod
        importlib.reload(litellm_mod)
        importlib.reload(mod)
        config = AIConfig(backend="openai", model="gpt-4o", retries=1)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            client = mod.OpenAIClient(config)
    client._mock = mock
    return client


ALL_BACKENDS = ["claude_client", "litellm_client", "openai_client"]


# ---------------------------------------------------------------------------
# Contract: call() returns Optional[str]
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("backend_fixture", ALL_BACKENDS)
def test_call_returns_string_on_success(backend_fixture, request):
    client = request.getfixturevalue(backend_fixture)
    if isinstance(client, ClaudeClient):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args=[], returncode=0, stdout="hello", stderr="",
            )
            result = client.call("sys", "usr")
    else:
        result = client.call("sys", "usr")

    assert isinstance(result, str)


@pytest.mark.parametrize("backend_fixture", ALL_BACKENDS)
def test_call_returns_none_on_failure(backend_fixture, request):
    client = request.getfixturevalue(backend_fixture)
    if isinstance(client, ClaudeClient):
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("x", 1)):
            result = client.call("sys", "usr")
    else:
        client._mock.completion.side_effect = Exception("fail")
        result = client.call("sys", "usr")

    assert result is None


# ---------------------------------------------------------------------------
# Contract: call_json() returns Optional[dict]
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("backend_fixture", ALL_BACKENDS)
def test_call_json_returns_dict(backend_fixture, request):
    client = request.getfixturevalue(backend_fixture)
    if isinstance(client, ClaudeClient):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args=[], returncode=0, stdout='{"k": "v"}', stderr="",
            )
            result = client.call_json("sys", "usr")
    else:
        client._mock.completion.return_value = _make_litellm_response('{"k": "v"}')
        result = client.call_json("sys", "usr")

    assert isinstance(result, dict)
    assert result["k"] == "v"


@pytest.mark.parametrize("backend_fixture", ALL_BACKENDS)
def test_call_json_returns_none_on_failure(backend_fixture, request):
    client = request.getfixturevalue(backend_fixture)
    if isinstance(client, ClaudeClient):
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("x", 1)):
            result = client.call_json("sys", "usr")
    else:
        client._mock.completion.side_effect = Exception("fail")
        result = client.call_json("sys", "usr")

    assert result is None


# ---------------------------------------------------------------------------
# Contract: call_with_meta() returns AICallResult
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("backend_fixture", ALL_BACKENDS)
def test_call_with_meta_returns_aicallresult(backend_fixture, request):
    client = request.getfixturevalue(backend_fixture)
    if isinstance(client, ClaudeClient):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args=[], returncode=0, stdout="resp", stderr="",
            )
            result = client.call_with_meta("sys", "usr")
    else:
        result = client.call_with_meta("sys", "usr")

    assert isinstance(result, AICallResult)
    assert result.text is not None
    assert result.duration_ms >= 0
    assert result.error is None


@pytest.mark.parametrize("backend_fixture", ALL_BACKENDS)
def test_call_with_meta_failure_has_error(backend_fixture, request):
    client = request.getfixturevalue(backend_fixture)
    if isinstance(client, ClaudeClient):
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("x", 1)):
            result = client.call_with_meta("sys", "usr")
    else:
        client._mock.completion.side_effect = Exception("fail")
        result = client.call_with_meta("sys", "usr")

    assert isinstance(result, AICallResult)
    assert result.text is None
    assert result.error is not None
    assert len(result.error) > 0


# ---------------------------------------------------------------------------
# Contract: protocol conformance
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("backend_fixture", ALL_BACKENDS)
def test_is_ai_provider(backend_fixture, request):
    client = request.getfixturevalue(backend_fixture)
    assert isinstance(client, AIProvider)


# ---------------------------------------------------------------------------
# Contract: empty response → None from call_json
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("backend_fixture", ["litellm_client", "openai_client"])
def test_call_json_empty_response_returns_none(backend_fixture, request):
    client = request.getfixturevalue(backend_fixture)
    client._mock.completion.return_value = _make_litellm_response("")
    result = client.call_json("sys", "usr")
    assert result is None
