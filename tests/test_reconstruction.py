"""Tests for citation reconstruction benchmark — metrics, models, runners, prompts."""

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from klemma.evaluation.dataset import (
    BenchmarkDataset,
    PaperSection,
    ReconstructionDataset,
    ReconstructionGroundTruth,
    ReconstructionSample,
    SectionCitation,
    load_dataset,
)
from klemma.evaluation.metrics import reconstruction_metrics
from klemma.evaluation.reconstruction import (
    compute_baseline,
    run_reconstruction,
    run_reconstruction_benchmark,
)
from klemma.state import StateManager

# --- Pure metric tests ---


class TestReconstructionMetrics:
    """Test reconstruction_metrics (macro P/R, F1, intent accuracy, nDCG)."""

    def test_perfect_reconstruction(self):
        gt = [
            {"section_id": "1", "citekey": "a", "intent": "background"},
            {"section_id": "2", "citekey": "b", "intent": "method"},
        ]
        preds = [
            {"section_id": "1", "citekey": "a", "intent": "background"},
            {"section_id": "2", "citekey": "b", "intent": "method"},
        ]
        result = reconstruction_metrics(preds, gt)
        assert result["macro_precision"] == 1.0
        assert result["macro_recall"] == 1.0
        assert result["f1"] == 1.0
        assert result["intent_accuracy"] == 1.0
        assert result["ndcg_avg"] == 1.0

    def test_empty_predictions(self):
        gt = [
            {"section_id": "1", "citekey": "a", "intent": "background"},
        ]
        result = reconstruction_metrics([], gt)
        assert result["macro_precision"] == 0.0
        assert result["macro_recall"] == 0.0
        assert result["f1"] == 0.0

    def test_empty_ground_truth(self):
        result = reconstruction_metrics(
            [{"section_id": "1", "citekey": "a", "intent": "background"}],
            [],
        )
        assert result["total_gt"] == 0
        assert result["f1"] == 0.0

    def test_partial_overlap(self):
        gt = [
            {"section_id": "1", "citekey": "a", "intent": "background"},
            {"section_id": "1", "citekey": "b", "intent": "method"},
            {"section_id": "2", "citekey": "c", "intent": "result_comparison"},
        ]
        preds = [
            {"section_id": "1", "citekey": "a", "intent": "background"},
            {"section_id": "1", "citekey": "x", "intent": "method"},  # wrong
            {"section_id": "2", "citekey": "c", "intent": "result_comparison"},
        ]
        result = reconstruction_metrics(preds, gt)
        # Section 1: P=1/2, R=1/2; Section 2: P=1, R=1
        assert result["macro_precision"] == pytest.approx(0.75, abs=0.01)
        assert result["macro_recall"] == pytest.approx(0.75, abs=0.01)
        assert result["intent_accuracy"] == 1.0  # matched pairs all correct

    def test_intent_mismatch(self):
        gt = [
            {"section_id": "1", "citekey": "a", "intent": "background"},
            {"section_id": "1", "citekey": "b", "intent": "method"},
        ]
        preds = [
            {"section_id": "1", "citekey": "a", "intent": "method"},      # wrong intent
            {"section_id": "1", "citekey": "b", "intent": "background"},   # wrong intent
        ]
        result = reconstruction_metrics(preds, gt)
        assert result["macro_precision"] == 1.0  # citekeys match
        assert result["macro_recall"] == 1.0
        assert result["intent_accuracy"] == 0.0  # intents wrong

    def test_ndcg_ordering(self):
        """Higher-relevance items (method > result_comparison > background) ranked first get better nDCG."""
        gt = [
            {"section_id": "1", "citekey": "a", "intent": "method"},            # relevance 3
            {"section_id": "1", "citekey": "b", "intent": "result_comparison"},  # relevance 2
            {"section_id": "1", "citekey": "c", "intent": "background"},         # relevance 1
        ]
        # Perfect order
        preds_good = [
            {"section_id": "1", "citekey": "a", "intent": "method"},
            {"section_id": "1", "citekey": "b", "intent": "result_comparison"},
            {"section_id": "1", "citekey": "c", "intent": "background"},
        ]
        # Reversed order
        preds_bad = [
            {"section_id": "1", "citekey": "c", "intent": "background"},
            {"section_id": "1", "citekey": "b", "intent": "result_comparison"},
            {"section_id": "1", "citekey": "a", "intent": "method"},
        ]
        good = reconstruction_metrics(preds_good, gt)
        bad = reconstruction_metrics(preds_bad, gt)
        assert good["ndcg_avg"] == 1.0
        assert bad["ndcg_avg"] < good["ndcg_avg"]

    def test_multi_section(self):
        gt = [
            {"section_id": "1", "citekey": "a", "intent": "background"},
            {"section_id": "2", "citekey": "b", "intent": "method"},
            {"section_id": "3", "citekey": "c", "intent": "result_comparison"},
        ]
        # Only predict for section 1 and 2
        preds = [
            {"section_id": "1", "citekey": "a", "intent": "background"},
            {"section_id": "2", "citekey": "b", "intent": "method"},
        ]
        result = reconstruction_metrics(preds, gt)
        # Section 1: P=1, R=1; Section 2: P=1, R=1; Section 3: P=0, R=0
        assert result["macro_precision"] == pytest.approx(2 / 3, abs=0.01)
        assert result["macro_recall"] == pytest.approx(2 / 3, abs=0.01)
        assert result["per_section"]["3"]["recall"] == 0.0

    def test_per_section_structure(self):
        gt = [{"section_id": "1.1", "citekey": "a", "intent": "background"}]
        preds = [{"section_id": "1.1", "citekey": "a", "intent": "background"}]
        result = reconstruction_metrics(preds, gt)
        assert "1.1" in result["per_section"]
        ps = result["per_section"]["1.1"]
        assert ps["gt_count"] == 1
        assert ps["pred_count"] == 1
        assert ps["hits"] == 1


