"""LiteLLM AI backend — unified interface for 100+ providers.

Model format: provider/model (e.g. "anthropic/claude-sonnet-4-5-20250929", "ollama/llama3").
Install: pip install klemma[litellm]
"""

import logging
from typing import Optional

from .ai import AIProviderBase
from .config import AIConfig

logger = logging.getLogger(__name__)


class LiteLLMClient(AIProviderBase):
    """AI backend using LiteLLM for multi-provider support."""

    def __init__(self, config: AIConfig):
        super().__init__(config)
        try:
            import litellm as _litellm
        except ImportError:
            raise ImportError(
                "LiteLLM backend requires the 'litellm' package. "
                "Install with: pip install klemma[litellm]"
            )

        self._litellm = _litellm
        self._litellm.request_timeout = config.timeout

        if config.api_key:
            self._api_key = config.api_key
        else:
            self._api_key = None

    def call(
        self,
        system: str,
        user: str,
        max_tokens: int = 8192,
        temperature: float = 0.3,
        timeout: Optional[int] = None,
    ) -> Optional[str]:
        """Call LiteLLM completion with retries."""
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]

        for attempt in range(self.retries + 1):
            try:
                kwargs = {
                    "model": self.model,
                    "messages": messages,
                    "max_tokens": max_tokens,
                    "temperature": temperature,
                }
                if timeout:
                    kwargs["timeout"] = timeout
                if self._api_key:
                    kwargs["api_key"] = self._api_key
                response = self._litellm.completion(**kwargs)
                return response.choices[0].message.content
            except Exception as e:
                logger.warning(
                    "LiteLLM error (attempt %d/%d): %s",
                    attempt + 1, self.retries + 1, e,
                )
        return None
