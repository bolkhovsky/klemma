"""Tests for reassign --apply: fragment reassignment + vault frontmatter updates."""

import pytest
import yaml

from klemma.state import StateManager
from klemma.vault import VaultAdapter


def _make_note(sections=None, extra=None):
    """Create a minimal vault note with YAML frontmatter."""
    fm = {"citekey": "testKey2023", "title": "Test Paper"}
    if sections is not None:
        fm["sections"] = sections
    if extra:
        fm.update(extra)
    body = yaml.dump(fm, default_flow_style=False, allow_unicode=True, sort_keys=False)
    return f"---\n{body}---\n\n> Some content here\n"


class TestUpdateFrontmatterSections:
    """Test VaultAdapter.update_frontmatter_sections()."""

    def test_adds_new_sections(self, tmp_path):
        vault = VaultAdapter(str(tmp_path), use_cli=False)
        note = tmp_path / "@testKey2023.md"
        note.write_text(_make_note(sections=["1.1", "2.3"]))

        ok = vault.update_frontmatter_sections("@testKey2023", ["1.1", "2.3", "3.2"])
        assert ok is True

        props = vault.get_properties("@testKey2023")
        assert set(props["sections"]) == {"1.1", "2.3", "3.2"}

    def test_sorts_sections_numerically(self, tmp_path):
        vault = VaultAdapter(str(tmp_path), use_cli=False)
        note = tmp_path / "@testKey2023.md"
        note.write_text(_make_note(sections=["3.2"]))

        vault.update_frontmatter_sections("@testKey2023", ["3.2", "1.1", "2.3"])
        props = vault.get_properties("@testKey2023")
        assert props["sections"] == ["1.1", "2.3", "3.2"]

    def test_preserves_other_frontmatter(self, tmp_path):
        vault = VaultAdapter(str(tmp_path), use_cli=False)
        note = tmp_path / "@testKey2023.md"
        note.write_text(_make_note(
            sections=["1.1"],
            extra={"quality": 4, "chapter": 3, "tags": ["Sea Ice"]},
        ))

        vault.update_frontmatter_sections("@testKey2023", ["1.1", "2.3"])
        props = vault.get_properties("@testKey2023")
        assert props["quality"] == 4
        assert props["chapter"] == 3
        assert props["tags"] == ["Sea Ice"]
        assert set(props["sections"]) == {"1.1", "2.3"}

    def test_preserves_body_content(self, tmp_path):
        vault = VaultAdapter(str(tmp_path), use_cli=False)
        note = tmp_path / "@testKey2023.md"
        note.write_text(_make_note(sections=["1.1"]))

        vault.update_frontmatter_sections("@testKey2023", ["1.1", "2.3"])
        text = note.read_text()
        assert "> Some content here" in text

    def test_returns_false_for_missing_note(self, tmp_path):
        vault = VaultAdapter(str(tmp_path), use_cli=False)
        ok = vault.update_frontmatter_sections("@nonexistent", ["1.1"])
        assert ok is False

    def test_returns_false_for_no_frontmatter(self, tmp_path):
        vault = VaultAdapter(str(tmp_path), use_cli=False)
        note = tmp_path / "@testKey2023.md"
        note.write_text("No frontmatter here, just text.")

        ok = vault.update_frontmatter_sections("@testKey2023", ["1.1"])
        assert ok is False

    def test_creates_sections_from_empty(self, tmp_path):
        vault = VaultAdapter(str(tmp_path), use_cli=False)
        note = tmp_path / "@testKey2023.md"
        note.write_text(_make_note(sections=None))

        vault.update_frontmatter_sections("@testKey2023", ["2.1", "3.3"])
        props = vault.get_properties("@testKey2023")
        assert props["sections"] == ["2.1", "3.3"]

    def test_folder_parameter(self, tmp_path):
        vault = VaultAdapter(str(tmp_path), use_cli=False)
        refs = tmp_path / "2 - Refs"
        refs.mkdir()
        note = refs / "@testKey2023.md"
        note.write_text(_make_note(sections=["1.1"]))

        ok = vault.update_frontmatter_sections(
            "@testKey2023", ["1.1", "3.2"], folder="2 - Refs",
        )
        assert ok is True
        props = vault.get_properties("@testKey2023")
        assert "3.2" in props["sections"]

    def test_unicode_preserved(self, tmp_path):
        vault = VaultAdapter(str(tmp_path), use_cli=False)
        note = tmp_path / "@testKey2023.md"
        content = _make_note(
            sections=["1.1"],
            extra={"title": "Прогнозирование ледовой обстановки"},
        )
        note.write_text(content)

        vault.update_frontmatter_sections("@testKey2023", ["1.1", "2.3"])
        props = vault.get_properties("@testKey2023")
        assert props["title"] == "Прогнозирование ледовой обстановки"
        assert set(props["sections"]) == {"1.1", "2.3"}


class TestUpdateFragmentSection:
    """Test fragment-level section reassignment in DB."""

    @pytest.fixture
    def state(self, tmp_path):
        db_path = tmp_path / "test.db"
        return StateManager(str(db_path))

    def test_reassign_fragment_section(self, state):
        state.register_sources(["src1"])
        state.fragments.save_fragments("src1", [
            {"text": "Fragment about ice prediction", "type": "key_idea",
             "section": "1.4", "relevance": 5},
        ])
        # Verify original
        frags = state.get_fragments(source_id="src1")
        assert frags[0]["section"] == "1.4"

        # Reassign
        ok = state.update_fragment_section(frags[0]["id"], "3.3")
        assert ok is True

        # Verify updated
        frags = state.get_fragments(source_id="src1")
        assert frags[0]["section"] == "3.3"

    def test_reassign_nonexistent_fragment(self, state):
        ok = state.update_fragment_section(99999, "1.1")
        assert ok is False

    def test_reassign_preserves_other_fields(self, state):
        state.register_sources(["src1"])
        state.fragments.save_fragments("src1", [
            {"text": "Important fragment", "type": "methodology",
             "section": "2.1", "relevance": 4, "citation_intent": "method"},
        ])
        frags = state.get_fragments(source_id="src1")
        frag_id = frags[0]["id"]

        state.update_fragment_section(frag_id, "3.2")

        frags = state.get_fragments(source_id="src1")
        assert frags[0]["section"] == "3.2"
        assert frags[0]["fragment_type"] == "methodology"
        assert frags[0]["relevance_score"] == 4
        assert frags[0]["citation_intent"] == "method"
