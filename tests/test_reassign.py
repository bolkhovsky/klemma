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
