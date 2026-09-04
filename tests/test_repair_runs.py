"""Plan C2: repair on the three-tier stores (--run, attempt provenance), source select/show."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from klemma.cli import main as klemma_cli
from klemma.hashing import compute_content_hash
from klemma.literature.sidecar import write_pdf_sidecar
from klemma.models import FragmentRecord
from klemma.state import StateManager
from klemma.stores.paper_store import LocalPaperStore
from klemma.stores.project_store import LocalProjectStore
from klemma.stores.user_library import LocalUserLibrary

PAGE = "3.4 Определение требуемой обеспеченности\nОценка оправдываемости положения кромки льда.\n"


def _kctx(tmp_path: Path):
    state = StateManager(tmp_path / "klemma.db")
    lib = tmp_path / "library.db"
    kctx = MagicMock()
    kctx.state = state
    kctx.project_root = tmp_path
    kctx.klemma_home = tmp_path / ".klemma"
    kctx.config.ai.max_pdf_chars = 50000
    kctx.config.zotero.storage_path = str(tmp_path / "storage")
    kctx.library = None
    kctx.paper_store = LocalPaperStore(lib)
    kctx.user_library = LocalUserLibrary(lib)
    kctx.project_store = LocalProjectStore(tmp_path / "project.db")
    kctx.embeddings = None
    return kctx


def _invoke(cmd, args, kctx, module):
    runner = CliRunner()
    with patch(f"klemma.commands.{module}._get_context", return_value=kctx):
        return runner.invoke(klemma_cli, [cmd] + args, catch_exceptions=False)


def test_repair_run_validates_attempt_and_publishes(tmp_path):
    kctx = _kctx(tmp_path)
    ps, pj, ul = kctx.paper_store, kctx.project_store, kctx.user_library
    kctx.state.register_sources(["gost2025"])
    write_pdf_sidecar(tmp_path, "gost2025", [PAGE], {"title": "ГОСТ"})
    pid = ps.register_paper(title="ГОСТ", pdf_hash="h")
    ul.add_source(pid, "gost2025", status="completed")
    quote = "Оценка оправдываемости положения кромки льда."
    fid_ok = compute_content_hash(pid, quote, 1)
    fid_bad = compute_content_hash(pid, "нет такого текста", 1)
    ps.start_attempt("att", pid, ai_model="m")
    ps.save_attempt_fragments("att", pid, [
        FragmentRecord(fragment_id=fid_ok, paper_id=pid, fragment_text=quote, page_number=1, verbatim=True),
        FragmentRecord(fragment_id=fid_bad, paper_id=pid, fragment_text="нет такого текста", page_number=1, verbatim=True),
    ], [{"verbatim_status": "confirmed"}, {"verbatim_status": "confirmed"}])
    run_id = pj.start_run("gost2025", paper_id=pid, attempt_id="att")
    status = pj.publish_run(run_id, [{"fragment_id": fid_ok}, {"fragment_id": fid_bad}],
                            is_partial=False, validation_incomplete=True)
    assert status == "pending" and pj.get_active_run_id("gost2025") is None

    result = _invoke("repair", ["--run", str(run_id)], kctx, "repair")
    assert result.exit_code == 0, result.output
    links = {r["fragment_id"]: r for r in ps.get_attempt_fragments("att")}
    assert links[fid_ok]["verbatim_status"] == "confirmed"
    assert links[fid_ok]["char_start"] is not None and links[fid_ok]["source_locator"] == "п. 3.4"
    assert links[fid_bad]["verbatim_status"] == "downgraded"
    run = pj.get_run(run_id)
    assert run["status"] == "published" and run["validation_incomplete"] == 0
    assert pj.get_active_run_id("gost2025") == run_id
    assert "published" in result.output


def test_repair_verbatim_mirrors_verdicts_into_legacy_attempt(tmp_path):
    from klemma.migration import legacy_attempt_id

    kctx = _kctx(tmp_path)
    state, ps, ul = kctx.state, kctx.paper_store, kctx.user_library
    state.register_sources(["gost2025"])
    quote = "Оценка оправдываемости положения кромки льда."
    state.save_fragments("gost2025", [{"text": quote, "page": 1, "verbatim": False},
                                       {"text": "выдумка", "page": 1, "verbatim": True}])
    write_pdf_sidecar(tmp_path, "gost2025", [PAGE], {"title": "ГОСТ"})
    pid = ps.register_paper(title="ГОСТ", pdf_hash="h")
    ul.add_source(pid, "gost2025", status="completed")

    result = _invoke("repair", ["gost2025", "--steps", "verbatim"], kctx, "repair")
    assert result.exit_code == 0, result.output
    att = legacy_attempt_id(pid, "gost2025")
    links = {r["fragment_text"]: r for r in ps.get_attempt_fragments(att)}
    assert links[quote]["verbatim_status"] == "confirmed" and links[quote]["char_start"] is not None
    assert links["выдумка"]["verbatim_status"] == "downgraded"
    assert ps.get_attempt(att)["mode"] == "legacy"


def test_source_select_filters_and_prints_citekeys(tmp_path):
    kctx = _kctx(tmp_path)
    state = kctx.state
    state.register_sources(["zero", "few", "many", "lowq", "dropped"])
    state.save_fragments("few", [{"text": "a"}, {"text": "b"}])
    state.save_fragments("lowq", [{"text": "a"}])
    state.save_fragments("many", [{"text": str(i)} for i in range(12)])
    with state._conn() as conn:
        conn.execute("UPDATE sources SET quality_score=5 WHERE id IN ('few','many')")
        conn.execute("UPDATE sources SET quality_score=2 WHERE id='lowq'")
        conn.execute("UPDATE sources SET status='completed'")
        conn.execute("UPDATE sources SET title='Retrieval-augmented LLM agents' WHERE id='dropped'")
    result = _invoke("source", ["select", "--max-fragments", "10", "--min-quality", "4",
                                "--exclude-title-regex", "LLM"], kctx, "analyze")
    assert result.exit_code == 0, result.output
    keys = {ln.strip() for ln in result.output.splitlines() if ln.strip() and " " not in ln.strip()}
    assert {"zero", "few"} <= keys and not ({"lowq", "many", "dropped"} & keys)


def test_source_show_lists_runs(tmp_path):
    kctx = _kctx(tmp_path)
    kctx.state.register_sources(["k1"])
    pj = kctx.project_store
    r = pj.start_run("k1", paper_id="p", attempt_id="a", mode="standard", ai_model="m")
    pj.publish_run(r, [{"fragment_id": "f1", "model_section": "2.4"}], is_partial=False,
                   validation_incomplete=False)
    result = _invoke("source", ["show", "k1", "--all-runs"], kctx, "analyze")
    assert result.exit_code == 0, result.output
    assert f"#{r}" in result.output and "published" in result.output
    assert "Project fragments" in result.output and "2.4" in result.output


def test_repair_run_publishes_attempt_consistently(tmp_path):
    """Codex P1: after --run the library attempt mirrors the project status."""
    kctx = _kctx(tmp_path)
    ps, pj, ul = kctx.paper_store, kctx.project_store, kctx.user_library
    kctx.state.register_sources(["gost2025"])
    write_pdf_sidecar(tmp_path, "gost2025", [PAGE], {"title": "ГОСТ"})
    pid = ps.register_paper(title="ГОСТ", pdf_hash="h")
    ul.add_source(pid, "gost2025", status="completed")
    quote = "Оценка оправдываемости положения кромки льда."
    fid = compute_content_hash(pid, quote, 1)
    ps.start_attempt("att", pid)
    ps.save_attempt_fragments("att", pid, [FragmentRecord(fragment_id=fid, paper_id=pid, fragment_text=quote, page_number=1, verbatim=True)], [{"verbatim_status": "confirmed"}])
    ps.finish_attempt("att", status="pending", validation_incomplete=True)
    run_id = pj.start_run("gost2025", paper_id=pid, attempt_id="att")
    pj.publish_run(run_id, [{"fragment_id": fid}], is_partial=False, validation_incomplete=True)
    assert ps.get_fragments(pid) == []  # hidden while pending
    result = _invoke("repair", ["--run", str(run_id)], kctx, "repair")
    assert result.exit_code == 0, result.output
    assert ps.get_attempt("att")["status"] == "published"
    assert len(ps.get_fragments(pid)) == 1


def test_process_rejects_replace_without_force_and_exhaustive(tmp_path):
    kctx = _kctx(tmp_path)
    r = _invoke("process", ["k", "--replace"], kctx, "process")
    assert r.exit_code == 2 and "requires --force" in r.output
    r = _invoke("process", ["k", "--exhaustive"], kctx, "process")
    assert r.exit_code == 2 and "not available" in r.output


def test_process_resume_stale_without_stale_runs_does_nothing(tmp_path):
    kctx = _kctx(tmp_path)
    with patch("klemma.commands.process._init_ai") as init_ai, \
         patch("klemma.commands.process._process_single") as ps_single:
        r = _invoke("process", ["--resume-stale"], kctx, "process")
    assert r.exit_code == 0 and "No stale runs" in r.output
    ps_single.assert_not_called()
    init_ai.assert_not_called()


def test_source_select_default_includes_degraded_and_project_prune(tmp_path):
    kctx = _kctx(tmp_path)
    state = kctx.state
    state.register_sources(["deg", "dropped"])
    with state._conn() as conn:
        conn.execute("UPDATE sources SET status='degraded' WHERE id='deg'")
        conn.execute("UPDATE sources SET status='completed' WHERE id='dropped'")
    kctx.project_store.save_prune_verdicts([{"citekey": "dropped", "reason": "x"}], [])
    r = _invoke("source", ["select"], kctx, "analyze")
    keys = {ln.strip() for ln in r.output.splitlines() if ln.strip() and " " not in ln.strip()}
    assert "deg" in keys and "dropped" not in keys
