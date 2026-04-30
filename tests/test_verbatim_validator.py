"""Tests for the post-AI verbatim validator in skills/extractor.py."""

from __future__ import annotations

import pytest

from klemma.literature.models import DowngradeStats, Fragment
from klemma.skills.extractor import validate_verbatim_fragments


class TestScopeGate:
    """Fragments with verbatim=false are never touched by the validator."""

    def test_paraphrase_never_validated(self) -> None:
        frags = [Fragment(text="completely fabricated paraphrase", verbatim=False)]
        stats = validate_verbatim_fragments(frags, "totally unrelated text", "s1")
        assert frags[0].verbatim is False  # stays false
        assert stats == DowngradeStats()  # zero counts — scope-gated out

    def test_empty_fragment_list(self) -> None:
        stats = validate_verbatim_fragments([], "some pdf text", "s1")
        assert stats == DowngradeStats()


class TestExactSubstringMatch:
    def test_plain_substring_confirmed(self) -> None:
        pdf = "The model achieves 92% accuracy on the benchmark."
        frags = [Fragment(text="92% accuracy on the benchmark", verbatim=True)]
        stats = validate_verbatim_fragments(frags, pdf, "s1")
        assert frags[0].verbatim is True
        assert stats.verbatim_confirmed == 1
        assert stats.downgraded == 0

    def test_ligature_normalized_then_confirmed(self) -> None:
        # PDF has fi-ligature; fragment has plain 'fi'. NFKC should reconcile.
        pdf = "the \ufb01nal e\ufb00ective forecast was accurate"
        frags = [Fragment(text="final effective forecast", verbatim=True)]
        stats = validate_verbatim_fragments(frags, pdf, "s1")
        assert frags[0].verbatim is True
        assert stats.verbatim_confirmed == 1

    def test_line_break_hyphen_confirmed(self) -> None:
        # PDF has "fore-\ncast" (typical PDF extraction artifact).
        pdf = "the fore-\ncast skill is poor"
        frags = [Fragment(text="forecast skill is poor", verbatim=True)]
        stats = validate_verbatim_fragments(frags, pdf, "s1")
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
        stats = validate_verbatim_fragments(frags, pdf, "s1")
        assert frags[0].verbatim is True
        assert stats.fuzzy_rescued == 1
        assert stats.downgraded == 0

    def test_fabrication_downgraded(self) -> None:
        pdf = "Arctic sea ice concentration is predicted using a neural network."
        frags = [Fragment(
            text="Transformers achieve state-of-the-art on ImageNet benchmarks",
            verbatim=True,
        )]
        stats = validate_verbatim_fragments(frags, pdf, "s1")
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
        stats = validate_verbatim_fragments(frags, pdf, "s1")
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
        stats = validate_verbatim_fragments(frags, pdf, "s1")
        assert stats.verbatim_claimed == 3
        assert stats.verbatim_confirmed == 1
        assert stats.fuzzy_rescued == 1
        assert stats.downgraded == 1
        assert [f.verbatim for f in frags] == [True, True, False, False]


class TestEdgeCases:
    def test_empty_pdf_text_leaves_flags(self) -> None:
        frags = [Fragment(text="some claim", verbatim=True)]
        stats = validate_verbatim_fragments(frags, "", "s1")
        # Can't validate, must not silently downgrade.
        assert frags[0].verbatim is True
        assert stats == DowngradeStats()

    def test_empty_fragment_text_downgraded(self) -> None:
        frags = [Fragment(text="   \n  ", verbatim=True)]
        stats = validate_verbatim_fragments(frags, "real content here", "s1")
        assert frags[0].verbatim is False
        assert stats.downgraded == 1


@pytest.mark.benchmark
def test_validator_runtime_at_1mb_with_realistic_text() -> None:
    """#382: ensure the 1 MB validation window stays within reasonable runtime
    on representative academic text.

    Synthetic 1 MB pdf_text built from a realistic vocabulary of ~200 unique
    English/scientific words so difflib.SequenceMatcher's position-dict has
    proper diversity (a tiny vocabulary turns SequenceMatcher into an
    adversarial worst case — every char has thousands of positions).
    100 fragments: 80 real (exact substring fast path), 20 fake (fuzzy
    rescue path).

    Skipped from default CI runs via the `benchmark` marker. Run via:
        pytest tests/test_verbatim_validator.py -m benchmark
    """
    import random
    import time

    rng = random.Random(382)
    # Realistic-ish vocabulary — 200+ words drawn from typical academic
    # corpus topics. Enough lexical diversity that the position-dict
    # behaves like a real PDF.
    vocab = (
        "model models modeling ice sea seasonal Antarctic Arctic Pacific "
        "Atlantic data dataset results method methodology approach proposed "
        "satellite SAR optical passive microwave radar altimeter forecast "
        "ensemble validation evaluation experiment experiments anomaly "
        "anomalies trend trends decade decades climate variability change "
        "concentration extent area thickness drift motion deformation lead "
        "ridge floe pack fast melt freeze pond snow albedo radiation flux "
        "atmospheric oceanic boundary layer surface temperature pressure "
        "wind salinity density transport import export advection convection "
        "uncertainty error bias correlation regression coefficient parameter "
        "neural network deep learning convolutional recurrent attention "
        "transformer encoder decoder layer feature representation embedding "
        "training inference prediction output input target loss gradient "
        "optimizer Adam batch epoch validation cross fold split early "
        "stopping regularization dropout normalization activation ReLU GELU "
        "softmax sigmoid linear projection token sequence position embedding "
        "Smith Lee Chen Wang Zhao Patel Anderson Korolev Goessling Lavergne"
    ).split()

    pdf_words: list[str] = []
    total_len = 0
    while total_len < 1_000_000:
        w = rng.choice(vocab)
        pdf_words.append(w)
        total_len += len(w) + 1  # +1 for the space separator
    pdf_text = " ".join(pdf_words)[:1_000_000]
    assert len(pdf_text) == 1_000_000

    # 80 real quotes (exact substring fast path)
    real_quotes = []
    for _ in range(80):
        start = rng.randint(0, len(pdf_text) - 150)
        real_quotes.append(pdf_text[start:start + rng.randint(40, 120)])
    # 20 fabricated (fuzzy rescue path)
    fakes = [
        f"Fabricated quote {i} about an entirely unrelated topic with random tokens"
        for i in range(20)
    ]
    fragments = [Fragment(text=q, verbatim=True) for q in real_quotes + fakes]

    t0 = time.monotonic()
    stats = validate_verbatim_fragments(fragments, pdf_text, "bench")
    elapsed = time.monotonic() - t0

    # Generous bound — real abuzyarov2011 (339K, 234 frags) validates in
    # well under this on the prod worker. Anything close to the bound
    # signals an algorithmic regression worth investigating.
    assert elapsed < 60.0, f"Validator took {elapsed:.1f}s on 1 MB / 100 frags — exceeds budget"
    # Real quotes confirmed; fakes downgraded
    assert stats.verbatim_confirmed >= 70, stats
    assert stats.downgraded >= 15, stats
    assert stats.verbatim_claimed == 100