# --- Dataset model tests ---


class TestReconstructionDataset:
    def test_ground_truth_validation(self):
        gt = ReconstructionGroundTruth(
            paper_citekey="test2020",
            paper_title="Test Paper",
            abstract="This paper studies X.",
            keywords=["sea ice", "remote sensing"],
            sections=[
                PaperSection(
                    section_id="1",
                    title="Introduction",
                    description="Introduces the problem of sea ice monitoring.",
                    citations=[
                        SectionCitation(
                            citekey="smith2020",
                            title="Smith Paper",
                            intent="background",
                            in_library=True,
                        )
                    ],
                )
            ],
            bibliography_size=42,
        )
        assert gt.paper_citekey == "test2020"
        assert gt.abstract == "This paper studies X."
        assert gt.keywords == ["sea ice", "remote sensing"]
        assert len(gt.sections) == 1
        assert gt.sections[0].description == "Introduces the problem of sea ice monitoring."
        assert gt.sections[0].citations[0].in_library is True

    def test_ground_truth_optional_fields_default(self):
        gt = ReconstructionGroundTruth(
            paper_citekey="bare2020",
            paper_title="Bare Paper",
        )
        assert gt.abstract == ""
        assert gt.keywords == []
        assert gt.sections == []

    def test_section_description_optional(self):
        section = PaperSection(section_id="1", title="Intro")
        assert section.description == ""

    def test_sample_validation(self):
        sample = ReconstructionSample(
            section_id="2.1",
            citekey="jones2021",
            intent="method",
        )
        assert sample.section_id == "2.1"

    def test_invalid_intent_rejected(self):
        with pytest.raises(Exception):
            ReconstructionSample(
                section_id="1",
                citekey="x",
                intent="INVALID",
            )

    def test_roundtrip_serialization(self, tmp_path):
        gt = ReconstructionGroundTruth(
            paper_citekey="pilot2020",
            paper_title="Pilot Study",
            abstract="A study of citation patterns.",
            keywords=["citations", "NLP"],
            sections=[
                PaperSection(
                    section_id="1",
                    title="Intro",
                    description="Introduces citation analysis problem.",
                    citations=[
                        SectionCitation(title="Ref A", intent="background"),
                        SectionCitation(
                            citekey="refB",
                            title="Ref B",
                            intent="method",
                            in_library=True,
                        ),
                    ],
                ),
            ],
            bibliography_size=10,
        )
        samples = [
            ReconstructionSample(section_id="1", citekey="refB", intent="method"),
        ]
        recon_ds = ReconstructionDataset(ground_truth=gt, samples=samples)
        ds = BenchmarkDataset(reconstruction=recon_ds)

        p = tmp_path / "recon.json"
        p.write_text(json.dumps(ds.model_dump()))
        loaded = load_dataset(p)
        assert loaded.reconstruction is not None
        rgt = loaded.reconstruction.ground_truth
        assert rgt.paper_citekey == "pilot2020"
        assert rgt.abstract == "A study of citation patterns."
        assert rgt.keywords == ["citations", "NLP"]
        assert rgt.sections[0].description == "Introduces citation analysis problem."
        assert len(loaded.reconstruction.samples) == 1
        assert loaded.reconstruction.samples[0].citekey == "refB"

    def test_benchmark_dataset_without_reconstruction(self):
        ds = BenchmarkDataset()
        assert ds.reconstruction is None

    def test_citation_without_citekey(self):
        """Citations not in library have citekey=None."""
        cit = SectionCitation(title="Unknown Paper", intent="background")
        assert cit.citekey is None
        assert cit.in_library is False


