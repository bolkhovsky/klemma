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

    def test_initial_schema_version_is_zero(self, state):
        """New databases start at schema version 0."""
        with state._conn() as conn:
            version = conn.execute("PRAGMA user_version").fetchone()[0]
        assert version == 0

    def test_migration_is_idempotent(self, state):
        """Running _migrate_schema() multiple times is safe."""
        with state._conn() as conn:
            state._migrate_schema(conn)
            state._migrate_schema(conn)
            version = conn.execute("PRAGMA user_version").fetchone()[0]
        assert version == 0

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
