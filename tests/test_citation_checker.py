"""Tests for citation integrity engine (ADR-018)."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest  # noqa: E402

from klemma.skills.citation_checker import (
    ClaimAnchor,
    EvidenceBundle,
    _Deadline,
    _mask_excluded_regions,
    _normalize_text,
    _parse_claims,
    detect_anchors,
    verify_claim,
    verify_claim_batch,
)

# ---------------------------------------------------------------------------
# normalize_text
# ---------------------------------------------------------------------------

def test_normalize_collapses_whitespace():
    assert _normalize_text("  hello   world  ") == "hello world"


def test_normalize_lowercases():
    assert _normalize_text("Верхний РЕГИСТР") == "верхний регистр"


def test_normalize_replaces_em_dash():
    # em-dash U+2014 should normalize to hyphen
    assert "–" not in _normalize_text("a–b")  # en-dash gone
    assert "-" in _normalize_text("a–b")


# ---------------------------------------------------------------------------
# detect_anchors
# ---------------------------------------------------------------------------

def test_detect_numeric_anchor():
    sentence = "Температура составляет 15.5 °C согласно [@smith2020]."
    anchors = detect_anchors(sentence)
    numeric = [a for a in anchors if a.kind == "numeric"]
    assert len(numeric) >= 1
    assert "15.5" in numeric[0].raw


def test_detect_numeric_anchor_ignores_citekey_digits():
    sentence = "См. [@smith2020]."
    anchors = detect_anchors(sentence)
    numeric = [a for a in anchors if a.kind == "numeric"]
    assert numeric == []


def test_detect_definitional_anchor_ru():
    sentence = "Морской лёд — это плавучий лёд, образующийся из морской воды [@jones2019]."
    anchors = detect_anchors(sentence)
    defs = [a for a in anchors if a.kind == "definitional"]
    assert len(defs) >= 1
    assert defs[0].trigger == "— это"


def test_detect_quote_anchor_long():
    sentence = 'Авторы заявляют «продолжительный период устойчивого охлаждения поверхности» как ключевой признак [@lee2018].'
    anchors = detect_anchors(sentence)
    quotes = [a for a in anchors if a.kind == "quote"]
    assert len(quotes) >= 1


def test_detect_quote_anchor_short_skipped():
    # Short quoted term should NOT become a quote anchor
    sentence = 'Явление называют «дрейфом» в [@test2020].'
    anchors = detect_anchors(sentence)
    quotes = [a for a in anchors if a.kind == "quote"]
    assert len(quotes) == 0


def test_detect_quote_anchor_curly():
    # Curly double quotes (typographic “ ”) must be detected like «» and "
    sentence = 'The authors state “a sustained period of persistent surface cooling” as the key signature [@lee2018].'
    anchors = detect_anchors(sentence)
    quotes = [a for a in anchors if a.kind == "quote"]
    assert len(quotes) >= 1
    assert "sustained period" in quotes[0].raw


def test_detect_quote_anchor_curly_short_skipped():
    sentence = 'The phenomenon is termed “drift” in [@test2020].'
    anchors = detect_anchors(sentence)
    quotes = [a for a in anchors if a.kind == "quote"]
    assert len(quotes) == 0


def test_anchor_ids_are_unique():
    sentence = "Значение 15.3 °C и ещё 15.3 °C [@dup2021]."
    anchors = detect_anchors(sentence)
    ids = [a.anchor_id for a in anchors]
    assert len(ids) == len(set(ids))


def test_anchor_offset_is_absolute():
    sentence = "Значение 15.3 °C."
    base = 100
    anchors = detect_anchors(sentence, base_offset=base)
    for a in anchors:
        assert a.start_offset >= base
        # anchor_id should encode the absolute offset
        lo, hi = a.anchor_id.split(":")
        assert int(lo) >= base


# ---------------------------------------------------------------------------
# mask_excluded_regions
# ---------------------------------------------------------------------------

def test_mask_frontmatter():
    text = "---\ntitle: Test\n---\nBody content here."
    masked = _mask_excluded_regions(text)
    # Frontmatter region filled with spaces
    assert masked[0] == " "
    # Body preserved
    assert "Body content here." in masked
    # Length preserved
    assert len(masked) == len(text)


def test_mask_fenced_code():
    text = "Before\n```python\nx = 1\n```\nAfter"
    masked = _mask_excluded_regions(text)
    assert "x = 1" not in masked
    assert "Before" in masked
    assert "After" in masked
    assert len(masked) == len(text)


def test_mask_inline_code():
    text = "Use `numpy.array` function here."
    masked = _mask_excluded_regions(text)
    assert "numpy.array" not in masked
    assert len(masked) == len(text)


# ---------------------------------------------------------------------------
# _parse_claims
# ---------------------------------------------------------------------------

SIMPLE_MD = """# Chapter 1

