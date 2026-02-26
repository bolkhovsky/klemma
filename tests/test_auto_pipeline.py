"""Tests for autonomous benchmark pipeline (Step 4)."""

from unittest.mock import MagicMock, patch

import pytest

from klemma.evaluation.dataset import (
    PaperSection,
    ReconstructionDataset,
    ReconstructionGroundTruth,
    ReconstructionSample,
    SectionCitation,
)
from klemma.evaluation.pipeline import run_analyst_from_source, run_auto_benchmark
from klemma.state import StateManager


@pytest.fixture
def state(tmp_path):
    return StateManager(tmp_path / "test.db")


def _mock_config():
    cfg = MagicMock()
    cfg.ai.max_pdf_chars = 50000
    cfg.ai.backend = "claude"
    cfg.ai.model = "opus"
    cfg.zotero.library_json = None
    cfg.zotero.storage_path = "/tmp/storage"
    return cfg


def _mock_gt():
    """Create a minimal ground truth for testing."""
    return ReconstructionGroundTruth(
        paper_citekey="test2020",
        paper_title="Test Paper",
        abstract="Abstract text.",
        sections=[
            PaperSection(
                section_id="1",
                title="Introduction",
                citations=[
                    SectionCitation(
                        citekey="sourceA", title="Source A",
                        intent="background", in_library=True,
                    ),
                ],
            ),
        ],
        bibliography_size=10,
    )


def _mock_dataset():
    gt = _mock_gt()
    return ReconstructionDataset(
        ground_truth=gt,
        samples=[
            ReconstructionSample(section_id="1", citekey="sourceA", intent="background"),
        ],
    )


class TestRunAnalystFromSource:
    @patch("klemma.evaluation.reconstruction.run_analyst")
    @patch("klemma.literature.pdf.PDFExtractor")
    def test_successful_extraction(self, mock_extractor_cls, mock_analyst, state):
        state.register_sources(["test2020"])
        state.set_pdf_path("test2020", "/fake/test.pdf")

        mock_extractor_instance = MagicMock()
        mock_extractor_instance.extract.return_value = "PDF text content..."
        mock_extractor_cls.return_value = mock_extractor_instance

        mock_analyst.return_value = _mock_gt()

        config = _mock_config()
        with patch("pathlib.Path.exists", return_value=True):
            dataset = run_analyst_from_source(state, MagicMock(), "test2020", config)

        assert dataset is not None
        assert len(dataset.samples) == 1
        assert dataset.ground_truth.paper_citekey == "test2020"

    def test_missing_source(self, state):
        config = _mock_config()
        result = run_analyst_from_source(state, MagicMock(), "nonexistent", config)
        assert result is None

    @patch("klemma.literature.pdf.PDFExtractor")
    def test_no_pdf(self, mock_extractor_cls, state):
        state.register_sources(["nopdf"])
        config = _mock_config()
        result = run_analyst_from_source(state, MagicMock(), "nopdf", config)
        assert result is None


class TestRunAutoBenchmark:
    @patch("klemma.evaluation.reconstruction.run_reconstruction_benchmark")
    def test_full_pipeline_with_explicit_paper(self, mock_benchmark, state):
        state.register_sources(["sourceA"])
        mock_benchmark.return_value = {
            "ground_truth": {"paper": "test2020", "sections": 1},
            "baseline": {"source_coverage": 1.0},
        }

        config = _mock_config()
        with patch.object(
            __import__("klemma.evaluation.pipeline", fromlist=["run_analyst_from_source"]),
            "run_analyst_from_source",
            return_value=_mock_dataset(),
        ):
            result = run_auto_benchmark(
                state, MagicMock(), config,
                paper_citekey="test2020",
                skip_prepare=True,
            )

        assert result.paper_citekey == "test2020"
        assert result.run_id  # should be saved
        assert "reconstruction" in result.results

    def test_analyst_failure(self, state):
        config = _mock_config()
        with patch(
            "klemma.evaluation.pipeline.run_analyst_from_source",
            return_value=None,
        ):
            result = run_auto_benchmark(
                state, MagicMock(), config,
                paper_citekey="bad_paper",
                skip_prepare=True,
            )
        assert "error" in result.results

    @patch("klemma.evaluation.reconstruction.run_reconstruction_benchmark")
    @patch("klemma.evaluation.candidates.discover_candidates")
    def test_auto_select_candidate(self, mock_candidates, mock_benchmark, state):
        from klemma.evaluation.candidates import CandidateScore
        mock_candidates.return_value = [
            CandidateScore(citekey="auto_pick", score=20),
        ]
        mock_benchmark.return_value = {
            "baseline": {"source_coverage": 0.5},
        }

        config = _mock_config()
        with patch(
            "klemma.evaluation.pipeline.run_analyst_from_source",
            return_value=_mock_dataset(),
        ):
            result = run_auto_benchmark(
                state, MagicMock(), config,
                skip_prepare=True,
            )
        assert result.paper_citekey == "auto_pick"

    @patch("klemma.evaluation.candidates.discover_candidates", return_value=[])
    def test_no_candidates(self, mock_candidates, state):
        config = _mock_config()
        result = run_auto_benchmark(
            state, MagicMock(), config,
            skip_prepare=True,
        )
        assert "error" in result.results

    @patch("klemma.evaluation.reconstruction.run_reconstruction_benchmark")
    def test_comparison_with_previous(self, mock_benchmark, state):
        mock_benchmark.return_value = {
            "baseline": {"source_coverage": 0.5},
        }
        config = _mock_config()

        with patch(
            "klemma.evaluation.pipeline.run_analyst_from_source",
            return_value=_mock_dataset(),
        ):
            result1 = run_auto_benchmark(
                state, MagicMock(), config,
                paper_citekey="test2020",
                skip_prepare=True,
            )
        assert result1.run_id

        mock_benchmark.return_value = {
            "baseline": {"source_coverage": 0.8},
        }
        with patch(
            "klemma.evaluation.pipeline.run_analyst_from_source",
            return_value=_mock_dataset(),
        ):
            result2 = run_auto_benchmark(
                state, MagicMock(), config,
                paper_citekey="test2020",
                skip_prepare=True,
            )
        assert result2.previous_run_id == result1.run_id
        assert result2.comparison is not None
