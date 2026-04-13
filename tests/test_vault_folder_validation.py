"""Tests for vault folder validation (notes_folder / tags_folder)."""

from pathlib import Path

from klemma.vault import VaultAdapter


def test_check_folder_exists(tmp_path: Path):
    """check_folder returns True for an existing subfolder."""
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "References").mkdir()

    adapter = VaultAdapter(str(vault), use_cli=False)
    assert adapter.check_folder("References") is True


def test_check_folder_missing(tmp_path: Path):
    """check_folder returns False for a non-existent subfolder."""
    vault = tmp_path / "vault"
    vault.mkdir()

    adapter = VaultAdapter(str(vault), use_cli=False)
    assert adapter.check_folder("References") is False


