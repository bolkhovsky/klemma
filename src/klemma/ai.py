"""Claude API client via anthropic SDK."""

import json
import logging
from pathlib import Path
from typing import Optional

import anthropic

from .config import AIConfig

logger = logging.getLogger(__name__)


class ClaudeClient:
    """Wrapper around Anthropic SDK for structured AI calls."""

    def __init__(self, config: AIConfig):
        api_key = config.api_key
        if not api_key:
            raise ValueError(
                f"API key not found. Set {config.api_key_env} environment variable."
            )
        self.client = anthropic.Anthropic(api_key=api_key)
        self.model = config.model
        self.timeout = config.timeout
        self.retries = config.retries
        self.max_pdf_chars = config.max_pdf_chars

    def call(
        self,
        system: str,
        user: str,
        max_tokens: int = 8192,
        temperature: float = 0.3,
    ) -> Optional[str]:
        """Make a Claude API call with retries.

        Returns the text response or None on failure.
        """
        for attempt in range(self.retries + 1):
            try:
                message = self.client.messages.create(
                    model=self.model,
                    max_tokens=max_tokens,
                    system=system,
                    messages=[{"role": "user", "content": user}],
                    temperature=temperature,
                    timeout=self.timeout,
                )
                return message.content[0].text
            except anthropic.APITimeoutError:
                logger.warning("Timeout (attempt %d/%d)", attempt + 1, self.retries + 1)
            except anthropic.RateLimitError:
                logger.warning("Rate limited (attempt %d/%d)", attempt + 1, self.retries + 1)
            except anthropic.APIError as e:
                logger.error("API error (attempt %d/%d): %s", attempt + 1, self.retries + 1, e)
        return None

    def call_json(
        self,
        system: str,
        user: str,
        max_tokens: int = 8192,
        temperature: float = 0.2,
    ) -> Optional[dict]:
        """Make a Claude API call and parse JSON from the response."""
        text = self.call(system, user, max_tokens=max_tokens, temperature=temperature)
        if not text:
            return None
        return self._extract_json(text)

    def render_prompt(self, template_path: Path, **kwargs) -> str:
        """Load a prompt template and render with Jinja2."""
        from jinja2 import Template

        raw = template_path.read_text(encoding="utf-8")
        return Template(raw).render(**kwargs)

    @staticmethod
    def _extract_json(text: str) -> Optional[dict]:
        """Extract JSON object from Claude's text response."""
        text = text.strip()

        # Strip markdown code blocks
        if "```" in text:
            lines = text.split("\n")
            json_lines = []
            in_block = False
            for line in lines:
                if line.strip().startswith("```"):
                    if in_block:
                        break
                    in_block = True
                    continue
                if in_block:
                    json_lines.append(line)
            if json_lines:
                text = "\n".join(json_lines)

        # Find JSON boundaries
        start = text.find("{")
        end = text.rfind("}") + 1
        if start == -1 or end <= start:
            logger.error("No JSON found in response")
            return None

        try:
            return json.loads(text[start:end])
        except json.JSONDecodeError as e:
            logger.error("JSON parse error: %s", e)
            return None
