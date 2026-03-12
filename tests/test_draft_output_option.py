"""Tests for _resolve_output helper and draft -s --output flag (issue #92)."""

from klemma.commands.write import _resolve_output


class TestResolveOutput:
    def test_default_path_uses_notes_drafts(self, tmp_path):
        result = _resolve_output(None, tmp_path, "1.1", no_save=False)
        assert result == tmp_path / "notes" / "drafts" / "Draft_1.1.md"

    def test_output_flag_overrides_default(self, tmp_path):
        custom = tmp_path / "my_draft.md"
        result = _resolve_output(str(custom), tmp_path, "1.1", no_save=False)
        assert result == custom

    def test_no_save_returns_none_even_with_output(self, tmp_path):
        custom = tmp_path / "my_draft.md"
        result = _resolve_output(str(custom), tmp_path, "1.1", no_save=True)
        assert result is None

    def test_no_save_returns_none_with_default(self, tmp_path):
        result = _resolve_output(None, tmp_path, "2.3", no_save=True)
        assert result is None

    def test_no_project_root_returns_none(self):
        result = _resolve_output(None, None, "1.1", no_save=False)
        assert result is None

    def test_output_with_tilde_expands(self, tmp_path, monkeypatch):
        # expanduser should be applied
        monkeypatch.setenv("HOME", str(tmp_path))
        result = _resolve_output("~/output.md", None, "1.1", no_save=False)
        assert result == tmp_path / "output.md"

    def test_section_id_embedded_in_default_filename(self, tmp_path):
        result = _resolve_output(None, tmp_path, "3.2.1", no_save=False)
        assert result is not None
        assert result.name == "Draft_3.2.1.md"
