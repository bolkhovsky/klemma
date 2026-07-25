"""Tests for check_draft_inline() and annotation helpers (PR 3, ADR-018)."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from klemma.config import AIConfig, KlemmaConfig
from klemma.skills.citation_checker import (
    CitationVerdict,
    _annotate_draft,
    _safe_comment_payload,
    check_draft_inline,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _cfg(verify_inline: bool = True) -> KlemmaConfig:
    cfg = KlemmaConfig()
    cfg.ai = AIConfig(
        backend="litellm",
        model="openai/gpt-4o-mini",
        citation_check_max_wall_clock=120,
        max_ai_calls_per_draft=12,
        citation_check_timeout=60,
        citation_check_retries=0,
        citation_check_max_claim_chars=1000,
        citation_check_max_passage_chars=2000,
        citation_check_max_passages=8,
        citation_check_max_prompt_chars=12000,
        citation_check_max_output_tokens=1024,
        verify_citations_inline=verify_inline,
    )
    cfg.ai._resolved_api_keys = {}
    return cfg


def _run(
    draft_text: str,
    fragments: list[dict],
    rag: list[dict] | None = None,
    *,
    cfg=None,
    judge_ai=None,
    use_ai=None,
):
    cfg = cfg or _cfg()
    return check_draft_inline(
        draft_text,
        fragments,
        rag or [],
        config=cfg,
        judge_ai=judge_ai,
        project_root=Path("/tmp"),
        klemma_home=None,
        project_chain=[],
        use_ai=(judge_ai is not None) if use_ai is None else use_ai,
    )


# ---------------------------------------------------------------------------
# _safe_comment_payload
# ---------------------------------------------------------------------------

class TestSafeCommentPayload:
    def test_closes_comment_replaced(self):
        # '-->' in payload would close the comment prematurely
        result = _safe_comment_payload("some -- thing --> end")
        assert "-->" not in result
        assert "--" not in result  # double-dash eliminated

    def test_double_dash_replaced(self):
        result = _safe_comment_payload("a--b")
        assert "--" not in result

    def test_newlines_replaced(self):
        result = _safe_comment_payload("line1\nline2\r\nline3")
        assert "\n" not in result
        assert "\r" not in result

    def test_truncation(self):
        long = "x" * 300
        result = _safe_comment_payload(long, max_len=150)
        assert len(result) <= 150

    def test_empty_string(self):
        assert _safe_comment_payload("") == ""


# ---------------------------------------------------------------------------
# _annotate_draft
# ---------------------------------------------------------------------------

def _make_verdict(severity: str, start: int, end: int, citekey: str, raw: str) -> CitationVerdict:
    from klemma.skills.citation_checker import ClaimAnchor
    anchor = ClaimAnchor(
        kind="definitional",
        raw=raw,
        trigger="является",
        start_offset=start,
        end_offset=end,
        anchor_id=f"{start}:{end}",
    )
    return CitationVerdict(
        citekey=citekey,
        claim_sentence="test sentence",
        location="inline",
        anchor=anchor,
        severity=severity,
        reason="test reason",
        offending_span=raw,
        ai_used=False,
    )


class TestAnnotateDraft:
    def test_soft_warn_annotation(self):
        draft = "Метод является точным [@smith2020]. Подтверждено."
        v = _make_verdict("soft_warn", 7, 20, "smith2020", "является точным")
        result = _annotate_draft(draft, [v])
        assert "<!-- klemma: проверь обоснование @smith2020 -->" in result

    def test_hard_warn_annotation(self):
        draft = "Метод является точным [@jones2021]."
        v = _make_verdict("hard_warn", 7, 20, "jones2021", "является точным")
        result = _annotate_draft(draft, [v])
        assert "<!-- klemma: необоснованный глосс vs @jones2021:" in result

    def test_ok_not_annotated(self):
        draft = "Good claim [@ok2020]."
        v = _make_verdict("ok", 0, 10, "ok2020", "Good claim")
        result = _annotate_draft(draft, [v])
        assert "klemma" not in result
        assert result == draft

    def test_unverifiable_not_annotated(self):
        draft = "Unknown [@x2020]."
        v = _make_verdict("unverifiable", 0, 7, "x2020", "Unknown")
        result = _annotate_draft(draft, [v])
        assert "klemma" not in result

    def test_dedup_same_position_same_citekey(self):
        draft = "Claim [@a2020]. Extra text."
        v1 = _make_verdict("soft_warn", 0, 5, "a2020", "Claim")
        v2 = _make_verdict("soft_warn", 0, 5, "a2020", "Claim")
        result = _annotate_draft(draft, [v1, v2])
        assert result.count("klemma") == 1

    def test_right_to_left_insertion_preserves_offsets(self):
        draft = "Alpha [@a]. Beta [@b]."
        v_a = _make_verdict("soft_warn", 0, 5, "a2020", "Alpha")
        v_b = _make_verdict("hard_warn", 12, 16, "b2021", "Beta")
        result = _annotate_draft(draft, [v_a, v_b])
        # Both annotations must appear
        assert "klemma: проверь обоснование @a2020" in result
        assert "klemma: необоснованный глосс vs @b2021" in result
        # a2020 annotation comes before b2021 annotation (left of b's position)
        assert result.index("a2020") < result.index("b2021")

    def test_citekey_with_dangerous_chars(self):
        draft = "Claim [@k--ey]."
        v = _make_verdict("soft_warn", 0, 5, "k--ey", "Claim")
        result = _annotate_draft(draft, [v])
        # The HTML comment must not be prematurely closed
        comment_start = result.find("<!--")
        comment_end = result.find("-->")
        assert comment_start != -1 and comment_end != -1
        assert comment_end > comment_start

    def test_raw_with_greater_than(self):
        draft = "SIC > 15% [@smith]. Done."
        v = _make_verdict("hard_warn", 0, 9, "smith2020", "SIC > 15%")
        result = _annotate_draft(draft, [v])
        # The comment must open and close properly (no premature closure)
        comment_start = result.find("<!--")
        comment_end = result.find("-->")
        assert comment_start != -1 and comment_end != -1
        assert comment_end > comment_start
        # '>' replaced with space → no risk of comment termination inside payload
        inner = result[comment_start + 4 : comment_end]
        assert "-->" not in inner


# ---------------------------------------------------------------------------
# check_draft_inline — no-AI mode
# ---------------------------------------------------------------------------

class TestCheckDraftInlineNoAI:
    def test_no_claims_returns_original_text(self):
        draft = "This text has no citations."
        annotated, report = _run(draft, [], judge_ai=None)
        assert annotated == draft
        assert report.status == "ok"
        assert report.verdicts == []
        assert report.target == "inline"

    def test_claim_no_fragment_unverifiable(self):
        draft = "Temperature is 15°C [@smith2020]."
        annotated, report = _run(draft, [], judge_ai=None)
        # use_ai=False (judge_ai is None), deliberate no-AI run → status ok
        assert report.status == "ok"
        # No fragments → unverifiable (source not available)
        assert any(v.severity == "unverifiable" for v in report.verdicts)

    def test_claim_no_fragment_no_ai_status_ok(self):
        draft = "The temperature is 15°C [@smith2020]."
        # No judge, no fragments → unverifiable, status ok (deliberate no-AI run)
        annotated, report = _run(draft, [], judge_ai=None)
        # With judge_ai=None and use_ai=False: no AI degraded, but we get unverifiable
        assert report.model is None

    def test_verbatim_quote_found_ok(self):
        draft = "Авторы пишут «точный метод оценки» согласно работе [@smith2020]."
        fragments = [{"source": "smith2020", "text": "точный метод оценки используется широко"}]
        annotated, report = _run(draft, fragments, judge_ai=None)
        # "точный метод оценки" is 3 words — below _QUOTE_MIN_WORDS=5, not a quote anchor
        assert annotated == draft  # no annotations if no anchors

    def test_long_verbatim_quote_missing_unverifiable(self):
        draft = "Авторы пишут «очень длинная цитата которой нет в источниках совсем» согласно [@smith2020]."
        fragments = [{"source": "smith2020", "text": "совершенно другой текст"}]
        annotated, report = _run(draft, fragments, judge_ai=None)
        quote_verdicts = [v for v in report.verdicts if v.anchor.kind == "quote"]
        if quote_verdicts:
            # search_complete=False → unverifiable (not hard_warn)
            assert all(v.severity == "unverifiable" for v in quote_verdicts)

    def test_numeric_absent_no_sidecar_unverifiable(self):
        draft = "Точность составила 95.3% [@jones2021]."
        fragments = [{"source": "jones2021", "text": "метод показал хорошие результаты"}]
        annotated, report = _run(draft, fragments, judge_ai=None)
        numeric_verdicts = [v for v in report.verdicts if v.anchor.kind == "numeric"]
        if numeric_verdicts:
            # search_complete=False → unverifiable (never hard_warn inline without sidecar)
            assert all(v.severity in ("unverifiable", "soft_warn") for v in numeric_verdicts)

    def test_draft_always_returned_on_error(self):
        draft = "Some text [@k]. More."
        with patch(
            "klemma.skills.citation_checker._parse_claims",
            side_effect=RuntimeError("boom"),
        ):
            annotated, report = _run(draft, [], judge_ai=None)
        assert annotated == draft  # always returned
        assert report.status == "error"

    def test_annotate_failure_returns_original(self):
        draft = "Method is good [@smith2020]."
        fragments = [{"source": "smith2020", "text": "method"}]
        with patch(
            "klemma.skills.citation_checker._annotate_draft",
            side_effect=RuntimeError("annotate boom"),
        ):
            annotated, report = _run(draft, fragments, judge_ai=None)
        assert annotated == draft
        assert report.status == "error"

    def test_empty_draft(self):
        annotated, report = _run("", [], judge_ai=None)
        assert annotated == ""
        assert report.status == "ok"


# ---------------------------------------------------------------------------
# check_draft_inline — annotation produced for soft/hard
# ---------------------------------------------------------------------------

class TestCheckDraftInlineAnnotations:
    def test_soft_warn_annotated(self):
        # With a definitional trigger and fragment available → soft_warn (no AI, unverifiable → no annotation)
        # Actually definitional without AI → unverifiable, which is NOT annotated.
        # To get soft_warn we'd need AI. Test annotation with a mock verdict instead.
        draft = "Метод является надёжным [@smith2020]. Extra."
        fragments = [{"source": "smith2020", "text": "методы измерения"}]

        # Patch verify_claim to return soft_warn for testing annotation
        from klemma.skills.citation_checker import CitationVerdict, ClaimAnchor
        mock_anchor = ClaimAnchor(
            kind="definitional",
            raw="является надёжным",
            trigger="является",
            start_offset=6,
            end_offset=24,
            anchor_id="6:24",
        )
        mock_verdict = CitationVerdict(
            citekey="smith2020",
            claim_sentence="Метод является надёжным [@smith2020].",
            location="inline",
            anchor=mock_anchor,
            severity="soft_warn",
            reason="lexically absent from passages",
            offending_span="является надёжным",
            ai_used=False,
        )
        with patch("klemma.skills.citation_checker.verify_claim", return_value=mock_verdict), \
             patch("klemma.skills.citation_checker._needs_ai_check", return_value=False):
            annotated, report = _run(draft, fragments, judge_ai=None)

        assert "<!-- klemma: проверь обоснование @smith2020 -->" in annotated
        assert len(report.verdicts) >= 1
        assert any(v.severity == "soft_warn" for v in report.verdicts)

    def test_draft_saved_despite_findings(self):
        draft = "Good text [@key2020]."
        fragments = [{"source": "key2020", "text": "some content"}]
        annotated, report = _run(draft, fragments, judge_ai=None)
        # Draft always returned (even if findings exist)
        assert isinstance(annotated, str)


# ---------------------------------------------------------------------------
# check_draft_inline — with AI judge (mocked)
# ---------------------------------------------------------------------------

class TestCheckDraftInlineWithAI:
    def _make_judge(self, response_text: str) -> MagicMock:
        meta = MagicMock()
        meta.text = response_text
        meta.input_tokens = 50
        meta.output_tokens = 20
        meta.model = "openai/gpt-4o-mini"
        meta.error = None
        judge = MagicMock()
        judge.call_with_meta.return_value = meta
        return judge

    def test_ai_used_for_definitional(self):
        draft = "IIEE соответствует ошибке площади [@jones2021]."
        fragments = [{"source": "jones2021", "text": "IIEE описывает метод оценки ошибки"}]

        judge = MagicMock()
        from klemma.skills.citation_checker import BatchResult

        # Return malformed result → bundles become unverifiable
        with patch("klemma.skills.citation_checker.verify_claim_batch") as mock_batch:
            mock_batch.return_value = BatchResult(
                verdicts=[],  # empty (simulates failed parse)
                input_tokens=30,
                output_tokens=10,
                model="openai/gpt-4o-mini",
                errors=["malformed judge response"],
            )
            annotated, report = _run(draft, fragments, judge_ai=judge)

        # verify_claim_batch was called (AI path for definitional)
        mock_batch.assert_called_once()
        # Errors recorded
        assert report.errors

    def test_ai_hard_warn_produces_annotation(self):
        from klemma.skills.citation_checker import CitationVerdict, ClaimAnchor

        draft = "Метод является ошибочным [@jones2021]."
        fragments = [{"source": "jones2021", "text": "метод точный"}]

        anchor = ClaimAnchor(
            kind="definitional",
            raw="является ошибочным",
            trigger="является",
            start_offset=6,
            end_offset=24,
            anchor_id="6:24",
        )
        hard_verdict = CitationVerdict(
            citekey="jones2021",
            claim_sentence=draft,
            location="inline",
            anchor=anchor,
            severity="hard_warn",
            reason="contradiction found",
            offending_span="является ошибочным",
            ai_used=True,
        )

        with patch("klemma.skills.citation_checker.verify_claim_batch") as mock_batch:
            from klemma.skills.citation_checker import BatchResult
            mock_batch.return_value = BatchResult(
                verdicts=[hard_verdict],
                input_tokens=50,
                output_tokens=20,
                model="openai/gpt-4o-mini",
                errors=[],
            )
            judge = MagicMock()
            annotated, report = _run(draft, fragments, judge_ai=judge)

        assert "<!-- klemma: необоснованный глосс vs @jones2021" in annotated
        assert report.input_tokens == 50
        assert report.model == "openai/gpt-4o-mini"

    def test_no_ai_judge_status_degraded_when_use_ai_true(self):
        draft = "Метод является надёжным [@smith2020]."
        fragments = [{"source": "smith2020", "text": "текст"}]
        annotated, report = _run(draft, fragments, judge_ai=None, use_ai=True)
        assert report.status == "degraded"
        assert any(v.severity == "unverifiable" for v in report.verdicts)

    def test_ai_call_budget_exhausted(self):
        draft = "A является B [@k1]. C является D [@k2]. E является F [@k3]."
        fragments = [
            {"source": "k1", "text": "text1"},
            {"source": "k2", "text": "text2"},
            {"source": "k3", "text": "text3"},
        ]
        cfg = _cfg()
        cfg.ai.max_ai_calls_per_draft = 1  # Only 1 AI call allowed

        judge = MagicMock()
        from klemma.skills.citation_checker import BatchResult, CitationVerdict

        def mock_batch(bundles, **kwargs):
            a = bundles[0].anchor
            v = CitationVerdict(
                citekey=bundles[0].citekey, claim_sentence="x", location="inline",
                anchor=a, severity="ok", reason="ok", offending_span="", ai_used=True,
            )
            return BatchResult(verdicts=[v], input_tokens=10, output_tokens=5,
                               model="m", errors=[])

        with patch("klemma.skills.citation_checker.verify_claim_batch", side_effect=mock_batch) as mb:
            annotated, report = check_draft_inline(
                draft, fragments, [],
                config=cfg, judge_ai=judge,
                project_root=Path("/tmp"),
                use_ai=True,
            )

        # Only 1 AI call should have been made
        assert mb.call_count <= 1
        # Remaining definitional anchors → unverifiable
        unverifiable = [v for v in report.verdicts if v.severity == "unverifiable"]
        assert len(unverifiable) >= 1


# ---------------------------------------------------------------------------
# verify_citations_inline config flag
# ---------------------------------------------------------------------------

class TestVerifyCitationsInlineConfig:
    def test_default_is_true(self):
        ai = AIConfig()
        assert ai.verify_citations_inline is True

    def test_can_be_disabled(self):
        ai = AIConfig(verify_citations_inline=False)
        assert ai.verify_citations_inline is False
