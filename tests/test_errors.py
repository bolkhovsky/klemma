"""Tests for klemma error taxonomy."""
from klemma.errors import (
    AIAuthError,
    AIRateLimitError,
    AIResponseError,
    AITimeoutError,
    KlemmaAIError,
)


def test_error_hierarchy():
    """All AI errors are KlemmaAIError subclasses."""
    assert issubclass(AITimeoutError, KlemmaAIError)
    assert issubclass(AIRateLimitError, KlemmaAIError)
    assert issubclass(AIAuthError, KlemmaAIError)
    assert issubclass(AIResponseError, KlemmaAIError)


def test_retryable_classification():
    """Timeout and rate-limit are retryable; auth and response are not."""
    assert AITimeoutError("t").retryable is True
    assert AIRateLimitError("r").retryable is True
    assert AIAuthError("a").retryable is False
    assert AIResponseError("resp").retryable is False


def test_error_preserves_cause():
    """Original exception is preserved as __cause__."""
    original = ValueError("root")
    err = AITimeoutError("timed out", cause=original)
    assert err.__cause__ is original


def test_error_str():
    err = AIRateLimitError("429 too many requests")
    assert "429 too many requests" in str(err)
