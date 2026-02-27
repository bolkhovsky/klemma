"""LiteLLM AI backend — unified interface for 100+ providers.

Model format: provider/model (e.g. "anthropic/claude-sonnet-4-5-20250929", "ollama/llama3").
Bare model names (e.g. "gpt-4.1") also work — LiteLLM routes them to OpenAI.
Install: pip install klemma[litellm]
"""

import json
import logging
import re
from typing import Optional

from .ai import AIProviderBase, extract_json
from .config import AIConfig

logger = logging.getLogger(__name__)

# Patterns for reasoning models that need max_completion_tokens instead of max_tokens
_REASONING_RE = re.compile(r"^(o1|o3|o4|gpt-5)", re.IGNORECASE)


class LiteLLMClient(AIProviderBase):
    """AI backend using LiteLLM for multi-provider support.

    Feature parity with OpenAIClient:
    - json_mode (response_format)
    - base_url passthrough
    - reasoning model detection (o-series, gpt-5)
    """

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
        self._api_key = config.api_key or None
        self._base_url = config.base_url
        self._json_mode = config.json_mode

    @property
    def _is_reasoning_model(self) -> bool:
        """Detect reasoning models that require different API parameters."""
        # Strip provider prefix (e.g. "openai/o3-mini" → "o3-mini")
        bare = self.model.split("/")[-1] if "/" in self.model else self.model
        return bool(_REASONING_RE.match(bare))

    def _build_kwargs(
        self,
        messages: list[dict],
        max_tokens: int,
        temperature: float,
        timeout: Optional[int] = None,
        response_format: Optional[dict] = None,
    ) -> dict:
        """Build kwargs dict for litellm.completion() with all conditionals."""
        kwargs: dict = {
            "model": self.model,
            "messages": messages,
        }
        # Token limit
        if self._is_reasoning_model:
            kwargs["max_completion_tokens"] = max_tokens
        else:
            kwargs["max_tokens"] = max_tokens
            kwargs["temperature"] = temperature
        # Optional params
        if timeout:
            kwargs["timeout"] = timeout
        if self._api_key:
            kwargs["api_key"] = self._api_key
        if self._base_url:
            kwargs["base_url"] = self._base_url
        if response_format:
            kwargs["response_format"] = response_format
        return kwargs

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
        kwargs = self._build_kwargs(messages, max_tokens, temperature, timeout)

        for attempt in range(self.retries + 1):
            try:
                response = self._litellm.completion(**kwargs)
                return response.choices[0].message.content
            except Exception as e:
                logger.warning(
                    "LiteLLM error (attempt %d/%d): %s",
                    attempt + 1, self.retries + 1, e,
                )
        return None

    def call_json(
        self,
        system: str,
        user: str,
        max_tokens: int = 8192,
        temperature: float = 0.2,
        timeout: Optional[int] = None,
    ) -> Optional[dict]:
        """Call API and parse JSON, optionally using structured JSON mode."""
        if not self._json_mode:
            return super().call_json(system, user, max_tokens, temperature, timeout)

        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        kwargs = self._build_kwargs(
            messages, max_tokens, temperature, timeout,
            response_format={"type": "json_object"},
        )

        for attempt in range(self.retries + 1):
            try:
                response = self._litellm.completion(**kwargs)
                text = response.choices[0].message.content
                if not text:
                    continue
                try:
                    return json.loads(text)
                except json.JSONDecodeError:
                    return extract_json(text)
            except Exception as e:
                logger.warning(
                    "LiteLLM error (attempt %d/%d): %s",
                    attempt + 1, self.retries + 1, e,
                )
        return None
