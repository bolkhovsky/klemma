"""Tests for Guided Serendipity insights — blind spots and hidden clusters."""

import pytest

from klemma.skills.insights import (
    BlindSpot,
    HiddenCluster,
    InsightsResult,
    detect_blind_spots,
    detect_hidden_clusters,
    generate_insights,
    save_insights_as_decisions,
)
from klemma.state import StateManager


@pytest.fixture
def state(tmp_path):
    db_path = tmp_path / "test.db"
    sm = StateManager(db_path)
    for i, (citekey, section, chapter) in enumerate([
        ("paper_a1", "1.1", 1), ("paper_a2", "1.1", 1), ("paper_a3", "1.1", 1),
        ("paper_a4", "1.1", 1), ("paper_a5", "1.1", 1), ("paper_a6", "1.1", 1),
        ("paper_a7", "1.1", 1), ("paper_a8", "1.1", 1), ("paper_a9", "1.1", 1),
        ("paper_a10", "1.1", 1),
        ("paper_b1", "2.1", 2), ("paper_b2", "2.1", 2), ("paper_b3", "2.1", 2),
        ("paper_b4", "2.1", 2), ("paper_b5", "2.1", 2), ("paper_b6", "2.1", 2),
        ("paper_b7", "2.1", 2), ("paper_b8", "2.1", 2),
        ("paper_c1", "3.1", 3),
        ("paper_d1", "4.1", 4), ("paper_d2", "4.1", 4),
    ]):
        sm.register_sources([citekey])
        sm.mark_completed(
            citekey, note_path=f"@{citekey}.md",
            primary_chapter=chapter, primary_section=section,
        )
        sm.update_source_info(citekey, title=f"Paper {citekey}", authors=f"Author {i}")
        sm.sources.set_source_sections(citekey, [section], [chapter])
    return sm


class TestBlindSpots:
    def test_detects_weak_sections(self, state):
        spots = detect_blind_spots(state)
        sections = {s.section for s in spots}
        assert "3.1" in sections

    def test_severity_assignment(self, state):
        spots = detect_blind_spots(state)
        spot_31 = next((s for s in spots if s.section == "3.1"), None)
        assert spot_31 is not None
        assert spot_31.severity == "high"

    def test_empty_library(self, tmp_path):
        sm = StateManager(tmp_path / "empty.db")
        spots = detect_blind_spots(sm)
        assert spots == []

    def test_balanced_library(self, tmp_path):
        sm = StateManager(tmp_path / "balanced.db")
        for i in range(5):
            ck = f"s{i}"
            sm.register_sources([ck])
            sm.mark_completed(ck, note_path=f"@{ck}.md")
            sm.sources.set_source_sections(ck, ["1.1"], [1])
        spots = detect_blind_spots(sm)
        assert spots == []


class TestHiddenClusters:
    def test_finds_cross_section_similar_pairs(self, state):
        vec_a = [1.0, 0.0, 0.0] * 10
        vec_b = [0.99, 0.1, 0.0] * 10

        state.save_embedding("paper_a1", vec_a, "test")
        state.save_embedding("paper_b1", vec_b, "test")

        clusters = detect_hidden_clusters(state, similarity_threshold=0.9)
        assert len(clusters) >= 1
        pair = clusters[0]
        assert {pair.citekey_a, pair.citekey_b} == {"paper_a1", "paper_b1"}
        assert pair.section_a != pair.section_b

    def test_ignores_same_section_pairs(self, state):
        vec = [1.0, 0.0, 0.0] * 10
        state.save_embedding("paper_a1", vec, "test")
        state.save_embedding("paper_a2", vec, "test")

        clusters = detect_hidden_clusters(state)
        same_section = [c for c in clusters if c.section_a == c.section_b]
        assert same_section == []

    def test_empty_embeddings(self, state):
        clusters = detect_hidden_clusters(state)
        assert clusters == []

    def test_respects_max_pairs(self, state):
        vec = [1.0, 0.0, 0.0] * 10
        for ck in ["paper_a1", "paper_b1", "paper_c1", "paper_d1"]:
            state.save_embedding(ck, vec, "test")

        clusters = detect_hidden_clusters(state, similarity_threshold=0.5, max_pairs=2)
        assert len(clusters) <= 2


class TestGenerateInsights:
    def test_returns_combined_result(self, state):
        result = generate_insights(state)
        assert isinstance(result, InsightsResult)
        assert len(result.blind_spots) > 0


class TestSaveInsightsAsDecisions:
    def test_saves_blind_spot_decisions(self, state):
        result = InsightsResult(
            blind_spots=[
                BlindSpot(section="3.1", source_count=1, average_count=10.0, severity="high"),
                BlindSpot(section="4.1", source_count=3, average_count=10.0, severity="medium"),
                BlindSpot(section="5.1", source_count=8, average_count=10.0, severity="low"),
            ],
        )
        ids = save_insights_as_decisions(result, state)
        assert len(ids) == 2

        d = state.decisions.get_decision(ids[0])
        assert d["trigger_type"] == "insight"
        assert d["context_json"]["type"] == "blind_spot"
        assert d["sections"] == ["3.1"]

    def test_saves_cluster_decisions(self, state):
        result = InsightsResult(
            hidden_clusters=[
                HiddenCluster(
                    citekey_a="a2020", citekey_b="b2021",
                    similarity=0.92, section_a="1.1", section_b="3.2",
                    title_a="Paper A", title_b="Paper B",
                ),
            ],
        )
        ids = save_insights_as_decisions(result, state)
        assert len(ids) == 1

        d = state.decisions.get_decision(ids[0])
        assert d["trigger_type"] == "insight"
        assert d["context_json"]["type"] == "hidden_cluster"
        assert "1.1" in d["sections"]
        assert "3.2" in d["sections"]

    def test_empty_insights_no_decisions(self, state):
        result = InsightsResult()
        ids = save_insights_as_decisions(result, state)
        assert ids == []
