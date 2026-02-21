"""KlemmaContext — единый объект контекста, создаётся раз за CLI-команду."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from .ai import AIProvider
    from .config import KlemmaConfig, ProjectConfig
    from .library_provider import LibraryProvider
    from .state import StateManager
    from .tools.registry import ToolRegistry
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
    ai: Optional[AIProvider] = None
    library: Optional[LibraryProvider] = None
    tools: Optional[ToolRegistry] = None
    project: Optional[ProjectConfig] = None
    project_name: str = "default"
    # Points to active project's .klemma/ dir (backward-compat with skills)
    klemma_home: Path = field(default_factory=lambda: Path.home() / ".klemma")
    dissertation_context: str = ""
    available_tags: list[str] = field(default_factory=list)
    # New: per-directory project support
    project_root: Optional[Path] = None  # directory containing .klemma/
    project_chain: list[Path] = field(default_factory=list)  # child-first chain
    system_home: Path = field(default_factory=lambda: Path.home() / ".klemma")
