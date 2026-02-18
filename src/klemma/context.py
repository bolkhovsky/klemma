"""KlemmaContext — единый объект контекста, создаётся раз за CLI-команду."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from .ai import ClaudeClient
    from .config import KlemmaConfig
    from .library_provider import LibraryProvider
    from .state import StateManager
    from .vault import VaultAdapter


@dataclass
class KlemmaContext:
    """All dependencies for a single CLI command invocation.

    Created once in _init_components(), passed to skills.
    Replaces the (config, state, vault, ai, entry_lookup) tuple pattern.
    """

    config: KlemmaConfig
    state: StateManager
    vault: VaultAdapter
    ai: Optional[ClaudeClient] = None
    library: Optional[LibraryProvider] = None
    tools: Optional[object] = None  # ToolRegistry, added in Phase 2
