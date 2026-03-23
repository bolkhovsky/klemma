"""Tests for Guided Serendipity insights — blind spots and hidden clusters."""

import pytest

from klemma.skills.insights import (
    BlindSpot,
    HiddenCluster,
    InsightsResult,
    RawCandidate,
    _candidates_from_insights,
    _format_candidates_for_prompt,
    _format_feedback_for_prompt,
    _parse_curated_insights,
    check_insights_blocked,
    detect_blind_spots,
    detect_hidden_clusters,
    generate_curated_insights,
    generate_insights,
    save_insights_as_decisions,
    suppress_candidates,
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


# ── Suppression tests ────────────────────────────────────────────────────


class TestSuppression:
    def test_removes_trivial_clusters(self, state):
        """Clusters with similarity < 0.80 are suppressed."""
        candidates = [
            RawCandidate(
                candidate_type="hidden_cluster",
                section="1.1", sections=["1.1", "2.1"],
                similarity=0.75, section_a="1.1", section_b="2.1",
                citekey_a="a", citekey_b="b",
            ),
            RawCandidate(
                candidate_type="hidden_cluster",
                section="1.1", sections=["1.1", "3.1"],
                similarity=0.90, section_a="1.1", section_b="3.1",
                citekey_a="c", citekey_b="d",
            ),
        ]
        survivors = suppress_candidates(candidates, state)
        assert len(survivors) == 1
        assert survivors[0].similarity == 0.90

    def test_removes_duplicate_section_pairs(self, state):
        """Only the first cluster per section pair survives."""
        candidates = [
            RawCandidate(
                candidate_type="hidden_cluster",
                section="1.1", sections=["1.1", "2.1"],
                similarity=0.85, section_a="1.1", section_b="2.1",
                citekey_a="a", citekey_b="b",
            ),
            RawCandidate(
                candidate_type="hidden_cluster",
                section="2.1", sections=["1.1", "2.1"],
                similarity=0.82, section_a="2.1", section_b="1.1",
                citekey_a="c", citekey_b="d",
            ),
        ]
        survivors = suppress_candidates(candidates, state)
        assert len(survivors) == 1

    def test_removes_same_chapter_redundant_blind_spots(self, state):
        """Keep only worst blind spot per chapter."""
        candidates = [
            RawCandidate(
                candidate_type="blind_spot",
                section="1.1", sections=["1.1"],
                source_count=3, average_count=10.0, severity="medium",
            ),
            RawCandidate(
                candidate_type="blind_spot",
                section="1.2", sections=["1.2"],
                source_count=1, average_count=10.0, severity="high",
            ),
        ]
        survivors = suppress_candidates(candidates, state)
        assert len(survivors) == 1
        assert survivors[0].section == "1.2"  # worse (fewer sources)

    def test_removes_already_decided_sections(self, state):
        """Sections with existing decided insights are suppressed."""
        # Create and decide an insight for section 3.1
        did = state.decisions.save_decision(
            trigger_type="insight",
            context={"type": "blind_spot"},
            options=[{"key": "A", "title": "Act"}],
            sections=["3.1"],
        )
        state.decisions.decide(did, "A")

        candidates = [
            RawCandidate(
                candidate_type="blind_spot",
                section="3.1", sections=["3.1"],
                source_count=1, average_count=10.0, severity="high",
            ),
            RawCandidate(
                candidate_type="blind_spot",
                section="4.1", sections=["4.1"],
                source_count=2, average_count=10.0, severity="medium",
            ),
        ]
        survivors = suppress_candidates(candidates, state)
        assert len(survivors) == 1
        assert survivors[0].section == "4.1"

    def test_preserves_valid_candidates(self, state):
        """Valid candidates pass through suppression."""
        candidates = [
            RawCandidate(
                candidate_type="blind_spot",
                section="3.1", sections=["3.1"],
                source_count=1, average_count=10.0, severity="high",
            ),
            RawCandidate(
                candidate_type="hidden_cluster",
                section="1.1", sections=["1.1", "2.1"],
                similarity=0.92, section_a="1.1", section_b="2.1",
                citekey_a="a", citekey_b="b",
            ),
        ]
        survivors = suppress_candidates(candidates, state)
        assert len(survivors) == 2

    def test_empty_candidates(self, state):
        """Empty input returns empty output."""
        survivors = suppress_candidates([], state)
        assert survivors == []


# ── Blocking tests ───────────────────────────────────────────────────────


class TestBlocking:
    def test_not_blocked_when_no_pending(self, state):
        blocked, count, pending = check_insights_blocked(state)
        assert blocked is False
        assert count == 0

    def test_blocked_when_pending_exist(self, state):
        state.decisions.save_decision(
            trigger_type="insight",
            context={"type": "blind_spot"},
            options=[],
        )
        blocked, count, pending = check_insights_blocked(state)
        assert blocked is True
        assert count == 1

    def test_not_blocked_after_deciding(self, state):
        did = state.decisions.save_decision(
            trigger_type="insight",
            context={"type": "blind_spot"},
            options=[{"key": "A", "title": "Act"}],
        )
        state.decisions.decide(did, "A")

        blocked, count, pending = check_insights_blocked(state)
        assert blocked is False


# ── Curation tests ───────────────────────────────────────────────────────


class TestCuration:
    def test_parse_curated_insights_basic(self):
        """Parse a valid LLM response."""
        candidates = [
            RawCandidate(
                candidate_type="blind_spot",
                section="3.1", sections=["3.1"],
                source_count=1, average_count=10.0, severity="high",
            ),
        ]
        response = {
            "insights": [
                {
                    "candidate_index": 1,
                    "title": "Section 3.1 needs attention",
                    "explanation": "Very few sources for methodology",
                    "trajectory": "Could reveal methodology gap",
                    "diversity_tag": "gap",
                    "novelty_score": 0.8,
                    "actionability_score": 0.9,
                    "sections": ["3.1"],
                    "action_title": "Search for papers",
                    "action_description": "Run klemma suggest for section 3.1",
                },
            ],
        }
        result = _parse_curated_insights(response, candidates)
        assert len(result) == 1
        assert result[0].title == "Section 3.1 needs attention"
        assert result[0].diversity_tag == "gap"
        assert result[0].context["type"] == "blind_spot"
        assert len(result[0].options) == 3  # Act/Bookmark/Dismiss

    def test_diversity_cap_enforced(self):
        """Max 2 insights per diversity_tag."""
        candidates = [
            RawCandidate(candidate_type="blind_spot", section=f"{i}.1",
                         sections=[f"{i}.1"], source_count=1, average_count=10.0)
            for i in range(5)
        ]
        response = {
            "insights": [
                {"candidate_index": i + 1, "title": f"Insight {i}",
                 "explanation": "x", "trajectory": "y",
                 "diversity_tag": "gap", "novelty_score": 0.5,
                 "actionability_score": 0.5, "sections": [f"{i}.1"],
                 "action_title": "Act", "action_description": "Do"}
                for i in range(5)
            ],
        }
        result = _parse_curated_insights(response, candidates)
        # Only 2 should survive the diversity cap
        assert len(result) == 2

    def test_max_five_insights(self):
        """Never more than 5 insights."""
        candidates = [
            RawCandidate(candidate_type="blind_spot", section=f"{i}.1",
                         sections=[f"{i}.1"], source_count=1, average_count=10.0)
            for i in range(8)
        ]
        tags = ["gap", "bridge", "methodology", "anomaly", "gap", "bridge", "methodology", "anomaly"]
        response = {
            "insights": [
                {"candidate_index": i + 1, "title": f"Insight {i}",
                 "explanation": "x", "trajectory": "y",
                 "diversity_tag": tags[i], "novelty_score": 0.5,
                 "actionability_score": 0.5, "sections": [f"{i}.1"],
                 "action_title": "Act", "action_description": "Do"}
                for i in range(8)
            ],
        }
        result = _parse_curated_insights(response, candidates)
        assert len(result) <= 5

    def test_empty_response(self):
        """Empty LLM response returns empty list."""
        result = _parse_curated_insights({}, [])
        assert result == []

    def test_invalid_response_format(self):
        """Non-list 'insights' field returns empty list."""
        result = _parse_curated_insights({"insights": "not a list"}, [])
        assert result == []


class TestCandidateConversion:
    def test_candidates_from_insights(self):
        result = InsightsResult(
            blind_spots=[
                BlindSpot(section="3.1", source_count=1, average_count=10.0, severity="high"),
                BlindSpot(section="5.1", source_count=8, average_count=10.0, severity="low"),
            ],
            hidden_clusters=[
                HiddenCluster(
                    citekey_a="a", citekey_b="b",
                    similarity=0.9, section_a="1.1", section_b="2.1",
                ),
            ],
        )
        candidates = _candidates_from_insights(result)
        # Only medium/high blind spots + all clusters
        assert len(candidates) == 2
        assert candidates[0].candidate_type == "blind_spot"
        assert candidates[1].candidate_type == "hidden_cluster"


class TestFormatting:
    def test_format_candidates_for_prompt(self):
        candidates = [
            RawCandidate(
                candidate_type="blind_spot",
                section="3.1", sections=["3.1"],
                source_count=1, average_count=10.0, gap_count=5, severity="high",
            ),
        ]
        text = _format_candidates_for_prompt(candidates)
        assert "BLIND SPOT" in text
        assert "3.1" in text

    def test_format_feedback_for_prompt_empty(self):
        text = _format_feedback_for_prompt({"total_liked": 0, "total_disliked": 0, "recent_notes": []})
        assert text == "No feedback yet."

    def test_format_feedback_for_prompt_with_data(self):
        text = _format_feedback_for_prompt({
            "total_liked": 2, "total_disliked": 1,
            "liked_types": {"gap": 2},
            "disliked_types": {"bridge": 1},
            "recent_notes": ["test note"],
        })
        assert "Liked" in text
        assert "Disliked" in text
        assert "test note" in text


# ── Integration tests ────────────────────────────────────────────────────


class TestGenerateCuratedInsights:
    def test_blocked_returns_early(self, state):
        """If pending insights exist, returns blocked result."""
        state.decisions.save_decision(
            trigger_type="insight",
            context={"type": "blind_spot"},
            options=[],
        )
        from klemma.config import KlemmaConfig
        cfg = KlemmaConfig()

        result = generate_curated_insights(state, config=cfg)
        assert result.blocked is True
        assert result.pending_count == 1

    def test_raw_mode_skips_curation(self, state):
        """Raw mode saves all candidates without LLM."""
        from klemma.config import KlemmaConfig
        cfg = KlemmaConfig()

        result = generate_curated_insights(state, config=cfg, raw_mode=True)
        # With the fixture data, there should be blind spots
        assert result.raw_count > 0
        assert result.suppressed_count == 0

    def test_empty_library_returns_zero(self, tmp_path):
        """Empty library returns no insights."""
        sm = StateManager(tmp_path / "empty.db")
        from klemma.config import KlemmaConfig
        cfg = KlemmaConfig()

        result = generate_curated_insights(sm, config=cfg)
        assert result.raw_count == 0
        assert result.blocked is False
