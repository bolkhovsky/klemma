"""Tests for semantic fragment-to-section reassignment."""

import struct

import pytest

from klemma.embeddings import cosine_similarity


def _make_vec(dim=10, seed=1.0):
    """Create a simple test vector."""
    return [seed * (i + 1) / dim for i in range(dim)]


def _make_blob(vec):
    return struct.pack(f"{len(vec)}f", *vec)


class TestReassignLogic:
    """Test the cosine-based reassignment logic without CLI."""

    def test_cosine_similarity_identical(self):
        v = _make_vec(10, 1.0)
        assert cosine_similarity(v, v) == pytest.approx(1.0, abs=1e-6)

    def test_cosine_similarity_different(self):
        v1 = [1.0, 0.0, 0.0]
        v2 = [0.0, 1.0, 0.0]
        assert cosine_similarity(v1, v2) == pytest.approx(0.0, abs=1e-6)

    def test_best_section_match(self):
        """Fragment should match section with highest cosine similarity."""
        frag_vec = [1.0, 0.5, 0.0]
        sections = {
            "1.1": [1.0, 0.4, 0.0],  # very similar
            "2.1": [0.0, 0.0, 1.0],  # orthogonal
            "3.1": [0.5, 0.5, 0.5],  # moderate
        }

        best_section = ""
        best_score = -1.0
        for sec_id, sec_vec in sections.items():
            score = cosine_similarity(frag_vec, sec_vec)
            if score > best_score:
                best_score = score
                best_section = sec_id

        assert best_section == "1.1"
        assert best_score > 0.9

    def test_suggestion_filtering(self):
        """Only suggest when best section differs from current and above threshold."""
        frag_vec = [1.0, 0.5, 0.0]
        current_section = "2.1"
        threshold = 0.5

        sections = {
            "1.1": [1.0, 0.4, 0.0],  # best match
            "2.1": [0.0, 0.0, 1.0],  # current (low similarity)
        }

        best_section = ""
        best_score = -1.0
        for sec_id, sec_vec in sections.items():
            score = cosine_similarity(frag_vec, sec_vec)
            if score > best_score:
                best_score = score
                best_section = sec_id

        should_suggest = (
            best_section != current_section and best_score >= threshold
        )
        assert should_suggest is True
        assert best_section == "1.1"

    def test_no_suggestion_when_same_section(self):
        """Don't suggest when best match is already the current section."""
        frag_vec = [1.0, 0.5, 0.0]
        current_section = "1.1"

        sections = {
            "1.1": [1.0, 0.4, 0.0],  # best match = current
            "2.1": [0.0, 0.0, 1.0],
        }

        best_section = ""
        best_score = -1.0
        for sec_id, sec_vec in sections.items():
            score = cosine_similarity(frag_vec, sec_vec)
            if score > best_score:
                best_score = score
                best_section = sec_id

        should_suggest = best_section != current_section
        assert should_suggest is False

    def test_no_suggestion_below_threshold(self):
        """Don't suggest when best score is below threshold."""
        frag_vec = [1.0, 0.0, 0.0]
        current_section = "1.1"
        threshold = 0.9

        sections = {
            "1.1": [0.0, 1.0, 0.0],  # orthogonal = current
            "2.1": [0.5, 0.5, 0.0],  # moderate match
        }

        best_section = ""
        best_score = -1.0
        for sec_id, sec_vec in sections.items():
            score = cosine_similarity(frag_vec, sec_vec)
            if score > best_score:
                best_score = score
                best_section = sec_id

        should_suggest = (
            best_section != current_section and best_score >= threshold
        )
        assert should_suggest is False


class TestReassignCLI:
    """Test reassign CLI command argument handling."""

    def test_reassign_help_shows_citekey_argument(self):
        from click.testing import CliRunner

        from klemma.cli import main as klemma_cli

        runner = CliRunner()
        result = runner.invoke(klemma_cli, ["reassign", "--help"])
        assert result.exit_code == 0
        assert "CITEKEY" in result.output
        assert "--section" in result.output

    def test_section_without_citekey_fails(self):
        from unittest.mock import MagicMock, patch

        from click.testing import CliRunner

        from klemma.cli import main as klemma_cli

        runner = CliRunner()
        mock_ctx = MagicMock()
        mock_ctx.state = MagicMock()
        with (
            patch("klemma.cli._get_context", return_value=mock_ctx),
            patch("klemma.cli._init_components", return_value=mock_ctx),
            patch("klemma.cli.discover_project_root", return_value="/tmp"),
            patch("klemma.cli._sync_sections"),
        ):
            result = runner.invoke(klemma_cli, ["reassign", "-s", "1.1"])
        assert result.exit_code != 0
        assert "--section/-s requires a CITEKEY" in result.output

    def test_citekey_not_found_fails(self):
        from unittest.mock import MagicMock, patch

        from click.testing import CliRunner

        from klemma.cli import main as klemma_cli

        runner = CliRunner()
        mock_ctx = MagicMock()
        mock_state = MagicMock()
        mock_state.get_all_section_embeddings.return_value = {"1.1": [0.1]}
        mock_state.get_fragment_embeddings.return_value = {1: [0.1]}
        mock_state.get_embedded_fragment_metadata.return_value = []
        mock_state.get_existing_source_ids.return_value = {"real_paper"}
        mock_ctx.state = mock_state
        with (
            patch("klemma.cli._get_context", return_value=mock_ctx),
            patch("klemma.cli._init_components", return_value=mock_ctx),
            patch("klemma.cli.discover_project_root", return_value="/tmp"),
            patch("klemma.cli._sync_sections"),
        ):
            result = runner.invoke(klemma_cli, ["reassign", "nonexistent"])
        assert result.exit_code != 0
        assert "not found" in result.output