# --- Runner integration tests ---


@pytest.fixture
def state(tmp_path):
    return StateManager(tmp_path / "test.db")


def _make_dataset(samples=None):
    """Helper to create a minimal ReconstructionDataset with context."""
    gt = ReconstructionGroundTruth(
        paper_citekey="pilot2020",
        paper_title="Pilot Study on Citation Patterns",
        abstract="This paper analyzes citation patterns in academic literature.",
        keywords=["citations", "bibliometrics", "NLP"],
        sections=[
            PaperSection(
                section_id="1", title="Introduction",
                description="Introduces the problem of citation analysis.",
            ),
            PaperSection(
                section_id="2", title="Methods",
                description="Describes the NLP pipeline for citation extraction.",
            ),
        ],
        bibliography_size=20,
    )
    if samples is None:
        samples = [
            ReconstructionSample(section_id="1", citekey="sourceA", intent="background"),
            ReconstructionSample(section_id="2", citekey="sourceB", intent="method"),
        ]
    return ReconstructionDataset(ground_truth=gt, samples=samples)


class TestComputeBaseline:
    def test_matches_db_fragments(self, state):
        state.register_sources(["sourceA", "sourceB"])
        state.save_fragments("sourceA", [
            {"text": "Background info", "type": "key_idea",
             "section": "1", "relevance": 3, "citation_intent": "background"},
        ])
        state.save_fragments("sourceB", [
            {"text": "Method detail", "type": "methodology",
             "section": "2", "relevance": 4, "citation_intent": "method"},
        ])

        dataset = _make_dataset()
        result = compute_baseline(state, dataset)
        assert result["method"] == "baseline"
        assert result["macro_recall"] == 1.0
        assert result["macro_precision"] == 1.0
        assert result["f1"] == 1.0

    def test_no_matching_fragments(self, state):
        state.register_sources(["sourceA", "sourceB"])
        # No fragments saved

        dataset = _make_dataset()
        result = compute_baseline(state, dataset)
        assert result["predictions_count"] == 0
        assert result["macro_recall"] == 0.0

    def test_partial_match(self, state):
        state.register_sources(["sourceA", "sourceB"])
        state.save_fragments("sourceA", [
            {"text": "Background info", "type": "key_idea",
             "section": "1", "relevance": 3, "citation_intent": "background"},
        ])
        # sourceB has no fragments

        dataset = _make_dataset()
        result = compute_baseline(state, dataset)
        assert result["predictions_count"] == 1
        assert result["macro_recall"] > 0
        assert result["macro_recall"] < 1.0

    def test_deduplicates_predictions(self, state):
        state.register_sources(["sourceA"])
        state.save_fragments("sourceA", [
            {"text": "Frag 1", "type": "key_idea",
             "section": "1", "relevance": 3, "citation_intent": "background"},
            {"text": "Frag 2", "type": "key_idea",
             "section": "1", "relevance": 4, "citation_intent": "background"},
        ])
        dataset = _make_dataset(samples=[
            ReconstructionSample(section_id="1", citekey="sourceA", intent="background"),
        ])
        result = compute_baseline(state, dataset)
        assert result["predictions_count"] == 1  # deduplicated


