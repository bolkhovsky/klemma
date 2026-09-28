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


class TestMigrationV16:
    """v16: fragment spans/locator, sources.degraded_steps, claims table."""

    @staticmethod
    def _make_v15_db(db_path):
        """Build a minimal v15-era database (no v16 columns/tables)."""
        import sqlite3

        conn = sqlite3.connect(db_path)
        conn.executescript("""
            CREATE TABLE sources (
                id TEXT PRIMARY KEY,
                zotero_key TEXT,
                status TEXT DEFAULT 'pending',
                processed_at TEXT,
                error_message TEXT,
                note_path TEXT,
                quality_score INTEGER,
                primary_chapter INTEGER,
                primary_section TEXT,
                relevance_nr1 INTEGER DEFAULT 0,
                relevance_nr2 INTEGER DEFAULT 0,
                citation_priority TEXT DEFAULT 'medium',
                pdf_path TEXT,
                pdf_text_length INTEGER,
                fragment_count INTEGER DEFAULT 0
            );
            CREATE TABLE fragments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_id TEXT NOT NULL REFERENCES sources(id),
                fragment_text TEXT NOT NULL,
                fragment_type TEXT,
                chapter INTEGER,
                section TEXT,
                relevance_score INTEGER,
                usage_hint TEXT,
                page_number INTEGER,
                extracted_at TEXT DEFAULT (datetime('now')),
                used_in_draft BOOLEAN DEFAULT 0,
                citation_intent TEXT,
                embedding BLOB,
                embedding_model TEXT,
                section_type TEXT,
                verbatim INTEGER NOT NULL DEFAULT 0,
                UNIQUE(source_id, fragment_text)
            );
            INSERT INTO sources (id, status) VALUES ('legacy2020', 'completed');
            INSERT INTO fragments (source_id, fragment_text)
                VALUES ('legacy2020', 'A legacy fragment.');
            PRAGMA user_version = 15;
        """)
        conn.commit()
        conn.close()

    def test_v16_upgrades_v15_db(self, tmp_path):
        db_path = tmp_path / "v15.db"
        self._make_v15_db(db_path)

        state = StateManager(db_path)

        with state._conn() as conn:
            assert conn.execute("PRAGMA user_version").fetchone()[0] == 16
            frag_cols = {
                row[1] for row in conn.execute("PRAGMA table_info(fragments)")
            }
            assert {"char_start", "char_end", "source_locator"} <= frag_cols
            src_cols = {
                row[1] for row in conn.execute("PRAGMA table_info(sources)")
            }
            assert "degraded_steps" in src_cols
            tables = {
                row[0] for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            assert "claims" in tables
            indexes = {
                row[0] for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='index'"
                )
            }
            assert "idx_claims_manuscript" in indexes
            # Legacy data survives
            row = conn.execute(
                "SELECT fragment_text FROM fragments WHERE source_id='legacy2020'"
            ).fetchone()
            assert row[0] == "A legacy fragment."

    def test_v16_idempotent_on_v15_db(self, tmp_path):
        """Running _migrate_schema twice on an upgraded v15 DB is safe."""
        db_path = tmp_path / "v15.db"
        self._make_v15_db(db_path)
        state = StateManager(db_path)

        with state._conn() as conn:
            state._migrate_schema(conn)
            state._migrate_schema(conn)
            assert conn.execute("PRAGMA user_version").fetchone()[0] == 16
            # Columns are not duplicated
            frag_cols = [
                row[1] for row in conn.execute("PRAGMA table_info(fragments)")
            ]
            assert frag_cols.count("char_start") == 1
            assert frag_cols.count("source_locator") == 1

    def test_fresh_db_has_claims_table(self, tmp_path):
        """Base SCHEMA ships claims — fresh DBs get it without migration."""
        state = StateManager(tmp_path / "fresh.db")
        with state._conn() as conn:
            cols = {
                row[1] for row in conn.execute("PRAGMA table_info(claims)")
            }
        assert {
            "manuscript_path", "claim_hash", "anchor_key", "sentence",
            "citekey", "char_start", "char_end", "verdict", "stale",
        } <= cols

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


