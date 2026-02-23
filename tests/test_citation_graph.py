"""Tests for citation graph: citation_links table, graph stats, co-citation."""

import pytest

from klemma.state import StateManager


@pytest.fixture
def state(tmp_path):
    """Create a StateManager with a temporary database."""
    db_path = tmp_path / "test.db"
    return StateManager(db_path)


class TestMigrationV3:
    """Test schema migration to version 3."""

    def test_schema_version_is_three(self, state):
        with state._conn() as conn:
            version = conn.execute("PRAGMA user_version").fetchone()[0]
        assert version == 3

    def test_citation_links_table_exists(self, state):
        with state._conn() as conn:
            rows = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='citation_links'"
            ).fetchall()
        assert len(rows) == 1

    def test_citation_links_columns(self, state):
        with state._conn() as conn:
            cols = {
                row[1] for row in conn.execute("PRAGMA table_info(citation_links)")
            }
        expected = {
            "source_id", "target_citekey", "target_title_hash",
            "target_title", "target_authors", "target_year",
            "citation_intent", "in_library",
        }
        assert expected.issubset(cols)


class TestSaveCitationLinks:
    """Tests for save_citation_links() and get_citation_links()."""

    def test_save_and_retrieve(self, state):
        state.register_sources(["src1"])
        state.save_citation_links("src1", [
            {
                "authors": "Smith et al.",
                "year": 2020,
                "title": "Important Paper",
                "citation_intent": "method",
                "in_library": True,
                "citekey": "Smith2020",
            },
            {
                "authors": "Jones et al.",
                "year": 2019,
                "title": "Another Paper",
                "citation_intent": "background",
                "in_library": False,
            },
        ])
        links = state.get_citation_links(source_id="src1")
        assert len(links) == 2

    def test_unique_constraint_on_title(self, state):
        """Saving same title twice from same source → upsert, not duplicate."""
        state.register_sources(["src1"])
        state.save_citation_links("src1", [
            {"authors": "A", "year": 2020, "title": "Same Paper", "in_library": False},
        ])
        state.save_citation_links("src1", [
            {"authors": "A", "year": 2020, "title": "Same Paper", "in_library": True},
        ])
        links = state.get_citation_links(source_id="src1")
        assert len(links) == 1
        assert links[0]["in_library"] == 1

    def test_title_normalization(self, state):
        """Title case variations should hash to the same value."""
        state.register_sources(["src1"])
        state.save_citation_links("src1", [
            {"authors": "A", "year": 2020, "title": "Some Paper Title"},
        ])
        state.save_citation_links("src1", [
            {"authors": "A", "year": 2020, "title": "some paper title"},
        ])
        links = state.get_citation_links(source_id="src1")
        assert len(links) == 1

    def test_different_sources_same_target(self, state):
        """Two sources can cite the same paper."""
        state.register_sources(["src1", "src2"])
        ref = {"authors": "X", "year": 2020, "title": "Shared Reference"}
        state.save_citation_links("src1", [ref])
        state.save_citation_links("src2", [ref])
        all_links = state.get_citation_links()
        assert len(all_links) == 2

    def test_empty_title_skipped(self, state):
        """References with empty title are skipped."""
        state.register_sources(["src1"])
        state.save_citation_links("src1", [
            {"authors": "A", "year": 2020, "title": ""},
            {"authors": "B", "year": 2021, "title": "Valid Title"},
        ])
        links = state.get_citation_links(source_id="src1")
        assert len(links) == 1


class TestCitationGraphStats:
    """Tests for get_citation_graph_stats()."""

    def _setup_graph(self, state):
        state.register_sources(["src1", "src2"])
        state.save_citation_links("src1", [
            {"authors": "A", "year": 2020, "title": "Internal Paper", "in_library": True, "citekey": "A2020"},
            {"authors": "B", "year": 2019, "title": "External Paper 1", "in_library": False},
            {"authors": "C", "year": 2021, "title": "External Paper 2", "in_library": False},
        ])
        state.save_citation_links("src2", [
            {"authors": "A", "year": 2020, "title": "Internal Paper", "in_library": True, "citekey": "A2020"},
            {"authors": "B", "year": 2019, "title": "External Paper 1", "in_library": False},
        ])

    def test_total_links(self, state):
        self._setup_graph(state)
        stats = state.get_citation_graph_stats()
        assert stats["total_links"] == 5

    def test_unique_targets(self, state):
        self._setup_graph(state)
        stats = state.get_citation_graph_stats()
        assert stats["unique_targets"] == 3

    def test_in_library_count(self, state):
        self._setup_graph(state)
        stats = state.get_citation_graph_stats()
        assert stats["in_library"] == 2
        assert stats["external"] == 3

    def test_avg_refs_per_source(self, state):
        self._setup_graph(state)
        stats = state.get_citation_graph_stats()
        assert stats["avg_refs_per_source"] == 2.5

    def test_most_cited_external(self, state):
        self._setup_graph(state)
        stats = state.get_citation_graph_stats()
        # "External Paper 1" is cited by both sources
        top = stats["most_cited_external"]
        assert len(top) >= 1
        assert top[0]["cite_count"] == 2

    def test_most_connected_internal(self, state):
        self._setup_graph(state)
        stats = state.get_citation_graph_stats()
        top = stats["most_connected_internal"]
        assert len(top) >= 1
        assert top[0]["target_citekey"] == "A2020"
        assert top[0]["cite_count"] == 2