class TestRunReconstruction:
    def test_ai_driven_reconstruction(self, state):
        state.register_sources(["sourceA", "sourceB"])
        state.save_fragments("sourceA", [
            {"text": "Background info", "type": "key_idea",
             "section": "1", "relevance": 3, "citation_intent": "background"},
        ])

        # Mock AI to return correct recommendations
        mock_ai = MagicMock()
        mock_ai.call_json.return_value = {
            "recommendations": [
                {"section_id": "1", "citekey": "sourceA",
                 "intent": "background", "justification": "test"},
                {"section_id": "2", "citekey": "sourceB",
                 "intent": "method", "justification": "test"},
            ]
        }
        mock_ai.render_prompt.return_value = "rendered prompt"

        dataset = _make_dataset()
        result = run_reconstruction(mock_ai, state, dataset)
        assert result["method"] == "reconstruction"
        assert result["predictions_count"] == 2
        assert result["macro_recall"] == 1.0

    def test_ai_failure(self, state):
        state.register_sources(["sourceA"])
        mock_ai = MagicMock()
        mock_ai.call_json.return_value = None
        mock_ai.render_prompt.return_value = "rendered prompt"

        dataset = _make_dataset()
        result = run_reconstruction(mock_ai, state, dataset)
        assert result.get("error") == "AI call failed"

    def test_deduplicates_ai_recommendations(self, state):
        state.register_sources(["sourceA"])
        mock_ai = MagicMock()
        mock_ai.call_json.return_value = {
            "recommendations": [
                {"section_id": "1", "citekey": "sourceA",
                 "intent": "background", "justification": "first"},
                {"section_id": "1", "citekey": "sourceA",
                 "intent": "method", "justification": "duplicate"},
            ]
        }
        mock_ai.render_prompt.return_value = "rendered prompt"

        dataset = _make_dataset(samples=[
            ReconstructionSample(section_id="1", citekey="sourceA", intent="background"),
        ])
        result = run_reconstruction(mock_ai, state, dataset)
        assert result["predictions_count"] == 1


class TestRunReconstructionBenchmark:
    def test_full_benchmark_baseline_only(self, state):
        state.register_sources(["sourceA", "sourceB"])
        state.save_fragments("sourceA", [
            {"text": "Info", "type": "key_idea",
             "section": "1", "relevance": 3, "citation_intent": "background"},
        ])

        dataset = _make_dataset()
        result = run_reconstruction_benchmark(state, dataset)
        assert "ground_truth" in result
        assert "baseline" in result
        assert "reconstruction" not in result
        assert result["ground_truth"]["sections"] == 2
        assert result["ground_truth"]["samples"] == 2

    def test_full_benchmark_with_ai(self, state):
        state.register_sources(["sourceA", "sourceB"])
        mock_ai = MagicMock()
        mock_ai.call_json.return_value = {
            "recommendations": [
                {"section_id": "1", "citekey": "sourceA",
                 "intent": "background", "justification": "test"},
            ]
        }
        mock_ai.render_prompt.return_value = "rendered prompt"

        dataset = _make_dataset()
        result = run_reconstruction_benchmark(state, dataset, ai=mock_ai)
        assert "reconstruction" in result
        assert result["reconstruction"]["method"] == "reconstruction"


