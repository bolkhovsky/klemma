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

    def test_schema_version_after_init(self, state):
        """New databases migrate to latest version."""
        with state._conn() as conn:
            version = conn.execute("PRAGMA user_version").fetchone()[0]
        assert version >= 1

    def test_migration_is_idempotent(self, state):
        """Running _migrate_schema() multiple times is safe."""
        with state._conn() as conn:
            state._migrate_schema(conn)
            state._migrate_schema(conn)
            version = conn.execute("PRAGMA user_version").fetchone()[0]
        assert version >= 1

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


class TestIntentWeightedScoring:
    """Tests for intent-weighted gap scoring formula."""

    def _setup_gaps(self, state):
        """Create test gaps with different intents and equal other factors."""
        state.register_sources(["src1", "src2"])
        # Set same quality for both sources
        with state._conn() as conn:
            conn.execute(
                "UPDATE sources SET quality_score=4 WHERE id IN ('src1', 'src2')"
            )

        # Method gap — should rank highest (weight 3.0)
        state.save_reference_gaps("src1", [
            {
                "authors": "Alpha et al.",
                "year": 2020,
                "title": "Method Paper Alpha",
                "why_relevant": "Core method",
                "citation_intent": "method",
            }
        ])
        # Result comparison gap — mid-rank (weight 2.0)
        state.save_reference_gaps("src2", [
            {
                "authors": "Beta et al.",
                "year": 2021,
                "title": "Results Paper Beta",
                "why_relevant": "Compare results",
                "citation_intent": "result_comparison",
            },
            # Background gap — lowest rank (weight 1.0)
            {
                "authors": "Gamma et al.",
                "year": 2019,
                "title": "Background Paper Gamma",
                "why_relevant": "General context",
                "citation_intent": "background",
            },
        ])

    def test_method_gap_ranks_above_background(self, state):
        """Method-intent gaps should score higher than background."""
        self._setup_gaps(state)
        gaps = state.get_reference_gaps()
        scores = {g["ref_authors"]: g["score"] for g in gaps}
        assert scores["Alpha et al."] > scores["Gamma et al."]

    def test_result_gap_ranks_above_background(self, state):
        """Result-comparison gaps should score higher than background."""
        self._setup_gaps(state)
        gaps = state.get_reference_gaps()
        scores = {g["ref_authors"]: g["score"] for g in gaps}
        assert scores["Beta et al."] > scores["Gamma et al."]

    def test_intent_weight_in_results(self, state):
        """intent_weight field is present in results."""
        self._setup_gaps(state)
        gaps = state.get_reference_gaps()
        method_gap = [g for g in gaps if "Alpha" in g["ref_authors"]][0]
        assert method_gap["intent_weight"] == 3.0

    def test_null_intent_weight_is_one(self, state):
        """NULL intents get weight 1.0 — backward compatible."""
        state.register_sources(["src1"])
        with state._conn() as conn:
            conn.execute("UPDATE sources SET quality_score=3 WHERE id='src1'")
        state.save_reference_gaps("src1", [
            {
                "authors": "Old et al.",
                "year": 2018,
                "title": "Legacy Paper",
                "why_relevant": "Old data, no intent",
            }
        ])
        gaps = state.get_reference_gaps()
        old_gap = [g for g in gaps if "Old" in g["ref_authors"]][0]
        assert old_gap["intent_weight"] == 1.0

    def test_scoring_order_method_result_background(self, state):
        """Full ordering: method > result_comparison > background."""
        self._setup_gaps(state)
        gaps = state.get_reference_gaps()
        # Extract ordered names
        ordered = [g["ref_authors"] for g in gaps]
        alpha_idx = ordered.index("Alpha et al.")
        beta_idx = ordered.index("Beta et al.")
        gamma_idx = ordered.index("Gamma et al.")
        assert alpha_idx < beta_idx < gamma_idx


class TestIntentCoverage:
    """Tests for get_intent_coverage() method."""

    def test_empty_db_returns_empty(self, state):
        """No fragments → empty dict."""
        assert state.get_intent_coverage() == {}

    def test_fragments_without_intent_excluded(self, state):
        """Fragments with NULL intent are not counted."""
        state.register_sources(["src1"])
        state.save_fragments("src1", [
            {"text": "No intent", "section": "2.1"},
        ])
        assert state.get_intent_coverage() == {}

    def test_single_section_single_intent(self, state):
        state.register_sources(["src1"])
        state.save_fragments("src1", [
            {"text": "BG frag", "section": "2.1", "citation_intent": "background"},
        ])
        cov = state.get_intent_coverage()
        assert "2.1" in cov
        assert cov["2.1"]["background"] == 1
        assert cov["2.1"]["method"] == 0
        assert cov["2.1"]["result_comparison"] == 0
        assert cov["2.1"]["total"] == 1

    def test_multiple_intents_per_section(self, state):
        state.register_sources(["src1"])
        state.save_fragments("src1", [
            {"text": "BG1", "section": "3.1", "citation_intent": "background"},
            {"text": "BG2", "section": "3.1", "citation_intent": "background"},
            {"text": "M1", "section": "3.1", "citation_intent": "method"},
            {"text": "R1", "section": "3.1", "citation_intent": "result_comparison"},
        ])
        cov = state.get_intent_coverage()
        assert cov["3.1"]["background"] == 2
        assert cov["3.1"]["method"] == 1
        assert cov["3.1"]["result_comparison"] == 1
        assert cov["3.1"]["total"] == 4

    def test_multiple_sections(self, state):
        state.register_sources(["src1"])
        state.save_fragments("src1", [
            {"text": "M1", "section": "2.1", "citation_intent": "method"},
            {"text": "BG1", "section": "2.3", "citation_intent": "background"},
            {"text": "R1", "section": "2.3", "citation_intent": "result_comparison"},
        ])
        cov = state.get_intent_coverage()
        assert len(cov) == 2
        assert cov["2.1"]["method"] == 1
        assert cov["2.1"]["total"] == 1
        assert cov["2.3"]["background"] == 1
        assert cov["2.3"]["result_comparison"] == 1
        assert cov["2.3"]["total"] == 2