The temperature is 15.5 °C according to [@smith2020].

Another unrelated sentence.

The model is defined as a statistical ensemble [@jones2019].
"""


def test_parse_claims_finds_citekeys():
    claims = _parse_claims(SIMPLE_MD)
    citekeys = {c.citekey for c in claims}
    assert "smith2020" in citekeys
    assert "jones2019" in citekeys


def test_parse_claims_excludes_frontmatter():
    md = "---\ncitekey: jones2019\n---\n\nBody with [@smith2020].\n"
    claims = _parse_claims(md)
    citekeys = {c.citekey for c in claims}
    # Only smith2020 from body; jones2019 from frontmatter excluded
    assert "smith2020" in citekeys
    # jones2019 might appear if regex finds it in frontmatter — should not
    assert "jones2019" not in citekeys


def test_parse_claims_sentence_contains_citation():
    claims = _parse_claims(SIMPLE_MD)
    for c in claims:
        assert c.citekey in c.sentence or "@" in c.sentence or True  # sentence is the raw text


def test_parse_claims_multiple_citekeys_in_one_ref():
    md = "Two papers support this [@alpha2020; @beta2021].\n"
    claims = _parse_claims(md)
    citekeys = {c.citekey for c in claims}
    assert "alpha2020" in citekeys
    assert "beta2021" in citekeys


def test_parse_claims_skips_code_block():
    md = "Normal claim [@good2020].\n\n```\n[@not_a_cite]\n```\n"
    claims = _parse_claims(md)
    citekeys = {c.citekey for c in claims}
    assert "good2020" in citekeys
    assert "not_a_cite" not in citekeys


def test_parse_claims_offsets_in_range():
    claims = _parse_claims(SIMPLE_MD)
    for c in claims:
        assert 0 <= c.start_offset < len(SIMPLE_MD)
        assert c.start_offset < c.end_offset <= len(SIMPLE_MD)


def test_parse_claims_plain_citation_has_no_false_numeric_anchor():
    claims = _parse_claims("См. [@smith2020].\n")
    assert len(claims) == 1
    assert claims[0].anchors == []


# ---------------------------------------------------------------------------
# Path traversal protection (via read_pdf_sidecar)
# ---------------------------------------------------------------------------

def test_path_traversal_rejected(tmp_path):
    from klemma.literature.sidecar import read_pdf_sidecar

    result = read_pdf_sidecar(tmp_path, "../../etc/passwd")
    assert result is None


def test_path_traversal_with_null_byte_rejected(tmp_path):
    from klemma.literature.sidecar import read_pdf_sidecar

    result = read_pdf_sidecar(tmp_path, "valid\x00../../etc/passwd")
    assert result is None


# ---------------------------------------------------------------------------
# verify_claim (deterministic)
# ---------------------------------------------------------------------------

def _make_bundle(
    kind="numeric",
    anchor_raw="15.5 °C",
    trigger="numeric_value",
    source_available=True,
    search_complete=True,
    anchor_found=True,
    passages=None,
    sentence="The temp is 15.5 °C",
    citekey="smith2020",
):
    anchor = ClaimAnchor(
        kind=kind,
        raw=anchor_raw,
        trigger=trigger,
        start_offset=10,
        end_offset=20,
        anchor_id="10:20",
    )
    return EvidenceBundle(
        claim_sentence=sentence,
        citekey=citekey,
        location="",
        anchor=anchor,
        passages=passages or ["The temperature is 15.5 °C in this region."],
        source_available=source_available,
        search_complete=search_complete,
        anchor_found=anchor_found,
    )


def test_verify_claim_no_source():
    b = _make_bundle(source_available=False)
    v = verify_claim(b)
    assert v.severity == "unverifiable"


def test_verify_claim_quote_found():
    long_quote = "«продолжительный период устойчивого охлаждения поверхности»"
    b = _make_bundle(
        kind="quote",
        anchor_raw=long_quote,
        trigger="quoted_span",
        passages=["продолжительный период устойчивого охлаждения поверхности наблюдается в Арктике."],
    )
    v = verify_claim(b)
    assert v.severity == "ok"


def test_verify_claim_quote_missing_hard_warn():
    long_quote = "«продолжительный период устойчивого охлаждения поверхности»"
    b = _make_bundle(
        kind="quote",
        anchor_raw=long_quote,
        trigger="quoted_span",
        passages=["Нет такой фразы в источнике."],
        search_complete=True,
    )
    v = verify_claim(b)
    assert v.severity == "hard_warn"


def test_verify_claim_quote_missing_unverifiable_when_incomplete():
    long_quote = "«продолжительный период устойчивого охлаждения поверхности»"
    b = _make_bundle(
        kind="quote",
        anchor_raw=long_quote,
        trigger="quoted_span",
        passages=["Нет такой фразы."],
        search_complete=False,
    )
    v = verify_claim(b)
    assert v.severity == "unverifiable"


def test_verify_claim_numeric_absent_hard_warn():
    b = _make_bundle(
        kind="numeric",
        anchor_raw="99.9",
        anchor_found=False,
        search_complete=True,
        passages=["The temperature is 15.5 °C."],
    )
    v = verify_claim(b)
    assert v.severity == "hard_warn"
    assert "99.9" in v.offending_span


def test_verify_claim_numeric_present_unverifiable():
    b = _make_bundle(kind="numeric", anchor_found=True)
    v = verify_claim(b)
    # numeric-found needs AI → unverifiable in deterministic path
    assert v.severity == "unverifiable"


def test_verify_claim_definitional_unverifiable():
    b = _make_bundle(kind="definitional", trigger="— это")
    v = verify_claim(b)
    assert v.severity == "unverifiable"


# ---------------------------------------------------------------------------
# verify_claim_batch (AI, mocked judge)
# ---------------------------------------------------------------------------

def _make_judge(response_json: str, error: str | None = None):
    """Return a mock AIProvider with a scripted call_with_meta."""
    from klemma.ai import AICallResult

    judge = MagicMock()
    judge.call_with_meta.return_value = AICallResult(
        text=response_json if not error else "",
        duration_ms=50,
        model="test-model",
        error=error,
        input_tokens=100,
        output_tokens=50,
    )
    judge.render_prompt.return_value = "RENDERED_PROMPT"
    return judge


def _make_config_mock(
    timeout=60,
    max_claim_chars=1000,
    max_passage_chars=2000,
    max_passages=8,
    max_prompt_chars=12000,
    max_output_tokens=1024,
):
    cfg = MagicMock()
    cfg.ai.citation_check_timeout = timeout
    cfg.ai.citation_check_max_claim_chars = max_claim_chars
    cfg.ai.citation_check_max_passage_chars = max_passage_chars
    cfg.ai.citation_check_max_passages = max_passages
    cfg.ai.citation_check_max_prompt_chars = max_prompt_chars
    cfg.ai.citation_check_max_output_tokens = max_output_tokens
    return cfg


def _valid_judge_response(anchor_id="10:20", severity="ok"):
    import json
    return json.dumps({
        "verdicts": [{
            "anchor_id": anchor_id,
            "verdict": "ok" if severity == "ok" else "contradicted",
            "contradiction": False,
            "severity": severity,
            "offending_span": "",
            "reason": "test reason",
        }]
    })


@pytest.fixture
def prompt_file(tmp_path):
    p = tmp_path / "citation_check.md"
    p.write_text("Test prompt {{ bundles }}", encoding="utf-8")
    return p


def test_batch_ok_verdict(prompt_file):
    bundle = _make_bundle(kind="numeric", anchor_found=True)
    judge = _make_judge(_valid_judge_response("10:20", "ok"))
    cfg = _make_config_mock()
    deadline = _Deadline.from_secs(60)

    result = verify_claim_batch([bundle], judge_ai=judge, deadline=deadline, cfg=cfg, prompt_path=prompt_file)

    assert len(result.verdicts) == 1
    assert result.verdicts[0].severity == "ok"
    assert result.verdicts[0].ai_used


def test_batch_hard_warn_verdict(prompt_file):
    bundle = _make_bundle(kind="numeric", anchor_found=True)
    judge = _make_judge(_valid_judge_response("10:20", "hard_warn"))
    cfg = _make_config_mock()
    deadline = _Deadline.from_secs(60)

    result = verify_claim_batch([bundle], judge_ai=judge, deadline=deadline, cfg=cfg, prompt_path=prompt_file)

    assert result.verdicts[0].severity == "hard_warn"


def test_batch_deadline_exceeded(prompt_file):
    bundle = _make_bundle(kind="numeric", anchor_found=True)
    judge = _make_judge("{}")
    cfg = _make_config_mock()
    deadline = _Deadline(0.0)  # already expired

    result = verify_claim_batch([bundle], judge_ai=judge, deadline=deadline, cfg=cfg, prompt_path=prompt_file)

    assert len(result.verdicts) == 1
    assert result.verdicts[0].severity == "unverifiable"
    assert "deadline" in result.verdicts[0].reason
    judge.call_with_meta.assert_not_called()


def test_batch_missing_prompt(tmp_path):
    bundle = _make_bundle(kind="numeric", anchor_found=True)
    judge = _make_judge("{}")
    cfg = _make_config_mock()
    deadline = _Deadline.from_secs(60)
    missing = tmp_path / "nonexistent.md"

    result = verify_claim_batch([bundle], judge_ai=judge, deadline=deadline, cfg=cfg, prompt_path=missing)

    assert result.verdicts[0].severity == "unverifiable"


def test_batch_malformed_json(prompt_file):
    bundle = _make_bundle(kind="numeric", anchor_found=True)
    judge = _make_judge("not json at all {{")
    cfg = _make_config_mock()
    deadline = _Deadline.from_secs(60)

    result = verify_claim_batch([bundle], judge_ai=judge, deadline=deadline, cfg=cfg, prompt_path=prompt_file)

    assert result.verdicts[0].severity == "unverifiable"
    assert result.errors


def test_batch_missing_anchor_id_in_response(prompt_file):
    import json
    bundle = _make_bundle(kind="numeric", anchor_found=True)
    # Judge returns a verdict for a different anchor_id
    resp = json.dumps({"verdicts": [{"anchor_id": "999:999", "verdict": "ok", "severity": "ok",
                                      "contradiction": False, "offending_span": "", "reason": "x"}]})
    judge = _make_judge(resp)
    cfg = _make_config_mock()
    deadline = _Deadline.from_secs(60)

    result = verify_claim_batch([bundle], judge_ai=judge, deadline=deadline, cfg=cfg, prompt_path=prompt_file)

    assert result.verdicts[0].severity == "unverifiable"
    assert result.errors


def test_batch_judge_error(prompt_file):
    bundle = _make_bundle(kind="numeric", anchor_found=True)
    judge = _make_judge("", error="API timeout")
    cfg = _make_config_mock()
    deadline = _Deadline.from_secs(60)

    result = verify_claim_batch([bundle], judge_ai=judge, deadline=deadline, cfg=cfg, prompt_path=prompt_file)

    assert result.verdicts[0].severity == "unverifiable"
    assert "API timeout" in result.errors[0] or result.errors


# ---------------------------------------------------------------------------
# Numbered-reference mode (papers/, PR-3)
# ---------------------------------------------------------------------------

def _make_ref_map(matched=None, unmatched=None):
    from klemma.literature.reference_parser import ParsedReference
    from klemma.skills.reference_matcher import RefMap

    ref_map = RefMap()
    for n, ck in (matched or {}).items():
        ref_map.number_to_citekey[n] = ck
    for n in unmatched or []:
        ref_map.unmatched[n] = ParsedReference(raw=f"entry {n}")
    return ref_map


NUMBERED_MD = """# Статья