class TestRunAllWithReconstruction:
    def test_reconstruct_filter(self, state):
        state.register_sources(["sourceA"])
        dataset = _make_dataset(samples=[
            ReconstructionSample(section_id="1", citekey="sourceA", intent="background"),
        ])
        ds = BenchmarkDataset(reconstruction=dataset)

        from klemma.evaluation.runners import run_all
        result = run_all(state, ds, "reconstruct")
        assert "reconstruction" in result
        assert "intent" not in result

    def test_all_filter_includes_reconstruction(self, state):
        state.register_sources(["sourceA"])
        dataset = _make_dataset(samples=[
            ReconstructionSample(section_id="1", citekey="sourceA", intent="background"),
        ])
        ds = BenchmarkDataset(reconstruction=dataset)

        from klemma.evaluation.runners import run_all
        result = run_all(state, ds, "all")
        assert "reconstruction" in result


# --- Prompt rendering tests ---


class TestPromptRendering:
    def test_analyst_template_renders(self):
        from jinja2 import Template
        prompt_path = Path(__file__).parent.parent / "prompts" / "analyst.md"
        template = Template(prompt_path.read_text(encoding="utf-8"))
        rendered = template.render(
            pdf_text="Sample paper text...",
            library_entries="- smith2020: Some Paper",
            paper_citekey="test2020",
            paper_title="Test Paper",
        )
        assert "test2020" in rendered
        assert "Sample paper text" in rendered
        assert "smith2020" in rendered

    def test_reconstruct_template_renders(self):
        from jinja2 import Template
        prompt_path = Path(__file__).parent.parent / "prompts" / "reconstruct.md"
        template = Template(prompt_path.read_text(encoding="utf-8"))
        rendered = template.render(
            paper_title="My Research Paper",
            abstract="This paper studies citation patterns.",
            keywords=["citations", "NLP", "bibliometrics"],
            sections=[
                {"section_id": "1", "title": "Introduction",
                 "description": "Introduces the problem of citation analysis."},
                {"section_id": "2", "title": "Methods",
                 "description": "Describes the NLP pipeline."},
            ],
            sources=[
                {
                    "citekey": "smith2020",
                    "title": "Smith Paper",
                    "year": "2020",
                    "abstract": "Smith's abstract about NLP.",
                    "fragments": [
                        {"intent": "background", "text": "Important finding"},
                    ],
                },
            ],
        )
        assert "My Research Paper" in rendered
        assert "citation patterns" in rendered
        assert "citations, NLP, bibliometrics" in rendered
        assert "Introduces the problem" in rendered
        assert "Smith's abstract" in rendered
        assert "smith2020" in rendered
        assert "Important finding" in rendered

    def test_reconstruct_template_handles_missing_optional_fields(self):
        """Template should render even without abstract/keywords/description."""
        from jinja2 import Template
        prompt_path = Path(__file__).parent.parent / "prompts" / "reconstruct.md"
        template = Template(prompt_path.read_text(encoding="utf-8"))
        rendered = template.render(
            paper_title="Bare Paper",
            abstract="",
            keywords=[],
            sections=[
                {"section_id": "1", "title": "Introduction", "description": ""},
            ],
            sources=[
                {
                    "citekey": "jones2021",
                    "title": "Jones Paper",
                    "year": "2021",
                    "abstract": "",
                    "fragments": [],
                },
            ],
        )
        assert "Bare Paper" in rendered
        assert "jones2021" in rendered
