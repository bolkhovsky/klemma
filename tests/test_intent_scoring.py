"""Tests for citation intent scoring and schema migrations."""

import pytest

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
