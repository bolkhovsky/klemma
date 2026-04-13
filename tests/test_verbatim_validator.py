"""Tests for the post-AI verbatim validator in skills/extractor.py."""

from __future__ import annotations

from klemma.literature.models import DowngradeStats, Fragment
from klemma.skills.extractor import _validate_verbatim_fragments


class TestScopeGate:
    """Fragments with verbatim=false are never touched by the validator."""

    def test_paraphrase_never_validated(self) -> None:
        frags = [Fragment(text="completely fabricated paraphrase", verbatim=False)]
        stats = _validate_verbatim_fragments(frags, "totally unrelated text", "s1")
        assert frags[0].verbatim is False  # stays false
        assert stats == DowngradeStats()  # zero counts — scope-gated out

    def test_empty_fragment_list(self) -> None:
        stats = _validate_verbatim_fragments([], "some pdf text", "s1")
        assert stats == DowngradeStats()


class TestExactSubstringMatch:
    def test_plain_substring_confirmed(self) -> None:
        pdf = "The model achieves 92% accuracy on the benchmark."
        frags = [Fragment(text="92% accuracy on the benchmark", verbatim=True)]
        stats = _validate_verbatim_fragments(frags, pdf, "s1")
        assert frags[0].verbatim is True
        assert stats.verbatim_confirmed == 1
        assert stats.downgraded == 0

    def test_ligature_normalized_then_confirmed(self) -> None:
        # PDF has fi-ligature; fragment has plain 'fi'. NFKC should reconcile.
        pdf = "the \ufb01nal e\ufb00ective forecast was accurate"
        frags = [Fragment(text="final effective forecast", verbatim=True)]
        stats = _validate_verbatim_fragments(frags, pdf, "s1")
        assert frags[0].verbatim is True
        assert stats.verbatim_confirmed == 1

    def test_line_break_hyphen_confirmed(self) -> None:
        # PDF has "fore-\ncast" (typical PDF extraction artifact).
        pdf = "the fore-\ncast skill is poor"
        frags = [Fragment(text="forecast skill is poor", verbatim=True)]
        stats = _validate_verbatim_fragments(frags, pdf, "s1")
        assert frags[0].verbatim is True
        assert stats.verbatim_confirmed == 1


class TestFuzzyRescue:
    def test_single_char_swap_rescued(self) -> None:
        pdf = "decomposition separates overestimation from underestimation errors."
        # One-char noise near the start: 'decompositioa' vs 'decomposition'
        frags = [Fragment(
            text="decompositioa separates overestimation from underestimation",
            verbatim=True,
        )]
        stats = _validate_verbatim_fragments(frags, pdf, "s1")
        assert frags[0].verbatim is True
        assert stats.fuzzy_rescued == 1
        assert stats.downgraded == 0

    def test_fabrication_downgraded(self) -> None:
        pdf = "Arctic sea ice concentration is predicted using a neural network."
        frags = [Fragment(
            text="Transformers achieve state-of-the-art on ImageNet benchmarks",
            verbatim=True,
        )]
        stats = _validate_verbatim_fragments(frags, pdf, "s1")
        assert frags[0].verbatim is False
        assert stats.downgraded == 1
        assert stats.fuzzy_rescued == 0

    def test_below_threshold_downgraded(self) -> None:
        # Mostly rewritten — ratio stays well below 0.95.
        pdf = "The model attains 88% macro-F1 on the held-out split."
        frags = [Fragment(
            text="The system achieves 50% F1 on custom evaluation",
            verbatim=True,
        )]
        stats = _validate_verbatim_fragments(frags, pdf, "s1")
        assert frags[0].verbatim is False
        assert stats.downgraded == 1


class TestMixedBatch:
    def test_realistic_mix(self) -> None:
        pdf = (
            "The effective forecast skill for sea ice extent is poor. "
            "We propose a neural network approach. "
            "IIEE decomposition separates overestimation from underestimation."
        )
        frags = [
            Fragment(text="neural network approach", verbatim=True),  # exact
            Fragment(text="decompositiom separates overestimation", verbatim=True),  # fuzzy
            Fragment(text="GPT-4 scored 99% on LaTeX", verbatim=True),  # fabrication
            Fragment(text="the authors present a novel method", verbatim=False),  # paraphrase, skipped
        ]
        stats = _validate_verbatim_fragments(frags, pdf, "s1")
        assert stats.verbatim_claimed == 3
        assert stats.verbatim_confirmed == 1
        assert stats.fuzzy_rescued == 1
        assert stats.downgraded == 1
        assert [f.verbatim for f in frags] == [True, True, False, False]


class TestEdgeCases:
    def test_empty_pdf_text_leaves_flags(self) -> None:
        frags = [Fragment(text="some claim", verbatim=True)]
        stats = _validate_verbatim_fragments(frags, "", "s1")
        # Can't validate, must not silently downgrade.
        assert frags[0].verbatim is True
        assert stats == DowngradeStats()

    def test_empty_fragment_text_downgraded(self) -> None:
        frags = [Fragment(text="   \n  ", verbatim=True)]
        stats = _validate_verbatim_fragments(frags, "real content here", "s1")
        assert frags[0].verbatim is False
        assert stats.downgraded == 1
