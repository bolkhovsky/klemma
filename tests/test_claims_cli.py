"""Tests for the claims ledger CLI wiring — persistence, --incremental, gate."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from klemma.cli import main as klemma_cli
from klemma.skills.citation_checker import (
    BatchResult,
    CitationVerdict,
    compute_claim_hash,
)
from klemma.state import StateManager


def _make_kctx(project_root: Path, state=None):
    kctx = MagicMock()
    kctx.config = MagicMock()
    kctx.config.ai.citation_check_model = None
    kctx.config.ai.citation_check_timeout = 60
    kctx.config.ai.citation_check_retries = 0
    kctx.config.ai.citation_check_max_wall_clock = 120
    kctx.config.ai.max_ai_calls_per_draft = 12
    kctx.config.ai.citation_check_max_claim_chars = 1000
    kctx.config.ai.citation_check_max_passage_chars = 2000
    kctx.config.ai.citation_check_max_passages = 8
    kctx.config.ai.citation_check_max_prompt_chars = 12000
    kctx.config.ai.citation_check_max_output_tokens = 1024
    kctx.config.ai.backend = "litellm"
    kctx.config.ai.model = "openai/gpt-4o-mini"
    kctx.config.ai._resolved_api_keys = {}
    kctx.state = state if state is not None else MagicMock()
    kctx.project_root = project_root
    kctx.klemma_home = project_root / ".klemma"
    kctx.project_chain = [project_root]
    kctx.paper_store = None
    kctx.user_library = None
    return kctx


def _invoke(args, kctx):
    try:
        runner = CliRunner(mix_stderr=False)
    except TypeError:
        runner = CliRunner()
    with patch("klemma.commands.verify._get_context", return_value=kctx):
        return runner.invoke(klemma_cli, args, catch_exceptions=False)


def _write_sidecar(project_root: Path, citekey: str, content: str) -> None:
    pdfs = project_root / ".klemma" / "pdfs"
    pdfs.mkdir(parents=True, exist_ok=True)
    (pdfs / f"{citekey}.md").write_text(
        f"# {citekey}\n\n> Citekey: {citekey}\n\n---\n\n{content}\n", encoding="utf-8"
    )


# ---------------------------------------------------------------------------
# Persistence on check-citations runs
# ---------------------------------------------------------------------------


def test_check_citations_persists_claims(tmp_path):
    state = StateManager(tmp_path / "klemma.db")
    md = tmp_path / "chapter.md"
    md.write_text(
        "# Глава\n\nТочность метода составила 85 % согласно [@smith2020].\n",
        encoding="utf-8",
    )
    _write_sidecar(tmp_path, "smith2020", "Точность метода составила 85 % на выборке.")

    kctx = _make_kctx(tmp_path, state=state)
    result = _invoke(["check-citations", "--no-ai", str(md)], kctx)
    assert result.exit_code == 0, result.output

    rows = state.get_claims("chapter.md")
    assert rows, "check run must land in the claims ledger"
    assert all(r["stale"] == 0 for r in rows)
    assert any(r["anchor_kind"] == "numeric" for r in rows)


def test_check_citations_registers_anchorless_claims(tmp_path):
    """A cited sentence with no anchor must land in the ledger as unchecked."""
    state = StateManager(tmp_path / "klemma.db")
    md = tmp_path / "chapter.md"
    md.write_text(
        "# Глава\n\nПодход подробно описан в литературе [@smith2020].\n",
        encoding="utf-8",
    )

    kctx = _make_kctx(tmp_path, state=state)
    result = _invoke(["check-citations", "--no-ai", str(md)], kctx)
    assert result.exit_code == 0, result.output

    rows = state.get_claims("chapter.md")
    assert len(rows) == 1
    assert rows[0]["anchor_key"] == ""
    assert rows[0]["verdict"] is None

    summary = state.get_claims_status_summary("chapter.md")
    assert summary[0]["unchecked"] == 1


def test_check_citations_marks_removed_claims_stale(tmp_path):
    state = StateManager(tmp_path / "klemma.db")
    md = tmp_path / "chapter.md"
    md.write_text(
        "# Глава\n\nТочность метода составила 85 % согласно [@smith2020].\n",
        encoding="utf-8",
    )
    kctx = _make_kctx(tmp_path, state=state)
    _invoke(["check-citations", "--no-ai", str(md)], kctx)
    old_hash = state.get_claims("chapter.md")[0]["claim_hash"]

    # Edit the sentence — the old claim disappears from the fresh parse
    md.write_text(
        "# Глава\n\nТочность метода составила 87 % согласно [@smith2020].\n",
        encoding="utf-8",
    )
    _invoke(["check-citations", "--no-ai", str(md)], kctx)

    rows = {r["claim_hash"]: r for r in state.get_claims("chapter.md")}
    assert len(rows) == 2
    assert rows[old_hash]["stale"] == 1
    live = [r for r in rows.values() if r["stale"] == 0]
    assert len(live) == 1


def test_file_outside_project_not_persisted(tmp_path):
    project_root = tmp_path / "proj"
    project_root.mkdir()
    state = StateManager(project_root / "klemma.db")
    outside = tmp_path / "outside.md"
    outside.write_text("Текст со ссылкой [@smith2020] и числом 42.\n", encoding="utf-8")

    kctx = _make_kctx(project_root, state=state)
    result = _invoke(["check-citations", "--no-ai", str(outside)], kctx)

    assert "не обновлён" in result.output
    assert state.get_claims_status_summary() == []


# ---------------------------------------------------------------------------
# --incremental replay
# ---------------------------------------------------------------------------

_DEFINITIONAL_MD = (
    "# Глава\n\nДанный метод является стандартным подходом "
    "в обработке данных согласно [@smith2020].\n"
)


def _stub_batch(counter):
    def fake_batch(bundles, **kwargs):
        counter.append(len(bundles))
        return BatchResult(
            verdicts=[
                CitationVerdict(
                    citekey=b.citekey,
                    claim_sentence=b.claim_sentence,
                    location=b.location,
                    anchor=b.anchor,
                    severity="ok",
                    reason="judge confirmed",
                    offending_span="",
                    ai_used=True,
                )
                for b in bundles
            ],
            input_tokens=10,
            output_tokens=5,
            model="stub-judge",
            errors=[],
        )
    return fake_batch


def test_incremental_replays_verdict_without_judge(tmp_path):
    state = StateManager(tmp_path / "klemma.db")
    md = tmp_path / "chapter.md"
    md.write_text(_DEFINITIONAL_MD, encoding="utf-8")
    _write_sidecar(tmp_path, "smith2020", "Метод описан как стандартный подход.")
    kctx = _make_kctx(tmp_path, state=state)

    calls: list[int] = []
    with patch("klemma.commands.verify.build_judge_provider", return_value=MagicMock()), \
         patch("klemma.skills.citation_checker.verify_claim_batch", side_effect=_stub_batch(calls)):
        result = _invoke(["check-citations", str(md)], kctx)
    assert result.exit_code == 0, result.output
    assert calls, "first run must call the judge"

    rows = [r for r in state.get_claims("chapter.md") if r["verdict"] == "ok"]
    assert rows and rows[0]["ai_used"] == 1
    assert rows[0]["judge_model"] == "stub-judge"

    # Second run with --incremental: same text → verdict replayed, judge silent
    calls.clear()
    with patch("klemma.commands.verify.build_judge_provider", return_value=MagicMock()), \
         patch("klemma.skills.citation_checker.verify_claim_batch", side_effect=_stub_batch(calls)):
        result = _invoke(["check-citations", "--incremental", str(md)], kctx)
    assert result.exit_code == 0, result.output
    assert calls == [], "--incremental must not re-judge a live verdict"

    rows = [r for r in state.get_claims("chapter.md") if r["verdict"] == "ok"]
    assert rows[0]["reason"].startswith("[cached] ")
    # Provenance survives the replay even though no fresh judge call happened
    assert rows[0]["judge_model"] == "stub-judge"


def test_incremental_rejudges_edited_claim(tmp_path):
    state = StateManager(tmp_path / "klemma.db")
    md = tmp_path / "chapter.md"
    md.write_text(_DEFINITIONAL_MD, encoding="utf-8")
    _write_sidecar(tmp_path, "smith2020", "Метод описан как стандартный подход.")
    kctx = _make_kctx(tmp_path, state=state)

    calls: list[int] = []
    with patch("klemma.commands.verify.build_judge_provider", return_value=MagicMock()), \
         patch("klemma.skills.citation_checker.verify_claim_batch", side_effect=_stub_batch(calls)):
        _invoke(["check-citations", str(md)], kctx)
    assert calls

    # The sentence was edited — its hash changed, replay must miss
    md.write_text(_DEFINITIONAL_MD.replace("стандартным", "общепринятым"), encoding="utf-8")
    calls.clear()
    with patch("klemma.commands.verify.build_judge_provider", return_value=MagicMock()), \
         patch("klemma.skills.citation_checker.verify_claim_batch", side_effect=_stub_batch(calls)):
        _invoke(["check-citations", "--incremental", str(md)], kctx)
    assert calls, "edited claim must go through the judge again"


# ---------------------------------------------------------------------------
# klemma claims status --gate
# ---------------------------------------------------------------------------


def _seed(state, manuscript, verdicts):
    entries = []
    for i, verdict in enumerate(verdicts):
        sentence = f"Утверждение номер {i} из рукописи."
        entries.append({
            "claim_hash": compute_claim_hash(sentence, "smith2020"),
            "anchor_key": f"numeric:{i:012d}" if verdict != "unchecked" else "",
            "sentence": sentence,
            "citekey": "smith2020",
            "ref_number": None,
            "location": "",
            "char_start": i * 100,
            "char_end": i * 100 + 50,
            "anchor_kind": "numeric" if verdict != "unchecked" else None,
            "anchor_raw": "42" if verdict != "unchecked" else None,
            "verdict": None if verdict == "unchecked" else verdict,
            "reason": None,
            "ai_used": False,
            "evidence_start": None,
            "evidence_end": None,
            "evidence_locator": None,
        })
    state.record_claim_check(manuscript, entries)
    return entries


def test_claims_status_lists_manuscripts(tmp_path):
    state = StateManager(tmp_path / "klemma.db")
    _seed(state, "paper.md", ["ok", "soft_warn"])
    kctx = _make_kctx(tmp_path, state=state)

    result = _invoke(["claims", "status"], kctx)
    assert result.exit_code == 0
    assert "paper.md" in result.output


def test_gate_passes_when_all_ok(tmp_path):
    state = StateManager(tmp_path / "klemma.db")
    _seed(state, "paper.md", ["ok", "ok"])
    kctx = _make_kctx(tmp_path, state=state)

    result = _invoke(["claims", "status", "--gate"], kctx)
    assert result.exit_code == 0, result.output


def test_gate_fails_on_unchecked(tmp_path):
    state = StateManager(tmp_path / "klemma.db")
    _seed(state, "paper.md", ["ok", "unchecked"])
    kctx = _make_kctx(tmp_path, state=state)

    result = _invoke(["claims", "status", "--gate"], kctx)
    assert result.exit_code == 1
    assert "unchecked" in result.output


def test_gate_fails_on_stale(tmp_path):
    state = StateManager(tmp_path / "klemma.db")
    _seed(state, "paper.md", ["ok"])
    state.mark_claims_stale("paper.md", set())
    kctx = _make_kctx(tmp_path, state=state)

    result = _invoke(["claims", "status", "--gate"], kctx)
    assert result.exit_code == 1
    assert "stale" in result.output


def test_gate_fails_on_hard_warn_default(tmp_path):
    state = StateManager(tmp_path / "klemma.db")
    _seed(state, "paper.md", ["ok", "hard_warn"])
    kctx = _make_kctx(tmp_path, state=state)

    result = _invoke(["claims", "status", "--gate"], kctx)
    assert result.exit_code == 1


def test_gate_soft_warn_passes_default_fails_strict(tmp_path):
    state = StateManager(tmp_path / "klemma.db")
    _seed(state, "paper.md", ["ok", "soft_warn"])
    kctx = _make_kctx(tmp_path, state=state)

    assert _invoke(["claims", "status", "--gate"], kctx).exit_code == 0
    assert _invoke(
        ["claims", "status", "--gate", "--fail-on", "soft_warn"], kctx
    ).exit_code == 1


def test_gate_empty_ledger_fails(tmp_path):
    state = StateManager(tmp_path / "klemma.db")
    kctx = _make_kctx(tmp_path, state=state)

    assert _invoke(["claims", "status"], kctx).exit_code == 0
    assert _invoke(["claims", "status", "--gate"], kctx).exit_code == 1


def test_gate_scoped_to_target_file(tmp_path):
    state = StateManager(tmp_path / "klemma.db")
    _seed(state, "clean.md", ["ok"])
    _seed(state, "dirty.md", ["hard_warn"])
    kctx = _make_kctx(tmp_path, state=state)

    clean = _invoke(["claims", "status", "--gate", str(tmp_path / "clean.md")], kctx)
    assert clean.exit_code == 0, clean.output
    assert "dirty.md" not in clean.output

    dirty = _invoke(["claims", "status", "--gate", str(tmp_path / "dirty.md")], kctx)
    assert dirty.exit_code == 1
