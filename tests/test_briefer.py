"""Tests for Guided Serendipity briefer skill."""

from unittest.mock import MagicMock

import pytest

from klemma.skills.briefer import (
    BriefingResult,
    SimilarSource,
    find_similar_sources,
    generate_briefing,
    save_briefing_as_decision,
)
from klemma.state import StateManager


@pytest.fixture
def state(tmp_path):
    db_path = tmp_path / "test.db"
    sm = StateManager(db_path)
    # Register a source with fragments
    sm.register_sources(["goessling2016"])
    sm.update_source_info(
        "goessling2016",
        title="Evaluating sea ice forecasts with IIEE",
        authors="Goessling H.F.",
        year=2016,
        abstract="IIEE separates forecast error into miss, false alarm, and displacement components.",
    )
    sm.mark_completed("goessling2016", note_path="@goessling2016.md")
    sm.fragments.save_fragments("goessling2016", [
        {
            "fragment_text": "IIEE decomposes errors into three components",
            "fragment_type": "quote",
            "chapter": 3,
            "section": "3.2",
            "relevance_score": 0.9,
            "citation_intent": "method",
        },
        {
            "fragment_text": "40% of forecast error is displacement",
            "fragment_type": "paraphrase",
            "chapter": 3,
            "section": "3.2",
            "relevance_score": 0.85,
            "citation_intent": "result_comparison",
        },
    ])
    return sm


class TestFindSimilarSources:
    def test_no_embeddings_returns_empty(self, state):
        result = find_similar_sources("goessling2016", state, embeddings=None)
        assert result == []

    def test_no_target_embedding_returns_empty(self, state):
        embeddings = MagicMock()
        result = find_similar_sources("goessling2016", state, embeddings=embeddings)
        assert result == []


class TestGenerateBriefing:
    def test_source_not_found(self, state):
        ai = MagicMock()
        cfg = MagicMock()
        result = generate_briefing("nonexistent", cfg, state, ai)
        assert result.error is not None
        assert "not found" in result.error

    def test_no_fragments_no_abstract(self, state):
        state.register_sources(["empty2024"])
        state.mark_completed("empty2024", note_path="@empty2024.md")
        ai = MagicMock()
        cfg = MagicMock()
        result = generate_briefing("empty2024", cfg, state, ai)
        assert result.error is not None
        assert "No fragments" in result.error

    def test_successful_briefing(self, state):
        ai = MagicMock()
        ai.render_prompt.return_value = "rendered prompt"
        ai.call_json.return_value = {
            "key_claims": ["IIEE splits error into 3 components"],
            "connections": [
                {"related_citekey": "@author2020", "relationship": "extends", "description": "Extends binary metrics"}
            ],
            "niches": ["No displacement error analysis for Arctic"],
            "forks": [
                {"key": "A", "title": "Use IIEE as one metric", "description": "Include in comparison table", "sections": ["3.2"]},
                {"key": "B", "title": "Focus on displacement", "description": "Make displacement central", "sections": ["3.2", "4.1"]},
            ],
            "recommended_sections": ["3.2"],
        }
        cfg = MagicMock()
        cfg.dissertation = MagicMock()
        cfg.dissertation.language = "Russian"

        result = generate_briefing(
            "goessling2016", cfg, state, ai,
            dissertation_context="Sea ice forecast verification",
        )

        assert result.error is None
        assert len(result.key_claims) == 1
        assert len(result.forks) == 2
        assert result.forks[0]["key"] == "A"
        assert result.recommended_sections == ["3.2"]
        ai.call_json.assert_called_once()

    def test_llm_returns_none(self, state):
        ai = MagicMock()
        ai.render_prompt.return_value = "prompt"
        ai.call_json.return_value = None
        cfg = MagicMock()
        cfg.dissertation = MagicMock()
        cfg.dissertation.language = "Russian"

        result = generate_briefing("goessling2016", cfg, state, ai)
        assert result.error is not None
        assert "no result" in result.error


class TestSaveBriefingAsDecision:
    def test_save_successful(self, state):
        briefing = BriefingResult(
            source_citekey="goessling2016",
            key_claims=["Claim 1"],
            connections=[{"related_citekey": "@x", "relationship": "extends", "description": "test"}],
            niches=["Niche 1"],
            forks=[
                {"key": "A", "title": "Direction A", "description": "Desc A", "sections": ["3.2"]},
                {"key": "B", "title": "Direction B", "description": "Desc B", "sections": ["4.1"]},
            ],
            recommended_sections=["3.2"],
            similar_sources=[SimilarSource(citekey="other2020", title="Other", similarity=0.85)],
        )

        decision_id = save_briefing_as_decision(briefing, state)
        assert decision_id is not None
        assert decision_id > 0

        # Verify stored decision
        d = state.decisions.get_decision(decision_id)
        assert d["trigger_type"] == "briefing"
        assert d["trigger_source"] == "goessling2016"
        assert d["chosen_option"] is None  # pending
        assert len(d["options_json"]) == 2
        assert d["options_json"][0]["key"] == "A"
        assert "3.2" in d["sections"]
        assert "4.1" in d["sections"]

    def test_no_forks_returns_none(self, state):
        briefing = BriefingResult(
            source_citekey="goessling2016",
            key_claims=["Claim"],
            forks=[],
        )
        assert save_briefing_as_decision(briefing, state) is None

    def test_error_returns_none(self, state):
        briefing = BriefingResult(
            source_citekey="goessling2016",
            error="Something failed",
            forks=[{"key": "A", "title": "X", "description": "Y"}],
        )
        assert save_briefing_as_decision(briefing, state) is None

    def test_influenced_by_links_to_previous(self, state):
        # Create a previous decision
        prev_id = state.decisions.save_decision(
            trigger_type="briefing",
            trigger_source="prev2020",
            context={},
            options=[{"key": "A", "title": "X"}],
        )
        state.decisions.decide(prev_id, "A")

        briefing = BriefingResult(
            source_citekey="goessling2016",
            forks=[
                {"key": "A", "title": "Dir A", "description": "Desc"},
                {"key": "B", "title": "Dir B", "description": "Desc"},
            ],
        )

        decision_id = save_briefing_as_decision(briefing, state)
        d = state.decisions.get_decision(decision_id)
        assert d["influenced_by"] == [prev_id]

    def test_context_includes_similar_sources(self, state):
        briefing = BriefingResult(
            source_citekey="goessling2016",
            key_claims=["C1"],
            similar_sources=[
                SimilarSource(citekey="a2020", title="A", similarity=0.9),
                SimilarSource(citekey="b2021", title="B", similarity=0.8),
            ],
            forks=[{"key": "A", "title": "X", "description": "Y"}],
        )

        decision_id = save_briefing_as_decision(briefing, state)
        d = state.decisions.get_decision(decision_id)
        ctx = d["context_json"]
        assert len(ctx["similar_sources"]) == 2
        assert ctx["similar_sources"][0]["citekey"] == "a2020"