class TestFragmentMetadataRepo:
    """Test get_embedded_fragment_metadata repository method."""

    @pytest.fixture
    def state(self, tmp_path):
        from klemma.state import StateManager
        db_path = tmp_path / "test.db"
        sm = StateManager(str(db_path))
        return sm

    def test_returns_embedded_fragments(self, state):
        # Register a source and save fragments
        state.register_sources(["src1"])
        state.fragments.save_fragments("src1", [
            {"text": "Fragment about ice prediction methods", "type": "key_idea",
             "chapter": 1, "section": "1.2", "relevance": 5},
            {"text": "Another fragment about data", "type": "methodology",
             "chapter": 2, "section": "2.1", "relevance": 3},
        ])

        # Embed first fragment only
        vec = _make_vec(10, 1.0)
        state.fragments.save_fragment_embedding(1, vec, "test-model")

        result = state.get_embedded_fragment_metadata()
        assert len(result) == 1
        assert result[0]["id"] == 1
        assert result[0]["source_id"] == "src1"
        assert result[0]["section"] == "1.2"
        assert "ice prediction" in result[0]["text_preview"]

    def test_empty_when_no_embeddings(self, state):
        state.register_sources(["src1"])
        state.fragments.save_fragments("src1", [
            {"text": "No embedding", "type": "key_idea"},
        ])
        result = state.get_embedded_fragment_metadata()
        assert result == []

    def test_model_filter(self, state):
        state.register_sources(["src1"])
        state.fragments.save_fragments("src1", [
            {"text": "Fragment one about research", "type": "key_idea"},
            {"text": "Fragment two about methods", "type": "key_idea"},
        ])
        state.fragments.save_fragment_embedding(1, _make_vec(10, 1.0), "model-a")
        state.fragments.save_fragment_embedding(2, _make_vec(10, 2.0), "model-b")

        result_a = state.get_embedded_fragment_metadata("model-a")
        assert len(result_a) == 1
        assert result_a[0]["id"] == 1

        result_b = state.get_embedded_fragment_metadata("model-b")
        assert len(result_b) == 1
        assert result_b[0]["id"] == 2

        result_all = state.get_embedded_fragment_metadata()
        assert len(result_all) == 2


