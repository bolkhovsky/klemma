"""Claude Code CLI wrapper for AI calls."""

import json
import logging
import subprocess
from pathlib import Path
from typing import Optional

from .config import AIConfig

logger = logging.getLogger(__name__)


class ClaudeClient:
    """Wrapper around Claude Code CLI (claude -p)."""

    def __init__(self, config: AIConfig):
        self.model = config.model
        self.timeout = config.timeout
        self.retries = config.retries
        self.max_pdf_chars = config.max_pdf_chars

        if not self.check_cli_available():
            raise RuntimeError(
                "'claude' command not found. Install Claude Code CLI: https://claude.ai/code"
            )

    def call(
        self,
        system: str,
        user: str,
        max_tokens: int = 8192,
        temperature: float = 0.3,
    ) -> Optional[str]:
        """Call Claude via CLI with retries.

        Returns the text response or None on failure.
        """
        prompt = f"{system}\n\n---\n\n{user}"

        for attempt in range(self.retries + 1):
            try:
                result = subprocess.run(
                    ["claude", "-p", "--model", self.model, prompt],
                    capture_output=True,
                    text=True,
                    timeout=self.timeout,
                )
                if result.returncode != 0:
                    logger.warning(
                        "Claude CLI error (attempt %d/%d): %s",
                        attempt + 1, self.retries + 1,
                        result.stderr[:200],
                    )
                    continue
                return result.stdout
            except subprocess.TimeoutExpired:
                logger.warning("Timeout (attempt %d/%d)", attempt + 1, self.retries + 1)
            except FileNotFoundError:
                logger.error("'claude' command not found in PATH")
                return None
            except Exception as e:
                logger.error("Error (attempt %d/%d): %s", attempt + 1, self.retries + 1, e)
        return None

    def call_json(
        self,
        system: str,
        user: str,
        max_tokens: int = 8192,
        temperature: float = 0.2,
    ) -> Optional[dict]:
        """Call Claude CLI and parse JSON from the response."""
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
    def check_cli_available() -> bool:
        """Check if Claude Code CLI is available."""
        try:
            result = subprocess.run(
                ["claude", "--version"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            return result.returncode == 0
        except Exception:
            return False

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