class TestCoCitation:
    """Tests for get_co_cited()."""

    def test_co_cited_basic(self, state):
        state.register_sources(["src1", "src2"])
        state.save_citation_links("src1", [
            {"authors": "Target", "year": 2020, "title": "Target Paper", "in_library": True, "citekey": "Target2020"},
            {"authors": "CoCited", "year": 2019, "title": "Co-cited Paper", "in_library": False},
        ])
        state.save_citation_links("src2", [
            {"authors": "Target", "year": 2020, "title": "Target Paper", "in_library": True, "citekey": "Target2020"},
            {"authors": "CoCited", "year": 2019, "title": "Co-cited Paper", "in_library": False},
            {"authors": "Only-src2", "year": 2021, "title": "Unique to src2", "in_library": False},
        ])
        co = state.get_co_cited("Target2020")
        # Should find "Co-cited Paper" (appears in both sources with Target)
        titles = [r["target_title"] for r in co]
        assert "Co-cited Paper" in titles

    def test_co_cited_empty(self, state):
        """Citekey not in graph returns empty list."""
        result = state.get_co_cited("nonexistent")
        assert result == []


class TestAuthorNetwork:
    """Tests for get_key_author_groups()."""

    def test_empty_graph(self, state):
        assert state.get_key_author_groups() == []

    def test_single_paper_author_excluded(self, state):
        """Authors with only 1 paper should not appear (min_papers=2)."""
        state.register_sources(["src1"])
        state.save_citation_links("src1", [
            {"authors": "Smith et al.", "year": 2020, "title": "Only Paper"},
        ])
        result = state.get_key_author_groups(min_papers=2)
        assert len(result) == 0

    def test_multi_paper_author(self, state):
        """Author with 2+ unique papers should appear."""
        state.register_sources(["src1", "src2"])
        state.save_citation_links("src1", [
            {"authors": "Smith et al.", "year": 2020, "title": "Paper One"},
        ])
        state.save_citation_links("src2", [
            {"authors": "Smith et al.", "year": 2021, "title": "Paper Two"},
        ])
        result = state.get_key_author_groups(min_papers=2)
        assert len(result) == 1
        assert result[0]["surname"] == "Smith"
        assert result[0]["paper_count"] == 2

    def test_in_library_count(self, state):
        """Track how many of an author's papers are in our library."""
        state.register_sources(["src1"])
        state.save_citation_links("src1", [
            {"authors": "Jones et al.", "year": 2019, "title": "Paper A", "in_library": True, "citekey": "Jones2019"},
            {"authors": "Jones et al.", "year": 2020, "title": "Paper B", "in_library": False},
            {"authors": "Jones et al.", "year": 2021, "title": "Paper C", "in_library": True, "citekey": "Jones2021"},
        ])
        result = state.get_key_author_groups(min_papers=2)
        assert len(result) == 1
        assert result[0]["in_library_count"] == 2

    def test_sorted_by_count(self, state):
        """Groups sorted by paper count descending."""
        state.register_sources(["src1"])
        state.save_citation_links("src1", [
            {"authors": "Alpha et al.", "year": 2020, "title": "A1"},
            {"authors": "Alpha et al.", "year": 2021, "title": "A2"},
            {"authors": "Beta et al.", "year": 2019, "title": "B1"},
            {"authors": "Beta et al.", "year": 2020, "title": "B2"},
            {"authors": "Beta et al.", "year": 2021, "title": "B3"},
        ])
        result = state.get_key_author_groups(min_papers=2)
        assert len(result) == 2
        assert result[0]["surname"] == "Beta"
        assert result[0]["paper_count"] == 3