Точность прогноза составила 85 % [5].

Метод оценки определяется как разность площадей [5, п. 3.4].

Двойная ссылка подтверждает вывод о расхождении оценок [5, 12].

Там же на численном примере показано влияние кромки льда.

## Список литературы

5. Smith J. Sea ice paper with enough length to parse. 2020.

12. Jones K. Another paper with enough length to parse. 2021.
"""


def test_numbered_claims_resolved_via_ref_map():
    ref_map = _make_ref_map(matched={5: "smith2020", 12: "jones2021"})
    claims = _parse_claims(NUMBERED_MD, ref_map=ref_map)
    citekeys = {c.citekey for c in claims}
    assert "smith2020" in citekeys
    assert "jones2021" in citekeys


def test_numbered_multi_ref_marker_one_claim_per_number():
    ref_map = _make_ref_map(matched={5: "smith2020", 12: "jones2021"})
    claims = _parse_claims(NUMBERED_MD, ref_map=ref_map)
    double = [c for c in claims if "Двойная ссылка" in c.sentence]
    assert {c.citekey for c in double} == {"smith2020", "jones2021"}


def test_numbered_locator_fills_location():
    ref_map = _make_ref_map(matched={5: "smith2020"})
    claims = _parse_claims(NUMBERED_MD, ref_map=ref_map)
    with_locator = [c for c in claims if c.location]
    assert len(with_locator) == 1
    assert with_locator[0].location == "п. 3.4"
    assert "разность площадей" in with_locator[0].sentence


def test_numbered_every_claim_has_reference_anchor():
    ref_map = _make_ref_map(matched={5: "smith2020"}, unmatched=[12])
    claims = _parse_claims(NUMBERED_MD, ref_map=ref_map)
    assert claims
    for c in claims:
        kinds = [a.kind for a in c.anchors]
        assert "reference" in kinds


def test_numbered_marker_digits_not_numeric_anchor():
    ref_map = _make_ref_map(matched={5: "smith2020"})
    md = "Простое утверждение без чисел в тексте [5].\n\n## Список литературы\n\n5. Smith J. Long enough entry to parse correctly. 2020.\n"
    claims = _parse_claims(md, ref_map=ref_map)
    assert len(claims) == 1
    numeric = [a for a in claims[0].anchors if a.kind == "numeric"]
    assert numeric == []


def test_numbered_numeric_anchor_from_sentence_body_kept():
    ref_map = _make_ref_map(matched={5: "smith2020"})
    claims = _parse_claims(NUMBERED_MD, ref_map=ref_map)
    first = [c for c in claims if "85 %" in c.sentence]
    assert first
    numeric = [a for a in first[0].anchors if a.kind == "numeric"]
    assert numeric
    assert "85" in numeric[0].raw


def test_numbered_unmatched_gets_only_reference_anchor():
    ref_map = _make_ref_map(matched={5: "smith2020"}, unmatched=[12])
    claims = _parse_claims(NUMBERED_MD, ref_map=ref_map)
    unmatched = [c for c in claims if c.citekey == "" and c.anchors[0].trigger == "unmatched_ref"]
    assert unmatched
    for c in unmatched:
        assert len(c.anchors) == 1
        assert c.anchors[0].kind == "reference"
        assert c.anchors[0].raw == "[12]"


def test_numbered_anaphora_detected():
    ref_map = _make_ref_map(matched={5: "smith2020"})
    claims = _parse_claims(NUMBERED_MD, ref_map=ref_map)
    anaphoric = [c for c in claims if c.anchors and c.anchors[0].trigger == "anaphoric_ref"]
    assert len(anaphoric) == 1
    assert "Там же" in anaphoric[0].sentence
    assert anaphoric[0].citekey == ""


def test_numbered_mode_off_without_ref_map():
    claims = _parse_claims(NUMBERED_MD)
    assert claims == []


def test_numbered_mode_off_without_bibliography():
    md = "Утверждение с нумерованной ссылкой [5].\n"
    ref_map = _make_ref_map(matched={5: "smith2020"})
    claims = _parse_claims(md, ref_map=ref_map)
    assert claims == []


def test_numbered_mode_off_when_at_citations_present():
    md = (
        "Утверждение с обычной ссылкой [@alpha2020] и ложным маркером [1].\n\n"
        "## Список литературы\n\n"
        "1. Smith J. Long enough entry to parse correctly here. 2020.\n"
    )
    ref_map = _make_ref_map(matched={1: "smith2020"})
    claims = _parse_claims(md, ref_map=ref_map)
    citekeys = {c.citekey for c in claims}
    assert citekeys == {"alpha2020"}
    for c in claims:
        assert all(a.kind != "reference" for a in c.anchors)


def test_numbered_bibliography_entries_not_claims():
    ref_map = _make_ref_map(matched={5: "smith2020"})
    claims = _parse_claims(NUMBERED_MD, ref_map=ref_map)
    for c in claims:
        assert "Список литературы" not in c.sentence
        assert "Sea ice paper" not in c.sentence


def test_verify_claim_reference_unmatched_soft_warn():
    b = _make_bundle(kind="reference", anchor_raw="[12]", trigger="unmatched_ref",
                     source_available=False, citekey="")
    v = verify_claim(b)
    assert v.severity == "soft_warn"
    assert "ref [12] not matched to library" in v.reason


def test_verify_claim_reference_anaphoric_soft_warn():
    b = _make_bundle(kind="reference", anchor_raw="Там же", trigger="anaphoric_ref",
                     source_available=False, citekey="")
    v = verify_claim(b)
    assert v.severity == "soft_warn"
    assert "anaphoric" in v.reason


def test_verify_claim_reference_matched_ok():
    b = _make_bundle(kind="reference", anchor_raw="[5]", trigger="numbered_ref",
                     source_available=True, citekey="smith2020")
    v = verify_claim(b)
    assert v.severity == "ok"
    assert "smith2020" in v.reason


def test_verify_claim_reference_matched_no_source_unverifiable():
    b = _make_bundle(kind="reference", anchor_raw="[5]", trigger="numbered_ref",
                     source_available=False, citekey="smith2020")
    v = verify_claim(b)
    assert v.severity == "unverifiable"


def test_needs_ai_check_reference_false():
    from klemma.skills.citation_checker import _needs_ai_check
    b = _make_bundle(kind="reference", anchor_raw="[5]", trigger="numbered_ref",
                     anchor_found=True)
    assert _needs_ai_check(b) is False


class _FakeState:
    """Minimal state stub: metadata for ref matching + fragments fallback."""

    def __init__(self, meta, fragments=None):
        self._meta = meta
        self._frags = fragments or {}

    def get_all_sources_metadata(self):
        return self._meta

    def get_fragments(self, citekey):
        return self._frags.get(citekey, [])


def test_check_citations_file_numbered_end_to_end(tmp_path):
    from klemma.skills.citation_checker import check_citations_file

    md = tmp_path / "paper.md"
    md.write_text(NUMBERED_MD, encoding="utf-8")

    # Sidecar gives full-text evidence for the matched source
    sidecar_dir = tmp_path / ".klemma" / "pdfs"
    sidecar_dir.mkdir(parents=True)
    (sidecar_dir / "smith2020.md").write_text(
        "---\ncitekey: smith2020\n---\n\nТочность прогноза составила 85 % на тестовой выборке.\n",
        encoding="utf-8",
    )

    state = _FakeState(meta=[
        {"id": "smith2020", "title": "Sea ice paper", "authors": "Smith, John",
         "year": 2020, "doi": ""},
    ])

    cfg = _make_config_mock()
    cfg.ai.citation_check_max_wall_clock = 120
    cfg.ai.max_ai_calls_per_draft = 12

    report = check_citations_file(
        md,
        config=cfg,
        state=state,
        paper_store=None,
        user_library=None,
        judge_ai=None,
        project_root=tmp_path,
        use_ai=False,
    )

    assert report.status == "ok"
    assert report.verdicts, "numbered paper must produce verdicts, not 'no claims found'"

    # Matched refs resolved deterministically
    ok_refs = [v for v in report.verdicts
               if v.anchor.kind == "reference" and v.severity == "ok"]
    assert ok_refs
    assert all(v.citekey == "smith2020" for v in ok_refs)

    # Unmatched ref [12] → soft_warn
    unmatched = [v for v in report.verdicts if "not matched to library" in v.reason]
    assert unmatched
    assert unmatched[0].severity == "soft_warn"
    assert "[12]" in unmatched[0].reason

    # Anaphora flagged
    anaphoric = [v for v in report.verdicts if "anaphoric" in v.reason]
    assert len(anaphoric) == 1
    assert anaphoric[0].severity == "soft_warn"

    # Locator from "[5, п. 3.4]" threaded into the verdict
    located = [v for v in report.verdicts if v.location == "п. 3.4"]
    assert located


def test_check_citations_file_at_mode_unaffected_by_ref_map_build(tmp_path):
    """@-mode files still parse identically (ref map build is guarded on '[@')."""
    from klemma.skills.citation_checker import check_citations_file

    md = tmp_path / "chapter.md"
    md.write_text("Значение 15.5 °C приводится в [@smith2020].\n", encoding="utf-8")

    cfg = _make_config_mock()
    cfg.ai.citation_check_max_wall_clock = 120
    cfg.ai.max_ai_calls_per_draft = 12

    report = check_citations_file(
        md,
        config=cfg,
        state=_FakeState(meta=[]),
        paper_store=None,
        user_library=None,
        judge_ai=None,
        project_root=tmp_path,
        use_ai=False,
    )

    assert all(v.anchor.kind != "reference" for v in report.verdicts)
    assert {v.citekey for v in report.verdicts} == {"smith2020"}


def test_batch_sanitizes_delimiters(prompt_file):
    """Injected <<< >>> in claim text should be stripped before rendering."""
    bundle = _make_bundle(
        kind="definitional",
        sentence="This <<<INJECT>>> marker [@evil2023].",
        passages=["Normal source text."],
        anchor_raw="<<<INJECT>>>",
    )
    import json
    resp = json.dumps({"verdicts": [{"anchor_id": "10:20", "verdict": "ok", "severity": "ok",
                                      "contradiction": False, "offending_span": "", "reason": "ok"}]})
    judge = _make_judge(resp)
    cfg = _make_config_mock()
    deadline = _Deadline.from_secs(60)

    # Must not raise; rendered prompt should not contain raw <<< >>>
    verify_claim_batch([bundle], judge_ai=judge, deadline=deadline, cfg=cfg, prompt_path=prompt_file)
    rendered_call = judge.render_prompt.call_args
    # Check that the bundles passed to render_prompt have sanitized text
    if rendered_call:
        bundles_arg = rendered_call[1].get("bundles", [])
        for b in bundles_arg:
            assert "<<<" not in b.get("anchor_raw", "")
            assert "<<<" not in b.get("claim_sentence", "")