class TestReassignScoringSignals:
    """Tests for cross-type penalty and citation-intent bonus logic (issue #107)."""

    def _adjusted_scores(
        self,
        frag_vec,
        sections: dict,
        current_section: str,
        section_type_map: dict,
        frag_intent: str = "",
        cross_type_penalty: float = 0.05,
        intent_bonus: float = 0.03,
    ) -> dict:
        """Replicate the adjust logic from manage.py for unit testing."""
        intent_type_affinity = {
            "background": {"introduction", "literature_review", "background", "theoretical_framework"},
            "method": {"methodology", "implementation", "experiments"},
            "result_comparison": {"results", "discussion", "experiments"},
            "extends": {"discussion", "conclusion", "theoretical_framework"},
            "contrasts": {"discussion", "literature_review", "results"},
            "uses_data": {"data_description", "methodology", "experiments"},
        }
        intent_affinity = intent_type_affinity.get(frag_intent, set())
        current_stype = section_type_map.get(current_section, "")

        scores = {sec_id: cosine_similarity(frag_vec, sec_vec) for sec_id, sec_vec in sections.items()}
        adjusted = {}
        for sec_id, raw_score in scores.items():
            adj = raw_score
            sec_stype = section_type_map.get(sec_id, "")
            if current_stype and sec_stype and sec_stype != current_stype:
                adj -= cross_type_penalty
            if sec_stype and sec_stype in intent_affinity:
                adj += intent_bonus
            adjusted[sec_id] = adj
        return adjusted

    def test_cross_type_penalty_applied(self):
        """Suggested section with different type gets penalty."""
        frag_vec = [1.0, 0.0, 0.0]
        sections = {
            "1.2": [0.9, 0.1, 0.0],  # same type: methodology → no penalty
            "2.1": [0.8, 0.1, 0.0],  # different type: results → penalty
        }
        stype_map = {"1.2": "methodology", "2.1": "results"}

        adjusted = self._adjusted_scores(
            frag_vec, sections,
            current_section="1.2",
            section_type_map=stype_map,
            cross_type_penalty=0.05,
        )

        raw_1_2 = cosine_similarity(frag_vec, sections["1.2"])
        raw_2_1 = cosine_similarity(frag_vec, sections["2.1"])

        # Same-type section: no penalty
        assert adjusted["1.2"] == pytest.approx(raw_1_2, abs=1e-6)
        # Cross-type section: penalized
        assert adjusted["2.1"] == pytest.approx(raw_2_1 - 0.05, abs=1e-6)

    def test_cross_type_penalty_suppresses_false_positive(self):
        """Cross-type penalty flips ranking when delta < penalty."""
        frag_vec = [1.0, 0.0, 0.0]
        # 2.1 raw score slightly higher than 1.2
        sections = {
            "1.2": [0.95, 0.1, 0.0],   # current, methodology
            "2.1": [0.97, 0.05, 0.0],  # slightly better raw, but different type
        }
        stype_map = {"1.2": "methodology", "2.1": "results"}

        adjusted = self._adjusted_scores(
            frag_vec, sections,
            current_section="1.2",
            section_type_map=stype_map,
            cross_type_penalty=0.05,
        )
        ranked = sorted(adjusted.items(), key=lambda x: -x[1])
        # After penalty, 2.1 should rank lower than 1.2
        assert ranked[0][0] == "1.2"

    def test_no_penalty_when_types_match(self):
        """Penalty is zero when suggested section has same type as current."""
        frag_vec = [1.0, 0.0, 0.0]
        sections = {
            "1.1": [0.9, 0.1, 0.0],
            "1.2": [0.85, 0.1, 0.0],  # same type as 1.1
        }
        stype_map = {"1.1": "methodology", "1.2": "methodology"}

        adjusted = self._adjusted_scores(
            frag_vec, sections,
            current_section="1.1",
            section_type_map=stype_map,
        )
        raw_1_2 = cosine_similarity(frag_vec, sections["1.2"])
        assert adjusted["1.2"] == pytest.approx(raw_1_2, abs=1e-6)

    def test_intent_bonus_applied_to_matching_section_type(self):
        """method intent adds bonus to methodology/experiments sections."""
        frag_vec = [1.0, 0.0, 0.0]
        sections = {
            "1.2": [0.8, 0.0, 0.0],  # methodology — matches 'method' intent
            "2.1": [0.85, 0.0, 0.0], # results — no bonus
        }
        stype_map = {"1.2": "methodology", "2.1": "results"}

        adjusted = self._adjusted_scores(
            frag_vec, sections,
            current_section="2.1",
            section_type_map=stype_map,
            frag_intent="method",
            intent_bonus=0.03,
        )
        raw_1_2 = cosine_similarity(frag_vec, sections["1.2"])
        raw_2_1 = cosine_similarity(frag_vec, sections["2.1"])

        assert adjusted["1.2"] == pytest.approx(raw_1_2 - 0.05 + 0.03, abs=1e-6)
        # 2.1 is current: no cross-type penalty (same type = "results"); no intent bonus
        assert adjusted["2.1"] == pytest.approx(raw_2_1, abs=1e-6)

    def test_min_delta_filters_low_confidence_suggestions(self):
        """Suggestions with delta below min_delta are excluded."""
        frag_vec = [1.0, 0.0, 0.0]
        current_section = "2.1"
        sections = {
            "1.1": [0.80, 0.0, 0.0],   # slightly better
            "2.1": [0.75, 0.0, 0.0],   # current
        }
        stype_map = {}
        adjusted = self._adjusted_scores(frag_vec, sections, current_section, stype_map)
        ranked = sorted(adjusted.items(), key=lambda x: -x[1])
        best_section, best_score = ranked[0]
        current_score = adjusted[current_section]
        delta = best_score - current_score

        min_delta = 0.10
        should_suggest = delta >= min_delta
        # Delta is small; with min_delta=0.10 this should NOT be suggested
        assert should_suggest is False

    def test_no_penalty_without_section_types(self):
        """When section_type_map is empty, no penalty applied."""
        frag_vec = [1.0, 0.0, 0.0]
        sections = {"1.1": [0.9, 0.0, 0.0], "2.1": [0.8, 0.0, 0.0]}

        adjusted = self._adjusted_scores(
            frag_vec, sections,
            current_section="1.1",
            section_type_map={},
        )
        for sec_id, sec_vec in sections.items():
            assert adjusted[sec_id] == pytest.approx(cosine_similarity(frag_vec, sec_vec), abs=1e-6)
