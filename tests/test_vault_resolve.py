"""Tests for `klemma.vault.resolve_notes_root` — the single source of truth
for where annotated `@citekey.md` notes land. Local default, Obsidian vault
override, and the flat-vault edge case are the three shapes to lock in.
"""

from __future__ import annotations

from pathlib import Path

from klemma.config import KlemmaConfig, ObsidianConfig
from klemma.vault import resolve_notes_root


def _cfg(**obsidian_kwargs) -> KlemmaConfig:
    cfg = KlemmaConfig()
    cfg.obsidian = ObsidianConfig(**obsidian_kwargs)
    return cfg


def test_resolve_notes_root_default(tmp_path: Path) -> None:
    project_root = tmp_path / "my_project"
    project_root.mkdir()

    cfg = _cfg()
    result = resolve_notes_root(cfg, project_root)

    assert result == project_root / ".klemma" / "notes"


def test_resolve_notes_root_obsidian_with_folder(tmp_path: Path) -> None:
    vault = tmp_path / "obsidian_vault"
    vault.mkdir()

    cfg = _cfg(vault_path=str(vault), notes_folder="References")
    result = resolve_notes_root(cfg, tmp_path / "ignored_project")

    assert result == vault / "References"


def test_resolve_notes_root_obsidian_flat_vault(tmp_path: Path) -> None:
    vault = tmp_path / "flat_vault"
    vault.mkdir()

    cfg = _cfg(vault_path=str(vault), notes_folder="")
    result = resolve_notes_root(cfg, tmp_path / "ignored_project")

    assert result == vault


def test_resolve_notes_root_expands_tilde(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    cfg = _cfg(vault_path="~/vault", notes_folder="Refs")
    result = resolve_notes_root(cfg, tmp_path / "project")
    assert result == tmp_path / "vault" / "Refs"


def test_resolve_notes_root_whitespace_only_vault_path_uses_local(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    cfg = _cfg(vault_path="   ", notes_folder="References")
    result = resolve_notes_root(cfg, project_root)
    assert result == project_root / ".klemma" / "notes"
