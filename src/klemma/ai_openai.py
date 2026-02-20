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

    def call(
        self,
        system: str,
        user: str,
        max_tokens: int = 8192,
        temperature: float = 0.3,
    ) -> Optional[str]:
        """Call OpenAI-compatible chat completions API with retries."""
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]

        for attempt in range(self.retries + 1):
            try:
                response = self._client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    max_tokens=max_tokens,
                    temperature=temperature,
                )
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
    ) -> Optional[dict]:
        """Call API and parse JSON, optionally using structured JSON mode."""
        if not self._json_mode:
            return super().call_json(system, user, max_tokens, temperature)

        # JSON mode: request structured output from the API
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]

        for attempt in range(self.retries + 1):
            try:
                response = self._client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    response_format={"type": "json_object"},
                )
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
