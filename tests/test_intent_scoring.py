"""Tests for citation intent scoring and schema migrations."""

import pytest
from pydantic import ValidationError

from klemma.literature.models import Fragment
from klemma.state import StateManager


@pytest.fixture
def state(tmp_path):
    """Create a StateManager with a temporary database."""
    db_path = tmp_path / "test.db"
    return StateManager(db_path)


class TestMigrateSchema:
    """Tests for _migrate_schema() infrastructure."""

    def test_schema_version_is_one_after_init(self, state):
        """New databases migrate to version 1."""
        with state._conn() as conn:
            version = conn.execute("PRAGMA user_version").fetchone()[0]
        assert version == 1

    def test_migration_is_idempotent(self, state):
        """Running _migrate_schema() multiple times is safe."""
        with state._conn() as conn:
            state._migrate_schema(conn)
            state._migrate_schema(conn)
            version = conn.execute("PRAGMA user_version").fetchone()[0]
        assert version == 1

    def test_base_tables_exist(self, state):
        """All base schema tables are created."""
        expected_tables = {
            "sources",
            "fragments",
            "reference_gaps",
            "source_sections",
            "daily_plans",
            "reading_queue",
            "daily_batches",
            "prune_verdicts",
        }
        with state._conn() as conn:
            rows = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
            tables = {row[0] for row in rows}
        assert expected_tables.issubset(tables)

    def test_fragments_has_citation_intent_column(self, state):
        """Migration v1 adds citation_intent to fragments."""
        with state._conn() as conn:
            cols = {
                row[1] for row in conn.execute("PRAGMA table_info(fragments)")
            }
        assert "citation_intent" in cols

    def test_reference_gaps_has_citation_intent_column(self, state):
        """Migration v1 adds citation_intent to reference_gaps."""
        with state._conn() as conn:
            cols = {
                row[1]
                for row in conn.execute("PRAGMA table_info(reference_gaps)")
            }
        assert "citation_intent" in cols


class TestFragmentCitationIntent:
    """Tests for Fragment model with citation_intent field."""

    def test_fragment_without_intent(self):
        """Fragment without citation_intent defaults to None."""
        f = Fragment(text="Some text")
        assert f.citation_intent is None

    def test_fragment_with_background(self):
        f = Fragment(text="Some text", citation_intent="background")
        assert f.citation_intent == "background"

    def test_fragment_with_method(self):
        f = Fragment(text="Some text", citation_intent="method")
        assert f.citation_intent == "method"

    def test_fragment_with_result_comparison(self):
        f = Fragment(text="Some text", citation_intent="result_comparison")
        assert f.citation_intent == "result_comparison"

    def test_fragment_with_invalid_intent_raises(self):
        with pytest.raises(ValidationError):
            Fragment(text="Some text", citation_intent="invalid_value")

    def test_fragment_from_json_with_intent(self):
        """Simulate parsing LLM JSON output with citation_intent."""
        data = {
            "text": "SPECTER uses citation-informed objectives",
            "type": "methodology",
            "chapter": 2,
            "section": "2.3",
            "relevance": 4,
            "usage_hint": "Use as method reference",
            "page": 3,
            "citation_intent": "method",
        }
        f = Fragment(**data)
        assert f.citation_intent == "method"
        assert f.relevance == 4

    def test_fragment_from_json_without_intent(self):
        """LLM might omit citation_intent — should default to None."""
        data = {
            "text": "Some finding",
            "type": "result",
            "chapter": 3,
            "section": "3.1",
            "relevance": 3,
            "usage_hint": "Compare results",
            "page": 7,
        }
        f = Fragment(**data)
        assert f.citation_intent is None


class TestSaveFragmentsWithIntent:
    """Tests for save_fragments() persisting citation_intent."""

    def test_save_fragment_with_intent(self, state):
        """citation_intent is saved to DB."""
        state.register_sources(["test_source"])
        state.save_fragments("test_source", [
            {
                "text": "Method X achieves 95% accuracy",
                "type": "result",
                "chapter": 3,
                "section": "3.1",
                "relevance": 4,
                "usage_hint": "Compare results",
                "page": 5,
                "citation_intent": "result_comparison",
            }
        ])
        frags = state.get_fragments(source_id="test_source")
        assert len(frags) == 1
        assert frags[0]["citation_intent"] == "result_comparison"

    def test_save_fragment_without_intent(self, state):
        """Fragments without intent save NULL — backward compatible."""
        state.register_sources(["test_source"])
        state.save_fragments("test_source", [
            {
                "text": "Some old fragment",
                "type": "key_idea",
                "relevance": 3,
            }
        ])
        frags = state.get_fragments(source_id="test_source")
        assert len(frags) == 1
        assert frags[0]["citation_intent"] is None

    def test_save_multiple_intents(self, state):
        """Multiple fragments with different intents."""
        state.register_sources(["src1"])
        state.save_fragments("src1", [
            {"text": "Background info", "citation_intent": "background"},
            {"text": "Method description", "citation_intent": "method"},
            {"text": "Result comparison", "citation_intent": "result_comparison"},
            {"text": "No intent given"},
        ])
        frags = state.get_fragments(source_id="src1")
        intents = [f["citation_intent"] for f in frags]
        assert "background" in intents
        assert "method" in intents
        assert "result_comparison" in intents
        assert None in intents


class TestSaveReferenceGapsWithIntent:
    """Tests for save_reference_gaps() persisting citation_intent."""

    def test_save_gap_with_intent(self, state):
        """citation_intent is saved for reference gaps."""
        state.register_sources(["src1"])
        state.save_reference_gaps("src1", [
            {
                "authors": "Smith et al.",
                "year": 2020,
                "title": "Important Method Paper",
                "why_relevant": "Core method reference",
                "citation_intent": "method",
                "dissertation_sections": ["2.3"],
            }
        ])
        gaps = state.get_reference_gaps()
        assert len(gaps) >= 1
        # Find our gap
        found = [g for g in gaps if "Smith" in g["ref_authors"]]
        assert len(found) == 1

    def test_save_gap_without_intent(self, state):
        """Gaps without intent save NULL — backward compatible."""
        state.register_sources(["src1"])
        state.save_reference_gaps("src1", [
            {
                "authors": "Jones et al.",
                "year": 2019,
                "title": "Background Paper",
                "why_relevant": "General context",
            }
        ])
        gaps = state.get_reference_gaps()
        assert len(gaps) >= 1
