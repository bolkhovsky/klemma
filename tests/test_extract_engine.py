"""Pure chunked-extraction engine (plan C1): skills/extract_engine.py.

All AI calls are scripted MagicMocks; no network, no DB, no vault.
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from klemma.ai import AICallResult, normalize_finish_reason
from klemma.literature.models import Fragment, ZoteroEntry
from klemma.literature.pdf import ChunkRecord
from klemma.skills.extract_engine import (
    Budget,
    ChunkOutcome,
    ExtractedFragment,
    build_full_text,
    compute_coverage,
    dedup_by_text_and_span,
    estimate_cost_usd,
    extract_from_pages,
)

ENTRY = ZoteroEntry(id="paper2025", title="Test paper")


def _result(payload, *, finish="stop", tin=10, tout=5, text=None) -> AICallResult:
    return AICallResult(
        text=text if text is not None else json.dumps(payload),
        input_tokens=tin,
        output_tokens=tout,
        model="test-model",
        finish_reason=finish,
    )


def _ai(script):
    """AI mock whose call_with_meta pops scripted results in order."""
    ai = MagicMock()
    ai.model = "test-model"
    ai.render_prompt.return_value = "prompt"
    queue = list(script)
    calls = []

    def _call(system, user, **kw):
        calls.append((system, user, kw))
        item = queue.pop(0)
        return item(len(calls)) if callable(item) else item

    ai.call_with_meta.side_effect = _call
    ai._calls = calls
    return ai


def _pages(n=3, size=1200):
    return [f"Page {i + 1} body. " * (size // 14) for i in range(n)]


# ---------------------------------------------------------------------------
# finish_reason normalisation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("stop", "stop"), ("end_turn", "stop"), ("length", "max_tokens"),
        ("max_tokens", "max_tokens"), ("content_filter", "error"), (None, "unknown"),
        ("", "unknown"), (MagicMock(), "unknown"), ("weird", "unknown"),
    ],
)
def test_normalize_finish_reason(raw, expected):
    assert normalize_finish_reason(raw) == expected


# ---------------------------------------------------------------------------
# Coverage by interval union
# ---------------------------------------------------------------------------


def test_coverage_union_never_exceeds_100_percent():
    chunks = [
        ChunkOutcome(index=0, char_start=0, char_end=60, status="ok"),
        ChunkOutcome(index=1, char_start=50, char_end=100, status="ok"),  # overlap
        ChunkOutcome(index=2, char_start=0, char_end=100, status="split"),  # parent, ignored
    ]
    cov = compute_coverage(chunks, 100)
    assert cov.covered_chars == 100
    assert cov.ratio == 1.0
    assert cov.complete
    assert cov.uncovered == []


def test_coverage_reports_gaps_from_failed_chunks():
    chunks = [
        ChunkOutcome(index=0, char_start=0, char_end=40, status="ok"),
        ChunkOutcome(index=1, char_start=40, char_end=70, status="failed"),
        ChunkOutcome(index=2, char_start=70, char_end=100, status="ok"),
    ]
    cov = compute_coverage(chunks, 100)
    assert cov.covered_chars == 70
    assert cov.uncovered == [(40, 70)]
    assert not cov.complete


# ---------------------------------------------------------------------------
# Accumulation across chunks, prompt hash, tokens
# ---------------------------------------------------------------------------


def test_accumulates_fragments_and_tokens_across_chunks(tmp_path):
    prompt = tmp_path / "extract.md"
    prompt.write_text("{{ pdf_text }}")
    pages = _pages(3)
    full = build_full_text(pages)
    quote0 = pages[0][:40]
    quote2 = pages[2][:40]
    ai = _ai([
        _result({"fragments": [{"text": quote0, "verbatim": True, "page": 1}],
                 "key_references": [{"title": "R1"}], "summary": "short"}, tin=100, tout=20),
        _result({"fragments": [{"text": quote2, "verbatim": True, "page": 3}],
                 "summary": "a much longer summary"}, tin=120, tout=30),
    ])
    chunks = [
        ChunkRecord(0, full[:len(full) // 2], 1, 2, 0, len(full) // 2),
        ChunkRecord(1, "[Page 2]\n" + full[len(full) // 2:], 2, 3, len(full) // 2, len(full)),
    ]
    out = extract_from_pages(None, ENTRY, prompt, {}, ai, chunks=chunks, full_text=full)

    assert len(out.fragments) == 2
    assert out.tokens_in == 220 and out.tokens_out == 50
    assert out.key_refs == [{"title": "R1"}]
    assert out.summary == "a much longer summary"
    assert out.failed_chunks == 0 and out.leaf_chunks == 2
    assert out.coverage.complete
    assert all(ef.verbatim_status == "confirmed" for ef in out.fragments)
    assert all(ef.char_start is not None for ef in out.fragments)
    assert full[out.fragments[0].char_start:out.fragments[0].char_end] == quote0
    assert len(out.prompt_hash) == 16
    assert len(ai._calls) == 2


def test_prompt_hash_is_stable_and_depends_on_template(tmp_path):
    p1 = tmp_path / "a.md"
    p1.write_text("one")
    p2 = tmp_path / "b.md"
    p2.write_text("two")
    ai = _ai([_result({"fragments": [{"text": "x"}]})] * 3)
    h1 = extract_from_pages(None, ENTRY, p1, {}, ai, text="x" * 50).prompt_hash
    h2 = extract_from_pages(None, ENTRY, p1, {}, ai, text="x" * 50).prompt_hash
    h3 = extract_from_pages(None, ENTRY, p2, {}, ai, text="x" * 50).prompt_hash
    assert h1 == h2 != h3


def test_pages_path_uses_patched_chunk_builder(tmp_path):
    prompt = tmp_path / "extract.md"
    prompt.write_text("p")
    pages = _pages(2)
    full = build_full_text(pages)
    one = [ChunkRecord(0, full, 1, 2, 0, len(full))]
    ai = _ai([_result({"fragments": [{"text": pages[1][:30], "verbatim": True}]})])
    with patch("klemma.literature.pdf.build_chunks_from_pages", return_value=one) as bc:
        out = extract_from_pages(pages, ENTRY, prompt, {}, ai, chunk_size=10, overlap=2)
    bc.assert_called_once()
    assert len(out.fragments) == 1
    assert out.full_text_length == len(full)


# ---------------------------------------------------------------------------
# Truncation → split → success; failure below min_chunk_chars
# ---------------------------------------------------------------------------


def test_truncated_chunk_is_split_and_children_succeed(tmp_path):
    prompt = tmp_path / "extract.md"
    prompt.write_text("p")
    pages = _pages(2, size=6000)
    full = build_full_text(pages)
    left_quote = full[20:60]
    right_quote = full[len(full) // 2 + 20: len(full) // 2 + 60]
    ai = _ai([
        _result({"fragments": [{"text": "cut off"}]}, finish="max_tokens"),  # parent → split
        _result({"fragments": [{"text": left_quote, "verbatim": True}]}),   # left child
        _result({"fragments": [{"text": right_quote, "verbatim": True}]}),  # right child
    ])
    chunks = [ChunkRecord(0, full, 1, 2, 0, len(full))]
    out = extract_from_pages(
        None, ENTRY, prompt, {}, ai, chunks=chunks, full_text=full, min_chunk_chars=1000,
    )
    statuses = [(c.index, c.status, c.parent_index) for c in out.chunks]
    assert statuses[0] == (0, "split", None)
    assert [s[1] for s in statuses[1:]] == ["ok", "ok"]
    assert all(s[2] == 0 for s in statuses[1:])
    assert out.failed_chunks == 0 and out.leaf_chunks == 2
    assert out.coverage.complete
    # The truncated parent's fragment must not survive
    assert {ef.fragment.text for ef in out.fragments} == {left_quote, right_quote}
    assert len(ai._calls) == 3


def test_unsplittable_failure_is_recorded_not_raised(tmp_path):
    prompt = tmp_path / "extract.md"
    prompt.write_text("p")
    full = build_full_text(_pages(1, size=800))
    ai = _ai([
        _result({}, text="{not json"),   # extract → parse failure
        _result({}, text="still {bad"),  # repair → still failure
    ])
    out = extract_from_pages(
        None, ENTRY, prompt, {}, ai,
        chunks=[ChunkRecord(0, full, 1, 1, 0, len(full))], full_text=full, min_chunk_chars=4000,
    )
    assert out.failed_chunks == 1
    assert out.chunks[0].status == "failed"
    assert out.chunks[0].error == "unparseable"
    assert not out.coverage.complete
    assert out.fragments == []
    assert len(ai._calls) == 2  # extract + one repair, no split below min size


def test_repair_retry_recovers_json(tmp_path):
    prompt = tmp_path / "extract.md"
    prompt.write_text("p")
    full = build_full_text(_pages(1))
    ai = _ai([
        _result({}, text="{oops"),
        _result({"fragments": [{"text": "ok fragment"}]}),
    ])
    out = extract_from_pages(
        None, ENTRY, prompt, {}, ai,
        chunks=[ChunkRecord(0, full, 1, 1, 0, len(full))], full_text=full,
    )
    assert out.failed_chunks == 0
    assert [ef.fragment.text for ef in out.fragments] == ["ok fragment"]
    assert ai._calls[1][0].startswith("You receive malformed JSON")


# ---------------------------------------------------------------------------
# Budget reservation before the call
# ---------------------------------------------------------------------------


def test_budget_reserved_before_call_blocks_remaining_chunks(tmp_path):
    prompt = tmp_path / "extract.md"
    prompt.write_text("p")
    full = build_full_text(_pages(2))
    half = len(full) // 2
    chunks = [
        ChunkRecord(0, full[:half], 1, 1, 0, half),
        ChunkRecord(1, "[Page 2]\n" + full[half:], 2, 2, half, len(full)),
    ]
    ai = _ai([_result({"fragments": [{"text": "first"}]}, tout=2048)])
    out = extract_from_pages(
        None, ENTRY, prompt, {}, ai, chunks=chunks, full_text=full,
        budget=Budget(max_output_tokens=3000),  # second call would reserve 2048 more
    )
    assert len(ai._calls) == 1
    assert out.chunks[1].status == "failed" and out.chunks[1].error == "budget"
    assert out.failed_chunks == 1
    assert not out.coverage.complete


def test_cost_estimate_uses_pricing_and_prefix_match():
    pricing = {"claude-x": {"input": 3.0, "output": 15.0}}
    assert estimate_cost_usd("anthropic/claude-x", 1_000_000, 1_000_000, pricing) == 18.0
    assert estimate_cost_usd("other", 10, 10, pricing) is None
    assert estimate_cost_usd("claude-x", 10, 10, None) is None


# ---------------------------------------------------------------------------
# Exhaustive mode refuses a backend without finish_reason
# ---------------------------------------------------------------------------


def test_exhaustive_refuses_unknown_finish_reason(tmp_path):
    prompt = tmp_path / "extract.md"
    prompt.write_text("p")
    full = build_full_text(_pages(1))
    ai = _ai([_result({"fragments": [{"text": "x"}]}, finish=None)])
    out = extract_from_pages(
        None, ENTRY, prompt, {}, ai,
        chunks=[ChunkRecord(0, full, 1, 1, 0, len(full))], full_text=full, mode="exhaustive",
    )
    assert out.error and "finish_reason" in out.error
    assert out.fragments == []


def test_standard_mode_accepts_unknown_finish_reason_with_valid_json(tmp_path):
    prompt = tmp_path / "extract.md"
    prompt.write_text("p")
    full = build_full_text(_pages(1))
    ai = _ai([_result({"fragments": [{"text": "x"}]}, finish=None)])
    out = extract_from_pages(
        None, ENTRY, prompt, {}, ai,
        chunks=[ChunkRecord(0, full, 1, 1, 0, len(full))], full_text=full,
    )
    assert out.error is None and len(out.fragments) == 1


# ---------------------------------------------------------------------------
# Verbatim validation + span location on the full text; validation cap
# ---------------------------------------------------------------------------


def test_fabricated_verbatim_is_downgraded_with_status(tmp_path):
    prompt = tmp_path / "extract.md"
    prompt.write_text("p")
    full = build_full_text(_pages(1))
    ai = _ai([_result({"fragments": [
        {"text": "Not anywhere in the document at all.", "verbatim": True},
        {"text": "plain paraphrase", "verbatim": False},
    ]})])
    out = extract_from_pages(
        None, ENTRY, prompt, {}, ai,
        chunks=[ChunkRecord(0, full, 1, 1, 0, len(full))], full_text=full,
    )
    by_text = {ef.fragment.text: ef for ef in out.fragments}
    fab = by_text["Not anywhere in the document at all."]
    assert fab.fragment.verbatim is False and fab.verbatim_status == "downgraded"
    assert by_text["plain paraphrase"].verbatim_status == "unclaimed"
    assert out.downgrade_stats.downgraded == 1


def test_validation_incomplete_flag_above_cap(tmp_path, monkeypatch):
    import klemma.skills.extract_engine as eng

    monkeypatch.setattr(eng, "VERBATIM_VALIDATION_CAP_LARGE", 500)
    prompt = tmp_path / "extract.md"
    prompt.write_text("p")
    full = build_full_text(_pages(2, size=600))
    assert len(full) > 500
    ai = _ai([_result({"fragments": [{"text": full[20:50], "verbatim": True}]})])
    out = extract_from_pages(
        None, ENTRY, prompt, {}, ai,
        chunks=[ChunkRecord(0, full, 1, 2, 0, len(full))], full_text=full,
    )
    assert out.validation_incomplete is True
    assert out.fragments[0].verbatim_status == "confirmed"


# ---------------------------------------------------------------------------
# Dedup by text and span (not by prefix)
# ---------------------------------------------------------------------------


def _ef(text, start=None, end=None):
    return ExtractedFragment(fragment=Fragment(text=text), char_start=start, char_end=end)


def test_dedup_keeps_distinct_claims_with_shared_prefix():
    prefix = "A" * 120
    a = _ef(prefix + " first distinct conclusion.", 0, 150)
    b = _ef(prefix + " second, unrelated result.", 300, 450)
    assert len(dedup_by_text_and_span([a, b])) == 2


def test_dedup_drops_exact_normalized_duplicates_without_spans():
    a = _ef("Sea ice  extent declines.")
    b = _ef("Sea ice extent declines.")
    assert len(dedup_by_text_and_span([a, b])) == 1


def test_dedup_fuzzy_only_when_spans_overlap():
    base = "The integrated ice edge error decomposes into extent and misplacement components"
    near = base + "."
    overlapping = [_ef(base, 100, 180), _ef(near, 100, 181)]
    disjoint = [_ef(base, 100, 180), _ef(near, 900, 981)]
    assert len(dedup_by_text_and_span(overlapping)) == 1
    assert len(dedup_by_text_and_span(disjoint)) == 2


# ---------------------------------------------------------------------------
# Parsing robustness
# ---------------------------------------------------------------------------


def test_invalid_citation_intent_does_not_drop_fragment(tmp_path):
    prompt = tmp_path / "extract.md"
    prompt.write_text("p")
    full = build_full_text(_pages(1))
    ai = _ai([_result({"fragments": [
        {"text": "kept", "citation_intent": "nonsense", "relevance": "9", "section": "2.4.1"},
        {"text": "   "},
    ]})])
    out = extract_from_pages(
        None, ENTRY, prompt, {}, ai,
        chunks=[ChunkRecord(0, full, 1, 1, 0, len(full))], full_text=full,
    )
    assert len(out.fragments) == 1
    f = out.fragments[0].fragment
    assert f.text == "kept" and f.citation_intent is None and f.relevance == 5


# ---------------------------------------------------------------------------
# Codex review on PR-A (#446)
# ---------------------------------------------------------------------------


def test_repair_call_is_budget_reserved(tmp_path):
    """P1: the JSON-repair call must not bypass the output-token reservation."""
    prompt = tmp_path / "extract.md"
    prompt.write_text("p")
    full = build_full_text(_pages(1))
    ai = _ai([_result({}, text="{bad", tout=2048)])  # repair would ask for 4096 more
    out = extract_from_pages(
        None, ENTRY, prompt, {}, ai,
        chunks=[ChunkRecord(0, full, 1, 1, 0, len(full))], full_text=full,
        budget=Budget(max_output_tokens=3000),
    )
    assert len(ai._calls) == 1
    assert out.chunks[0].status == "failed" and out.chunks[0].error == "budget"


def test_provider_error_finish_reason_fails_leaf_even_with_valid_json(tmp_path):
    """P2: content_filter/refusal is not a complete extraction of the chunk."""
    prompt = tmp_path / "extract.md"
    prompt.write_text("p")
    full = build_full_text(_pages(2, size=6000))
    ai = _ai([_result({"fragments": [{"text": "partial"}]}, finish="content_filter")])
    out = extract_from_pages(
        None, ENTRY, prompt, {}, ai,
        chunks=[ChunkRecord(0, full, 1, 2, 0, len(full))], full_text=full, min_chunk_chars=1000,
    )
    assert out.chunks[0].status == "failed" and out.chunks[0].error == "provider_error"
    assert out.fragments == [] and not out.coverage.complete
    assert len(ai._calls) == 1  # no split on provider errors


def test_fragments_beyond_validation_cap_are_checked_against_their_chunk(tmp_path, monkeypatch):
    """P2: a true quote late in a huge document must not be downgraded."""
    import klemma.skills.extract_engine as eng

    monkeypatch.setattr(eng, "VERBATIM_VALIDATION_CAP_LARGE", 400)
    prompt = tmp_path / "extract.md"
    prompt.write_text("p")
    pages = ["alpha " * 100, "omega distinct tail text " * 30]
    full = build_full_text(pages)
    half = full.index("[Page 2]")
    late_quote = "omega distinct tail text omega"
    assert full.index(late_quote) > 400
    chunks = [
        ChunkRecord(0, full[:half], 1, 1, 0, half),
        ChunkRecord(1, full[half:], 2, 2, half, len(full)),
    ]
    ai = _ai([
        _result({"fragments": []}),
        _result({"fragments": [
            {"text": late_quote, "verbatim": True},
            {"text": "fabricated late claim", "verbatim": True},
        ]}),
    ])
    out = extract_from_pages(None, ENTRY, prompt, {}, ai, chunks=chunks, full_text=full)
    assert out.validation_incomplete is True
    by_text = {ef.fragment.text: ef for ef in out.fragments}
    assert by_text[late_quote].verbatim_status == "confirmed"
    assert by_text[late_quote].char_start is not None and by_text[late_quote].char_start >= half
    assert by_text["fabricated late claim"].verbatim_status == "downgraded"


def test_span_prefers_originating_chunk_over_earlier_occurrence(tmp_path):
    """P2: a repeated quotation keeps the span (and page) of the chunk it came from."""
    prompt = tmp_path / "extract.md"
    prompt.write_text("p")
    repeated = "Sea ice edge position matters more than area."
    pages = ["intro. " + repeated + " filler " * 50, "later. " + repeated + " more filler " * 50]
    full = build_full_text(pages)
    half = full.index("[Page 2]")
    chunks = [
        ChunkRecord(0, full[:half], 1, 1, 0, half),
        ChunkRecord(1, full[half:], 2, 2, half, len(full)),
    ]
    ai = _ai([
        _result({"fragments": []}),
        _result({"fragments": [{"text": repeated, "verbatim": True}]}),  # no page given
    ])
    out = extract_from_pages(None, ENTRY, prompt, {}, ai, chunks=chunks, full_text=full)
    ef = out.fragments[0]
    assert ef.char_start >= half
    assert ef.fragment.page == 2


def test_parser_fallback_keeps_chapter_and_section(tmp_path):
    """P2: an invalid citation_intent must not discard routing metadata."""
    prompt = tmp_path / "extract.md"
    prompt.write_text("p")
    full = build_full_text(_pages(1))
    ai = _ai([_result({"fragments": [
        {"text": "kept", "citation_intent": "nonsense", "chapter": 2, "section": "2.4.1"},
    ]})])
    out = extract_from_pages(
        None, ENTRY, prompt, {}, ai,
        chunks=[ChunkRecord(0, full, 1, 1, 0, len(full))], full_text=full,
    )
    f = out.fragments[0].fragment
    assert f.chapter == 2 and f.section == "2.4.1" and f.citation_intent is None


@pytest.mark.parametrize(
    "kw",
    [
        {"chunk_size": 0},
        {"chunk_size": 1000, "chunk_overlap": 1000},
        {"chunk_overlap": -1},
        {"min_chunk_chars": 0},
        {"max_tokens_cap": 10},
        {"budget_max_output_tokens": -5},
        {"budget_max_cost_usd": -1.0},
    ],
)
def test_aiconfig_rejects_bad_chunk_geometry(kw):
    """P1: a config typo must not become runaway extraction."""
    from klemma.config import AIConfig

    with pytest.raises(ValueError):
        AIConfig(backend="litellm", model="m", **kw)


def test_aiconfig_defaults_are_valid():
    from klemma.config import AIConfig

    cfg = AIConfig(backend="litellm", model="m")
    assert 0 <= cfg.chunk_overlap < cfg.chunk_size and cfg.min_chunk_chars > 0


def test_force_reprocess_replaces_only_on_complete_extraction(tmp_path):
    """P1: a partial --force result merges onto the old corpus instead of replacing it."""
    from unittest.mock import patch

    from klemma.skills import extractor as ex_mod
    from klemma.skills.extract_engine import ChunkOutcome, CoverageReport, ExtractionOutcome

    def _outcome(failed: int, covered: int):
        return ExtractionOutcome(
            fragments=[ExtractedFragment(fragment=Fragment(text="new fragment"))],
            key_refs=[], summary="", notes={},
            chunks=[ChunkOutcome(0, 0, 100, "ok")],
            coverage=CoverageReport(total_chars=100, covered_chars=covered),
            prompt_hash="h", model="m", failed_chunks=failed, leaf_chunks=2,
        )

    state = MagicMock()
    state.save_fragments.return_value = 1
    cfg = MagicMock()
    cfg.ai.language = "ru"
    cfg.ai.task_classes = {}
    prompt = tmp_path / ".klemma" / "prompts"
    prompt.mkdir(parents=True)
    (prompt / "extract.md").write_text("p")

    with patch("klemma.skills.extract_engine.extract_from_pages", return_value=_outcome(1, 50)):
        ex_mod.extract_fragments(ENTRY, "text", cfg, state, MagicMock(),
                                 klemma_home=tmp_path / ".klemma", replace_existing=True)
    state.delete_fragments.assert_not_called()
    state.save_fragments.assert_called_once()

    state.reset_mock()
    with patch("klemma.skills.extract_engine.extract_from_pages", return_value=_outcome(0, 100)):
        ex_mod.extract_fragments(ENTRY, "text", cfg, state, MagicMock(),
                                 klemma_home=tmp_path / ".klemma", replace_existing=True)
    state.delete_fragments.assert_called_once_with("paper2025")


# ---------------------------------------------------------------------------
# Codex review round 2 on PR-A (#446)
# ---------------------------------------------------------------------------


def test_truncated_malformed_response_splits_without_repair_call(tmp_path):
    """P1: max_tokens + malformed JSON → split directly, no paid repair, budget intact."""
    prompt = tmp_path / "extract.md"
    prompt.write_text("p")
    full = build_full_text(_pages(2, size=6000))
    left, right = full[20:60], full[len(full) // 2 + 20: len(full) // 2 + 60]
    ai = _ai([
        _result({}, text='{"fragments": [{"text": "cut', finish="max_tokens", tout=2048),
        _result({"fragments": [{"text": left, "verbatim": True}]}, tout=500),
        _result({"fragments": [{"text": right, "verbatim": True}]}, tout=500),
    ])
    out = extract_from_pages(
        None, ENTRY, prompt, {}, ai,
        chunks=[ChunkRecord(0, full, 1, 2, 0, len(full))], full_text=full,
        min_chunk_chars=1000, budget=Budget(max_output_tokens=2048 + 2 * 2048 + 10),
    )
    assert len(ai._calls) == 3
    assert all("malformed JSON" not in c[0] for c in ai._calls)
    assert out.failed_chunks == 0 and out.coverage.complete


def test_repair_truncated_result_is_split_not_accepted(tmp_path):
    """P2: a repair answer cut by max_tokens is partial → split, not accepted."""
    prompt = tmp_path / "extract.md"
    prompt.write_text("p")
    full = build_full_text(_pages(2, size=6000))
    ai = _ai([
        _result({}, text="{bad"),
        _result({"fragments": [{"text": "partial"}]}, finish="max_tokens"),  # repair, truncated
        _result({"fragments": [{"text": full[20:50], "verbatim": True}]}),
        _result({"fragments": [{"text": full[-50:-20], "verbatim": True}]}),
    ])
    out = extract_from_pages(
        None, ENTRY, prompt, {}, ai,
        chunks=[ChunkRecord(0, full, 1, 2, 0, len(full))], full_text=full, min_chunk_chars=1000,
    )
    assert out.chunks[0].status == "split"
    assert "partial" not in {ef.fragment.text for ef in out.fragments}
    assert out.failed_chunks == 0


def test_repair_refused_by_provider_fails_leaf(tmp_path):
    prompt = tmp_path / "extract.md"
    prompt.write_text("p")
    full = build_full_text(_pages(1))
    ai = _ai([
        _result({}, text="{bad"),
        _result({"fragments": [{"text": "x"}]}, finish="content_filter"),
    ])
    out = extract_from_pages(
        None, ENTRY, prompt, {}, ai,
        chunks=[ChunkRecord(0, full, 1, 1, 0, len(full))], full_text=full,
    )
    assert out.chunks[0].status == "failed" and out.chunks[0].error == "provider_error"
    assert out.fragments == []


def test_max_tokens_cap_below_2048_is_honoured(tmp_path):
    prompt = tmp_path / "extract.md"
    prompt.write_text("p")
    full = build_full_text(_pages(3))
    ai = _ai([_result({"fragments": [{"text": "x"}]})])
    extract_from_pages(
        None, ENTRY, prompt, {}, ai,
        chunks=[ChunkRecord(0, full, 1, 3, 0, len(full))], full_text=full, max_tokens_cap=512,
    )
    assert ai._calls[0][2]["max_tokens"] == 512


def test_fallback_full_text_is_reconstructed_from_offsets(tmp_path):
    """P2: overlapping chunks without full_text must not inflate total_chars or shift spans."""
    from klemma.literature.pdf import build_chunks_from_pages

    prompt = tmp_path / "extract.md"
    prompt.write_text("p")
    pages = _pages(3, size=3000)
    full = build_full_text(pages)
    chunks = build_chunks_from_pages(pages, chunk_size=2500, overlap=400)
    assert len(chunks) >= 3
    quote = full[len(full) - 200: len(full) - 160]
    script = [_result({"fragments": []}) for _ in chunks[:-1]]
    script.append(_result({"fragments": [{"text": quote, "verbatim": True}]}))
    ai = _ai(script)
    out = extract_from_pages(None, ENTRY, prompt, {}, ai, chunks=chunks)  # no full_text
    assert out.full_text_length == len(full)
    assert out.coverage.complete
    ef = out.fragments[0]
    assert ef.verbatim_status == "confirmed"
    assert full[ef.char_start:ef.char_end] == quote


def test_budget_charges_reservation_when_usage_unreported(tmp_path):
    """P1: a backend without token counts (Claude CLI) still consumes the budget."""
    prompt = tmp_path / "extract.md"
    prompt.write_text("p")
    full = build_full_text(_pages(3))
    third = len(full) // 3
    chunks = [
        ChunkRecord(0, full[:third], 1, 1, 0, third),
        ChunkRecord(1, "[Page 2]\n" + full[third:2 * third], 2, 2, third, 2 * third),
        ChunkRecord(2, "[Page 3]\n" + full[2 * third:], 3, 3, 2 * third, len(full)),
    ]
    ai = _ai([_result({"fragments": [{"text": "a"}]}, tin=0, tout=0, finish=None)] * 3)
    out = extract_from_pages(
        None, ENTRY, prompt, {}, ai, chunks=chunks, full_text=full,
        budget=Budget(max_output_tokens=2048 * 2),  # room for two reservations only
    )
    assert len(ai._calls) == 2
    assert out.chunks[2].error == "budget"
    assert out.tokens_out == 2 * 2048


def test_extract_from_citekey_passes_full_pages(tmp_path):
    """P2: the research pre-extraction path must not send a truncated single chunk."""
    from klemma.skills import extractor as ex_mod

    pdf_extractor = MagicMock()
    pdf_extractor.find_pdf.return_value = tmp_path / "x.pdf"
    pages = ["page one text " * 50, "page two text " * 50]
    pdf_extractor.extract_pages.return_value = pages
    pdf_extractor.format_for_ai.return_value = "truncated"
    state = MagicMock()
    state.get_source.return_value = {"pdf_path": None}
    cfg = MagicMock()
    cfg.processing.min_pdf_length = 1
    with patch("klemma.skills.extractor.extract_fragments") as mock_extract:
        ex_mod.extract_from_citekey("k", cfg, state, MagicMock(), pdf_extractor, [tmp_path])
    assert mock_extract.call_args.kwargs["pages"] == pages


def test_fragments_from_rows_roundtrip():
    from klemma.skills.extractor import fragments_from_rows

    rows = [
        {"fragment_text": "a", "fragment_type": "quote", "chapter": 2, "section": "2.4",
         "relevance_score": 9, "usage_hint": "h", "page_number": 3, "verbatim": 1},
        {"fragment_text": "  ", "fragment_type": "quote"},
        {"fragment_text": "b", "relevance_score": None, "page_number": "x"},
    ]
    out = fragments_from_rows(rows)
    assert [f.text for f in out] == ["a", "b"]
    assert out[0].relevance == 5 and out[0].page == 3 and out[0].section == "2.4"
    assert out[1].relevance == 3 and out[1].page is None


# ---------------------------------------------------------------------------
# Codex review round 3 on PR-A (#446)
# ---------------------------------------------------------------------------


def test_payload_without_fragments_list_is_not_a_successful_leaf(tmp_path):
    """P1: {"summary": ...} is valid JSON but not an extraction → repair/split, not ok."""
    prompt = tmp_path / "extract.md"
    prompt.write_text("p")
    full = build_full_text(_pages(1))
    ai = _ai([
        _result({"summary": "no fragments key"}),
        _result({"fragments": "not a list"}),  # repair also schema-invalid
    ])
    out = extract_from_pages(
        None, ENTRY, prompt, {}, ai,
        chunks=[ChunkRecord(0, full, 1, 1, 0, len(full))], full_text=full, min_chunk_chars=4000,
    )
    assert out.chunks[0].status == "failed" and out.chunks[0].error == "unparseable"
    assert not out.coverage.complete
    assert len(ai._calls) == 2


def test_recursive_split_children_overlap_at_sentence_boundary(tmp_path):
    """P2: a claim straddling the midpoint must be complete in one child."""
    prompt = tmp_path / "extract.md"
    prompt.write_text("p")
    sentence = "Ice edge misplacement dominates the total error in autumn. "
    full = build_full_text([sentence * 120])
    mid = len(full) // 2
    ai = _ai([
        _result({}, text="{cut", finish="max_tokens"),
        _result({"fragments": []}),
        _result({"fragments": []}),
    ])
    out = extract_from_pages(
        None, ENTRY, prompt, {}, ai,
        chunks=[ChunkRecord(0, full, 1, 1, 0, len(full))], full_text=full,
        min_chunk_chars=1000, overlap=400,
    )
    left, right = out.chunks[1], out.chunks[2]
    assert left.char_end > right.char_start  # bounded overlap
    assert left.char_end - right.char_start <= 2 * 400
    cut = (left.char_end + right.char_start) // 2  # snapped midpoint
    assert full[cut - 2:cut].rstrip().endswith(".")
    assert abs(cut - mid) <= 500
    assert out.coverage.complete


def test_cap_straddling_chunk_quote_validated_against_chunk(tmp_path, monkeypatch):
    """P2: a chunk starting before the cap but extending past it keeps genuine quotes."""
    import klemma.skills.extract_engine as eng

    monkeypatch.setattr(eng, "VERBATIM_VALIDATION_CAP_LARGE", 300)
    prompt = tmp_path / "extract.md"
    prompt.write_text("p")
    full = build_full_text(["alpha " * 60 + "omega straddling quote here " * 20])
    quote = "omega straddling quote here omega"
    assert 0 < 300 < full.index(quote)
    ai = _ai([_result({"fragments": [
        {"text": quote, "verbatim": True},
        {"text": "nowhere fabricated", "verbatim": True},
    ]})])
    out = extract_from_pages(
        None, ENTRY, prompt, {}, ai,
        chunks=[ChunkRecord(0, full, 1, 1, 0, len(full))], full_text=full,
    )
    by_text = {ef.fragment.text: ef for ef in out.fragments}
    assert by_text[quote].verbatim_status == "confirmed"
    assert by_text[quote].char_start == full.index(quote)
    assert by_text["nowhere fabricated"].verbatim_status == "downgraded"
    assert out.validation_incomplete is True


def test_provider_failure_is_not_reported_as_missing_finish_reason(tmp_path):
    """Pilot 04.09: an auth/credit error must not masquerade as 'no finish_reason'."""
    prompt = tmp_path / "extract.md"
    prompt.write_text("p")
    full = build_full_text(_pages(1))
    ai = _ai([AICallResult(text=None, error="auth: credit balance too low", model="m")])
    out = extract_from_pages(
        None, ENTRY, prompt, {}, ai,
        chunks=[ChunkRecord(0, full, 1, 1, 0, len(full))], full_text=full, mode="exhaustive",
    )
    assert out.error and "provider error" in out.error and "credit" in out.error
    assert "finish_reason" not in out.error
    assert out.chunks[0].error == "auth: credit balance too low"
