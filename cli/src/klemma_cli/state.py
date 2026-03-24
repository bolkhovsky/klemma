"""Sync state persistence — reads/writes .klemma/sync_config.json."""

from __future__ import annotations

import json
from pathlib import Path

from .models import SyncConfig


def _config_path(project_root: Path) -> Path:
    return project_root / ".klemma" / "sync_config.json"


def load_sync_config(project_root: Path) -> SyncConfig | None:
    """Load sync config from .klemma/sync_config.json, or None if not linked."""
    path = _config_path(project_root)
    if not path.exists():
        return None
    data = json.loads(path.read_text())
    return SyncConfig(**data)


def save_sync_config(project_root: Path, config: SyncConfig) -> None:
    """Save sync config to .klemma/sync_config.json."""
    path = _config_path(project_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config.model_dump(), indent=2) + "\n")
