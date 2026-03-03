"""OpenAI-compatible AI backend — DEPRECATED, delegates to LiteLLM.

Use ``backend: litellm`` with ``model: openai/gpt-4.1`` instead.
This module emits a DeprecationWarning and delegates all calls to LiteLLMClient
with an ``openai/`` prefix on the model name.
"""

import logging
import warnings
from typing import Optional

from .ai import AICallResult, AIProviderBase
from .config import AIConfig

logger = logging.getLogger(__name__)


class OpenAIClient(AIProviderBase):
    """Deprecated OpenAI backend — thin wrapper around LiteLLMClient.

    Emits DeprecationWarning on construction. Prefixes bare model names
    with ``openai/`` so LiteLLM routes them correctly.
    """

    def __init__(self, config: AIConfig):
        warnings.warn(
            "backend: openai is deprecated. Use backend: litellm with "
            "model: openai/<model-name> instead (e.g. model: openai/gpt-4.1).",
            DeprecationWarning,
            stacklevel=2,
        )
        super().__init__(config)

        try:
            from .ai_litellm import LiteLLMClient
        except ImportError:
            raise ImportError(
                "OpenAI backend now requires the 'litellm' package (delegates to LiteLLM). "
                "Install with: pip install klemma[litellm]"
            )

        # Prefix bare model names with openai/ for LiteLLM routing
        prefixed_model = config.model
        if "/" not in prefixed_model:
            prefixed_model = f"openai/{prefixed_model}"

        # Create a modified config for the delegate
        delegate_config = config.model_copy(update={"model": prefixed_model, "backend": "litellm"})
        # Preserve private attrs
        delegate_config._resolved_api_keys = config._resolved_api_keys
        self._delegate = LiteLLMClient(delegate_config)

    @property
    def _is_reasoning_model(self) -> bool:
        """Detect reasoning models that require different API parameters."""
        m = self.model.lower()
        return m.startswith(("o1", "o3", "o4", "gpt-5"))

    def _token_kwargs(self, max_tokens: int) -> dict:
        """Build the right token-limit kwarg for the model.

        Reasoning models (o-series, gpt-5-*) require
        ``max_completion_tokens`` instead of ``max_tokens``.
        """
        if self._is_reasoning_model:
            return {"max_completion_tokens": max_tokens}
        return {"max_tokens": max_tokens}

    def call(
        self,
        system: str,
        user: str,
        max_tokens: int = 8192,
        temperature: float = 0.3,
        timeout: Optional[int] = None,
        model_override: Optional[str] = None,
    ) -> Optional[str]:
        return self._delegate.call(
            system, user, max_tokens, temperature, timeout,
            model_override=model_override,
        )

    def call_json(
        self,
        system: str,
        user: str,
        max_tokens: int = 8192,
        temperature: float = 0.2,
        timeout: Optional[int] = None,
        model_override: Optional[str] = None,
    ) -> Optional[dict]:
        return self._delegate.call_json(
            system, user, max_tokens, temperature, timeout,
            model_override=model_override,
        )

    def call_with_meta(
        self,
        system: str,
        user: str,
        max_tokens: int = 8192,
        temperature: float = 0.3,
        timeout: Optional[int] = None,
        model_override: Optional[str] = None,
    ) -> AICallResult:
        return self._delegate.call_with_meta(
            system, user, max_tokens, temperature, timeout,
            model_override=model_override,
        )
