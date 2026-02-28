"""OpenAI-compatible AI backend.

Works with: OpenAI API, Ollama (/v1), vLLM, LM Studio, llama-cpp-python server.
Install: pip install klemma[openai]
"""

import json
import logging
from typing import Optional

from .ai import AIProviderBase, extract_json
from .config import AIConfig

logger = logging.getLogger(__name__)


class OpenAIClient(AIProviderBase):
    """AI backend using the OpenAI Python SDK (chat completions API)."""

    def __init__(self, config: AIConfig):
        super().__init__(config)
        try:
            from openai import OpenAI
        except ImportError:
            raise ImportError(
                "OpenAI backend requires the 'openai' package. "
                "Install with: pip install klemma[openai]"
            )

        self._client = OpenAI(
            api_key=config.api_key or "not-needed",
            base_url=config.base_url,
        )
        self._json_mode = config.json_mode

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
    ) -> Optional[str]:
        """Call OpenAI-compatible chat completions API with retries."""
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]

        kwargs = {
            "model": self.model,
            "messages": messages,
            **self._token_kwargs(max_tokens),
        }
        if not self._is_reasoning_model:
            kwargs["temperature"] = temperature

        for attempt in range(self.retries + 1):
            try:
                response = self._client.chat.completions.create(**kwargs)
                return response.choices[0].message.content
            except Exception as e:
                logger.warning(
                    "OpenAI API error (attempt %d/%d): %s",
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

        # JSON mode: request structured output from the API
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]

        kwargs = {
            "model": self.model,
            "messages": messages,
            "response_format": {"type": "json_object"},
            **self._token_kwargs(max_tokens),
        }
        if not self._is_reasoning_model:
            kwargs["temperature"] = temperature

        for attempt in range(self.retries + 1):
            try:
                response = self._client.chat.completions.create(**kwargs)
                text = response.choices[0].message.content
                if not text:
                    continue
                try:
                    return json.loads(text)
                except json.JSONDecodeError:
                    return extract_json(text)
            except Exception as e:
                logger.warning(
                    "OpenAI API error (attempt %d/%d): %s",
                    attempt + 1, self.retries + 1, e,
                )
        return None