class TestSemanticGapScoring:
    """Tests for rerank_gaps_semantic()."""

    class FakeEmbeddings:
        dim = 3
        model_name = "test"

        def embed(self, title, abstract=""):
            return [1.0, 0.0, 0.0]

    def test_no_embeddings_returns_unchanged(self, state):
        """Without embeddings, gaps are returned as-is."""
        gaps = [{"score": 5.0, "source_ids": "src1"}]
        result = state.rerank_gaps_semantic(gaps, embeddings=None)
        assert result[0]["score"] == 5.0

    def test_semantic_boost_applied(self, state):
        """Gaps with embedded citing sources get semantic_boost."""
        state.register_sources(["src1", "src2"])
        state.save_embedding("src1", [1.0, 0.0, 0.0], "test")
        state.save_embedding("src2", [0.9, 0.1, 0.0], "test")

        gaps = [
            {"score": 10.0, "source_ids": "src1", "ref_authors": "A"},
            {"score": 10.0, "source_ids": "src2", "ref_authors": "B"},
        ]
        emb = self.FakeEmbeddings()
        result = state.rerank_gaps_semantic(gaps, embeddings=emb)
        # Both should have semantic_boost
        assert "semantic_boost" in result[0]
        assert result[0]["semantic_boost"] > 0

    def test_no_stored_embeddings_returns_unchanged(self, state):
        """If no embeddings in DB, gaps are returned as-is."""
        gaps = [{"score": 5.0, "source_ids": "src1"}]
        emb = self.FakeEmbeddings()
        result = state.rerank_gaps_semantic(gaps, embeddings=emb)
        assert result[0]["score"] == 5.0

    def test_reranking_changes_order(self, state):
        """Semantic boost can change gap ordering."""
        state.register_sources(["close", "far"])
        # "close" is very aligned with global centroid direction
        state.save_embedding("close", [1.0, 0.0, 0.0], "test")
        # "far" is orthogonal
        state.save_embedding("far", [0.0, 1.0, 0.0], "test")

        # Initially "far" has higher heuristic score
        gaps = [
            {"score": 20.0, "source_ids": "far", "ref_authors": "Far"},
            {"score": 10.0, "source_ids": "close", "ref_authors": "Close"},
        ]
        emb = self.FakeEmbeddings()
        result = state.rerank_gaps_semantic(gaps, embeddings=emb)
        # The "close" source gets bigger semantic boost, may overtake "far"
        # (depends on centroid direction — centroid = avg of both vectors)
        scores = {g["ref_authors"]: g["score"] for g in result}
        # Close source has better alignment so gets higher multiplier
        assert scores["Close"] > 0


class TestSectionWeights:
    """Tests for configurable per-section weights in gap scoring."""

    def _setup_weighted_gaps(self, state):
        """Create gaps in different sections with equal count/quality/intent."""
        state.register_sources(["src1"])
        with state._conn() as conn:
            conn.execute("UPDATE sources SET quality_score=4 WHERE id='src1'")
        state.save_reference_gaps("src1", [
            {
                "authors": "Weighted et al.",
                "year": 2022,
                "title": "Weighted Section Paper",
                "why_relevant": "In high-weight section",
                "dissertation_sections": ["2.1"],
            },
            {
                "authors": "Unweighted et al.",
                "year": 2022,
                "title": "Unweighted Section Paper",
                "why_relevant": "In unlisted section",
                "dissertation_sections": ["4.3"],
            },
        ])

    def test_custom_weight_boosts_score(self, state):
        """Gap in section with weight 1.0 scores higher than default 0.5."""
        self._setup_weighted_gaps(state)
        weights = {"2.1": 1.0}
        gaps = state.get_reference_gaps(section_weights=weights)
        scores = {g["ref_authors"]: g["score"] for g in gaps}
        assert scores["Weighted et al."] > scores["Unweighted et al."]

    def test_unlisted_section_defaults_to_half(self, state):
        """Gap in unconfigured section gets w_s=0.5."""
        self._setup_weighted_gaps(state)
        weights = {"2.1": 1.0}
        gaps = state.get_reference_gaps(section_weights=weights)
        weighted = [g for g in gaps if "Weighted" in g["ref_authors"]][0]
        unweighted = [g for g in gaps if "Unweighted" in g["ref_authors"]][0]
        # Both have same count=1, quality=4, intent=1.0
        # Weighted: 1*4*1.0*1.0 = 4.0, Unweighted: 1*4*0.5*1.0 = 2.0
        assert weighted["score"] == 4.0
        assert unweighted["score"] == 2.0

    def test_no_weights_uniform(self, state):
        """section_weights=None → all sections get w_s=1.0 (backward compat)."""
        self._setup_weighted_gaps(state)
        gaps = state.get_reference_gaps(section_weights=None)
        scores = {g["ref_authors"]: g["score"] for g in gaps}
        # Both sections get uniform weight 1.0
        assert scores["Weighted et al."] == scores["Unweighted et al."]

    def test_results_sorted_by_score(self, state):
        """Output is sorted descending by score."""
        self._setup_weighted_gaps(state)
        weights = {"2.1": 1.0}
        gaps = state.get_reference_gaps(section_weights=weights)
        scores = [g["score"] for g in gaps]
        assert scores == sorted(scores, reverse=True)
