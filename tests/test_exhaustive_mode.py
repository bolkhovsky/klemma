"""Plan C4: exhaustive (best-effort) extraction mode and its gold evaluation."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

from klemma.ai import AICallResult
from klemma.literature.models import ZoteroEntry
from klemma.literature.pdf import ChunkRecord
from klemma.skills.extract_engine import build_full_text, extract_from_pages
from klemma.skills.outline_digest import digest_ids, not_extracted

ENTRY = ZoteroEntry(id="p", title="P")
DIGEST = "Глава 2. Модель\n  2.4 Ошибка кромки\n    2.4.1 Бинаризация\n    2.4.2 Декомпозиция\n  2.5 Нейросети"


def _res(payload, finish="stop", tout=100):
    return AICallResult(text=json.dumps(payload), input_tokens=10, output_tokens=tout,
                        model="m", finish_reason=finish)


def _ai(script):
    ai = MagicMock()
    ai.model = "m"
    ai.render_prompt.return_value = "prompt"
    q = list(script)
    calls = []

    def _call(system, user, **kw):
        calls.append(kw)
        return q.pop(0)

    ai.call_with_meta.side_effect = _call
    ai._calls = calls
    return ai


def test_exhaustive_uses_full_cap_and_small_chunks(tmp_path):
    prompt = tmp_path / "e.md"
    prompt.write_text("p")
    pages = ["word " * 6000]  # ~30k chars → standard 25k chunks, exhaustive 20k
    full = build_full_text(pages)
    ai = _ai([_res({"fragments": [{"text": "word word", "verbatim": True}]})] * 3)
    out = extract_from_pages(pages, ENTRY, prompt, {}, ai, chunk_size=25000, overlap=1000,
                             max_tokens_cap=16384, mode="exhaustive", full_text=full)
    assert all(c["max_tokens"] == 16384 for c in ai._calls)
    assert out.leaf_chunks >= 2  # 30k text split with the 20k exhaustive chunk size


def test_notes_are_validated_deduped_and_not_covered_ignored(tmp_path):
    prompt = tmp_path / "e.md"
    prompt.write_text("p")
    pages = ["The edge misplacement dominates. Area error is secondary. " * 20]
    full = build_full_text(pages)
    q = "The edge misplacement dominates."
    ai = _ai([_res({
        "fragments": [{"text": q, "verbatim": True, "section": "2.4.2"}],
        "notes": {
            "contradicts": [
                {"item": "2.4.1", "quote": q, "note": "n1"},
                {"item": "2.4.1", "quote": "The edge  misplacement dominates.", "note": "dup"},
                {"item": "2.4.1", "quote": "fabricated sentence nowhere", "note": "n3"},
            ],
            "qualifies": [{"item": "2.5", "quote": "Area error is secondary.", "note": "q1"}],
            "not_covered": ["2.4.1", "2.5"],
        },
    })])
    out = extract_from_pages(None, ENTRY, prompt, {}, ai,
                             chunks=[ChunkRecord(0, full, 1, 1, 0, len(full))], full_text=full,
                             mode="exhaustive")
    c = out.notes["contradicts"]
    assert [n["status"] for n in c] == ["confirmed", "unverified"]
    assert c[0]["char_start"] is not None and c[1]["char_start"] is None
    assert out.notes["qualifies"][0]["status"] == "confirmed"
    assert "not_covered" not in out.notes


def test_not_extracted_is_deterministic_from_digest():
    assert digest_ids(DIGEST) == ["2.4", "2.4.1", "2.4.2", "2.5"]
    assert not_extracted(DIGEST, {"2.4.2", "2.5"}) == ["2.4.1"]  # 2.4 covered via 2.4.2
    assert not_extracted("", {"x"}) == []


def test_extract_fragments_exhaustive_reports_notes_and_not_extracted(tmp_path):
    from unittest.mock import patch

    from klemma.literature.models import Fragment
    from klemma.skills import extractor as ex_mod
    from klemma.skills.extract_engine import (
        ChunkOutcome,
        CoverageReport,
        ExtractedFragment,
        ExtractionOutcome,
    )

    outcome = ExtractionOutcome(
        fragments=[ExtractedFragment(fragment=Fragment(text="a", section="2.4.2"))],
        key_refs=[], summary="", notes={"qualifies": [{"item": "2.5", "quote": "x", "status": "confirmed"}]},
        chunks=[ChunkOutcome(0, 0, 10, "ok")], coverage=CoverageReport(10, 10),
        prompt_hash="h", model="m", leaf_chunks=1,
    )
    state = MagicMock()
    state.save_fragments.return_value = 1
    cfg = MagicMock()
    cfg.ai.language = "ru"
    cfg.ai.task_classes = {}
    cfg.ai.exhaustive_max_tokens = 16384
    home = tmp_path / ".klemma"
    (home / "prompts").mkdir(parents=True)
    (home / "prompts" / "extract_exhaustive.md").write_text("p")
    with patch("klemma.skills.extract_engine.extract_from_pages", return_value=outcome) as eng:
        res = ex_mod.extract_fragments(ENTRY, "t", cfg, state, MagicMock(), klemma_home=home,
                                       mode="exhaustive", outline_digest=DIGEST)
    assert eng.call_args.kwargs["mode"] == "exhaustive" and eng.call_args.kwargs["max_tokens_cap"] == 16384
    assert str(eng.call_args.args[2]).endswith("extract_exhaustive.md")
    assert res.not_extracted == ["2.4.1"]
    assert res.notes["qualifies"][0]["item"] == "2.5"


def test_structure_notes_formatter():
    from klemma.skills.extractor import format_structure_notes

    txt = format_structure_notes(
        {"contradicts": [{"item": "1.4.1", "quote": "q", "note": "n", "status": "unverified"}]},
        ["2.4.1"],
    )
    assert "Противоречит 1.4.1" in txt and "не подтверждена" in txt and "not_extracted" in txt
    assert format_structure_notes({}, []) == "—"


# ---------------------------------------------------------------------------
# Gold evaluation (pure)
# ---------------------------------------------------------------------------


def test_eval_recall_matching_and_precision_labels(tmp_path):
    from klemma.evaluation.extract_eval import (
        DocResult,
        evaluate,
        load_gold_dir,
        manifest,
        render_report,
        text_key,
    )

    frame = "[Page 3]\nThe misplacement error dominates in autumn. Extent error is small. Noise."
    (tmp_path / "doc1.json").write_text(json.dumps({
        "citekey": "doc1", "frame_pages": [3, 3],
        "claims": [
            {"quote": "The misplacement error dominates in autumn.", "item": "2.4.2"},
            {"quote": "Extent error is small.", "item": "2.4.2"},
            {"quote": "This claim is never extracted.", "item": "2.5"},
        ],
    }), encoding="utf-8")
    (tmp_path / "doc1.labels.json").write_text(json.dumps({
        text_key("The misplacement error dominates in autumn."): "relevant",
        text_key("Noise."): "irrelevant",
    }), encoding="utf-8")
    docs = load_gold_dir(tmp_path)
    assert docs[0].frame_pages == (3, 3) and len(docs[0].claims) == 3

    def runner(doc, i):
        # run 0: containment match + a partial-span match; run 1: only containment
        span_start = frame.index("Extent error is small.")
        frags = [("The misplacement error dominates in autumn.", None),
                 ("Noise.", None)]
        if i == 0:
            frags.append(("Extent error is", (span_start, span_start + int(0.85 * len("Extent error is small.")))))
        return frags, frame

    results = evaluate(docs, runner, runs=2)
    r = results[0]
    assert isinstance(r, DocResult)
    assert [m.found for m in r.runs] == [2, 1]
    assert round(r.min_recall, 2) == 0.33 and round(r.mean_recall, 2) == 0.5
    # run 0 has an unlabelled fragment → precision undefined; run 1 fully labelled
    assert r.runs[0].precision is None and r.runs[1].precision == 0.5
    from klemma.evaluation.extract_eval import candidate_labels_template, verdict

    v = verdict(results, recall_threshold=0.9)
    assert not v.recall_pass and v.precision_pass is None and not v.passed
    report = render_report(results, identity={"model": "m"}, recall_threshold=0.9)
    assert "min 0.33" in report and "fail" in report and "doc1" in report
    assert "misplacement" not in report  # no quotes in the report
    cands = candidate_labels_template(results)["doc1"]
    assert text_key("Noise.") in cands and cands[text_key("Noise.")] == "Noise."
    m = manifest(tmp_path)
    assert {e["file"] for e in m["files"]} == {"doc1.json", "doc1.labels.json"}


def test_eval_gold_errors_and_marker_straddling_quotes(tmp_path):
    import pytest

    from klemma.evaluation.extract_eval import GoldError, claim_found, gold_span, load_gold_dir

    (tmp_path / "bad.json").write_text(json.dumps({"citekey": "bad", "claims": []}), encoding="utf-8")
    with pytest.raises(GoldError):
        load_gold_dir(tmp_path)
    (tmp_path / "bad.json").write_text(json.dumps({"citekey": "bad", "claims": [{"item": "x"}]}), encoding="utf-8")
    with pytest.raises(GoldError):
        load_gold_dir(tmp_path)
    # a quote straddling a page marker is still located in raw coordinates
    frame = "[Page 1]\nThe edge error grows with\n\n[Page 2]\nlead time in autumn. Other text."
    claim = {"quote": "The edge error grows with lead time in autumn."}
    span = gold_span(claim, frame)
    assert span is not None and frame[span[0]:span[0] + 8] == "The edge"
    assert claim_found(claim, [("x", (span[0], span[1] - 3))], frame)


def test_section_ids_are_normalised_and_validated(tmp_path):
    from klemma.skills.extract_engine import normalize_section_id

    ids = {"2.4", "2.4.1", "2.4.2", "2.5"}
    assert normalize_section_id("2.4.1.") == "2.4.1"
    assert normalize_section_id("§2.4.1 Бинаризация", ids) == "2.4.1"
    assert normalize_section_id("2.4.1.7", ids) == "2.4.1"  # walk up to a known ancestor
    assert normalize_section_id("9.9", ids) is None and normalize_section_id("intro", ids) is None
    prompt = tmp_path / "e.md"
    prompt.write_text("p")
    full = build_full_text(["alpha beta gamma delta " * 30])
    ai = _ai([_res({"fragments": [
        {"text": "alpha beta", "section": "2.4.1."},          # verbatim omitted → exhaustive default True
        {"text": "gamma delta", "section": "7.7", "verbatim": True},
    ]})])
    out = extract_from_pages(None, ENTRY, prompt, {"outline_digest": DIGEST}, ai,
                             chunks=[ChunkRecord(0, full, 1, 1, 0, len(full))], full_text=full,
                             mode="exhaustive")
    by = {ef.fragment.text: ef for ef in out.fragments}
    assert by["alpha beta"].fragment.section == "2.4.1" and by["alpha beta"].verbatim_status == "confirmed"
    assert by["gamma delta"].fragment.section is None


def test_not_extracted_is_hierarchical():
    assert not_extracted(DIGEST, {"2.4.1", "2.4.2"}) == ["2.5"]  # 2.4 covered through its items
    assert not_extracted(DIGEST, {"2.4"}) == ["2.4.1", "2.4.2", "2.5"]


# ---------------------------------------------------------------------------
# Internal multi-agent review of PR-C/PR-D (confirmed findings)
# ---------------------------------------------------------------------------


def test_merge_notes_tolerates_null_and_dict_categories(tmp_path):
    prompt = tmp_path / "e.md"
    prompt.write_text("p")
    pages = ["alpha beta. " * 400, "gamma delta. " * 400]
    full = build_full_text(pages)
    half = full.index("[Page 2]")
    chunks = [ChunkRecord(0, full[:half], 1, 1, 0, half), ChunkRecord(1, full[half:], 2, 2, half, len(full))]
    ai = _ai([
        _res({"fragments": [], "notes": {"contradicts": None, "qualifies": {"item": "2.5", "quote": "gamma delta.", "note": "x"},
                                          "not_covered": ["2.4"], "invented": ["y"]}}),
        _res({"fragments": [], "notes": {"contradicts": [{"item": "2.4.1", "quote": "alpha beta.", "note": "n"}], "qualifies": None}}),
    ])
    out = extract_from_pages(None, ENTRY, prompt, {"outline_digest": DIGEST}, ai, chunks=chunks,
                             full_text=full, mode="exhaustive")
    assert out.error is None
    assert {n["item"] for n in out.notes["contradicts"]} == {"2.4.1"}
    assert out.notes["qualifies"][0]["item"] == "2.5"
    assert set(out.notes) <= {"contradicts", "qualifies"}


def test_note_items_outside_outline_are_dropped_and_fuzzy_labelled(tmp_path):
    prompt = tmp_path / "e.md"
    prompt.write_text("p")
    full = build_full_text(["The edge misplace-\nment dominates in autumn. " * 5])
    ai = _ai([_res({"fragments": [], "notes": {"contradicts": [
        {"item": "9.9.9", "quote": "The edge misplacement dominates in autumn.", "note": "x"},
        {"item": "2.4.1", "quote": "The edge misplacement dominates in autumn.", "note": "y"},
    ]}})])
    out = extract_from_pages(None, ENTRY, prompt, {"outline_digest": DIGEST}, ai,
                             chunks=[ChunkRecord(0, full, 1, 1, 0, len(full))], full_text=full, mode="exhaustive")
    notes = out.notes["contradicts"]
    assert [n["item"] for n in notes] == ["2.4.1"]
    assert notes[0]["status"] in ("confirmed", "fuzzy")


def test_normalize_section_prefers_deepest_id_and_keeps_labels_without_outline():
    from klemma.skills.extract_engine import normalize_section_id

    assert normalize_section_id("Глава 2, п. 2.4.1") == "2.4.1"
    assert normalize_section_id("methodology") == "methodology"
    assert normalize_section_id("methodology", {"2.4"}) is None
    assert normalize_section_id("Глава 2, п. 2.4.1", {"2.4", "2.4.1"}) == "2.4.1"


def test_verdict_requires_precision_unless_allowed():
    from klemma.evaluation.extract_eval import DocResult, RunMetrics, verdict

    r = DocResult("d", [RunMetrics(0, found=10, total=10, labelled=1, relevant=1, fragments=2)])
    assert verdict([r]).passed is False and verdict([r]).precision_pass is None
    assert verdict([r], allow_unlabelled=True).passed is True
    full = DocResult("d", [RunMetrics(0, found=10, total=10, labelled=2, relevant=2, fragments=2)])
    assert verdict([full]).passed is True


def test_canonical_config_reflects_mode():
    import json as _json
    from types import SimpleNamespace

    from klemma.extraction_runs import canonical_config_json

    cfg = SimpleNamespace(model="m", chunk_size=25000, chunk_overlap=2000, min_chunk_chars=4000,
                          max_tokens_cap=8192, exhaustive_max_tokens=16384, budget_max_input_tokens=0,
                          budget_max_output_tokens=0, budget_max_cost_usd=None, language="ru")
    std = _json.loads(canonical_config_json(cfg))
    exh = _json.loads(canonical_config_json(cfg, "exhaustive"))
    assert std["effective_chunk_size"] == 25000 and exh["effective_chunk_size"] == 20000
    assert exh["effective_max_tokens"] == 16384 and std["effective_max_tokens"] == 8192
    assert canonical_config_json(cfg) != canonical_config_json(cfg, "exhaustive")
