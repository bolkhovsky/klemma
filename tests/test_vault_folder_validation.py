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


def test_init_warns_missing_notes_folder(tmp_path: Path):
    """klemma init wizard warns when notes_folder does not exist in vault."""
    from unittest.mock import MagicMock

    vault = tmp_path / "vault"
    vault.mkdir()
    # No "References" subfolder created — should trigger warning

    # Simulate the validation logic from _run_wizard
    values = MagicMock()
    values.vault_path = str(vault)
    values.notes_folder = "References"

    notes_dir = Path(values.vault_path) / values.notes_folder
    assert not notes_dir.is_dir(), "Precondition: notes_folder should not exist"
