"""Klemma error taxonomy for AI backend contracts."""


class KlemmaAIError(Exception):
    """Base class for all AI backend errors."""

    retryable: bool = False

    def __init__(self, message: str, *, cause: BaseException | None = None):
        super().__init__(message)
        if cause is not None:
            self.__cause__ = cause


class AITimeoutError(KlemmaAIError):
    """AI call exceeded timeout — retryable."""
    retryable = True


class AIRateLimitError(KlemmaAIError):
    """AI provider rate limit hit — retryable."""
    retryable = True


class AIAuthError(KlemmaAIError):
    """Authentication/authorization failure — fatal."""
    retryable = False


class AIResponseError(KlemmaAIError):
    """AI returned unparseable or empty response — fatal."""
    retryable = False
