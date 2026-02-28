"""AI provider abstraction with pluggable backends.

Default backend: Claude Code CLI (claude -p).
Optional backends: OpenAI-compatible API, LiteLLM.
"""

import json
import logging
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Protocol, runtime_checkable

from .config import AIConfig

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Shared utility
# ---------------------------------------------------------------------------

def extract_json(text: str) -> Optional[dict]:
    """Extract a JSON object from an AI text response.

    Handles markdown code blocks and leading/trailing text around JSON.
    """
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


@dataclass
class AICallResult:
    """Result of an AI call with metadata for observability."""

    text: Optional[str] = None
    duration_ms: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    retries_used: int = 0
    model: str = ""
    error: Optional[str] = None

    def __bool__(self) -> bool:
        return self.text is not None


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------

@runtime_checkable
class AIProvider(Protocol):
    """Contract for all AI backends.

    Follows the LibraryProvider protocol pattern from library_provider.py.
    """

    model: str
    max_pdf_chars: int

    def call(
        self,
        system: str,
        user: str,
        max_tokens: int = 8192,
        temperature: float = 0.3,
        timeout: Optional[int] = None,
    ) -> Optional[str]: ...

    def call_json(
        self,
        system: str,
        user: str,
        max_tokens: int = 8192,
        temperature: float = 0.2,
        timeout: Optional[int] = None,
    ) -> Optional[dict]: ...

    def call_with_meta(
        self,
        system: str,
        user: str,
        max_tokens: int = 8192,
        temperature: float = 0.3,
        timeout: Optional[int] = None,
    ) -> AICallResult: ...

    def render_prompt(self, template_path: Path, **kwargs) -> str: ...

    @property
    def interactive_available(self) -> bool: ...


# ---------------------------------------------------------------------------
# Base class with shared logic
# ---------------------------------------------------------------------------

class AIProviderBase:
    """Shared implementation for AI backends.

    Subclasses only need to override ``call()``.
    """

    def __init__(self, config: AIConfig):
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
        timeout: Optional[int] = None,
    ) -> Optional[str]:
        raise NotImplementedError

    def call_json(
        self,
        system: str,
        user: str,
        max_tokens: int = 8192,
        temperature: float = 0.2,
        timeout: Optional[int] = None,
    ) -> Optional[dict]:
        """Call the backend and parse JSON from the response."""
        text = self.call(system, user, max_tokens=max_tokens, temperature=temperature, timeout=timeout)
        if not text:
            return None
        return extract_json(text)

    def call_with_meta(
        self,
        system: str,
        user: str,
        max_tokens: int = 8192,
        temperature: float = 0.3,
        timeout: Optional[int] = None,
    ) -> AICallResult:
        """Call the backend and return result with metadata."""
        t0 = time.monotonic()
        text = self.call(system, user, max_tokens=max_tokens, temperature=temperature, timeout=timeout)
        elapsed = int((time.monotonic() - t0) * 1000)
        return AICallResult(
            text=text,
            duration_ms=elapsed,
            model=self.model,
            error=None if text else "all retries exhausted",
        )

    def render_prompt(self, template_path: Path, **kwargs) -> str:
        """Load a prompt template and render with Jinja2."""
        from jinja2 import Template

        raw = template_path.read_text(encoding="utf-8")
        return Template(raw).render(**kwargs)

    @property
    def interactive_available(self) -> bool:
        """Whether this backend supports interactive terminal sessions."""
        return False


# ---------------------------------------------------------------------------
# Claude Code CLI backend (default)
# ---------------------------------------------------------------------------

class ClaudeClient(AIProviderBase):
    """Wrapper around Claude Code CLI (claude -p)."""

    def __init__(self, config: AIConfig):
        super().__init__(config)
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
        timeout: Optional[int] = None,
    ) -> Optional[str]:
        """Call Claude via CLI with retries.

        Returns the text response or None on failure.
        """
        effective_timeout = timeout or self.timeout
        prompt = f"{system}\n\n---\n\n{user}"

        for attempt in range(self.retries + 1):
            try:
                result = subprocess.run(
                    ["claude", "-p", "--model", self.model, prompt],
                    capture_output=True,
                    text=True,
                    timeout=effective_timeout,
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

    @property
    def interactive_available(self) -> bool:
        return True

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


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def create_ai(config: AIConfig) -> AIProvider:
    """Create the right AI backend from config.

    config.backend == "claude"  → ClaudeClient (interactive CLI)
    config.backend == "litellm" → LiteLLMClient (recommended — 100+ providers)
    config.backend == "openai"  → OpenAIClient (deprecated — delegates to LiteLLM)
    """
    backend = config.backend

    if backend == "claude":
        return ClaudeClient(config)

    if backend == "litellm":
        from .ai_litellm import LiteLLMClient
        return LiteLLMClient(config)

    if backend == "openai":
        from .ai_openai import OpenAIClient
        return OpenAIClient(config)

    raise ValueError(
        f"Unknown AI backend: {backend!r}. Supported: claude, litellm, openai"
    )
