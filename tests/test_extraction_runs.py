"""Plan C2 / ADR-020: extraction attempts (library.db) and project runs (project.db).

Covers: idempotent PaperStore revision, ProjectStore v5→v6 migration, the
publication transaction (atomic, integrity-checked), the active set, curated
sections surviving runs, multi-user isolation, partial/unvalidated states,
explicit activation, stale runs and --replace semantics.
"""

from __future__ import annotations

import sqlite3

import pytest

from klemma.models import FragmentRecord
from klemma.stores.paper_store import LocalPaperStore
from klemma.stores.project_store import LocalProjectStore


def _frag(pid: str, text: str, page: int = 1) -> FragmentRecord:
    from klemma.hashing import compute_content_hash

    fid = compute_content_hash(pid, text, page)
    return FragmentRecord(fragment_id=fid, paper_id=pid, fragment_text=text,
                          page_number=page, content_hash=fid)


# ---------------------------------------------------------------------------
# PaperStore: attempts without touching user_version
# ---------------------------------------------------------------------------


def test_attempt_tables_created_without_bumping_user_version(tmp_path):
    db = tmp_path / "library.db"
    LocalPaperStore(db)
    conn = sqlite3.connect(str(db))
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    conn.close()
    assert {"extraction_attempts", "extraction_attempt_fragments"} <= tables
    assert version == 1  # co-owned with LocalUserLibrary — must not be bumped here
    LocalPaperStore(db)  # idempotent reopen


def test_attempt_lifecycle_and_fragment_links(tmp_path):
    store = LocalPaperStore(tmp_path / "library.db")
    pid = store.register_paper(title="P", pdf_hash="h1")
    store.start_attempt("att-1", pid, request_fingerprint="fp", ai_model="m", mode="standard")
    f1, f2 = _frag(pid, "alpha"), _frag(pid, "beta")
    n = store.save_attempt_fragments("att-1", pid, [f1, f2], [
        {"char_start": 0, "char_end": 5, "source_locator": "с. 1", "verbatim_status": "confirmed"},
        {"char_start": None, "char_end": None, "source_locator": None, "verbatim_status": "unclaimed"},
    ])
    assert n == 2
    store.finish_attempt("att-1", status="published", coverage_json="{}")
    att = store.get_attempt("att-1")
    assert att["status"] == "published" and att["request_fingerprint"] == "fp"
    linked = store.get_attempt_fragments("att-1")
    assert {r["fragment_text"] for r in linked} == {"alpha", "beta"}
    assert linked[0]["char_start"] == 0 and linked[0]["source_locator"] == "с. 1"

    # A second attempt re-linking the same text reuses the canonical row.
    store.start_attempt("att-2", pid, ai_model="m")
    store.save_attempt_fragments("att-2", pid, [f1], [{"char_start": 0, "char_end": 5,
                                                        "source_locator": None,
                                                        "verbatim_status": "confirmed"}])
    assert len(store.get_fragments(pid)) == 2
    assert {a["attempt_id"] for a in store.get_attempts(pid)} == {"att-1", "att-2"}
    assert store.find_orphan_attempts({"att-1"})[0]["attempt_id"] == "att-2"


# ---------------------------------------------------------------------------
# ProjectStore v6 migration
# ---------------------------------------------------------------------------


def _v5_db(path):
    conn = sqlite3.connect(str(path))
    conn.executescript("""
        CREATE TABLE project_sources (
            citekey TEXT NOT NULL, paper_id TEXT NOT NULL, primary_chapter INTEGER,
            primary_section TEXT, relevance_nr1 INTEGER DEFAULT 0, relevance_nr2 INTEGER DEFAULT 0,
            citation_priority TEXT DEFAULT 'medium', added_at TEXT, user_id TEXT NOT NULL DEFAULT '',
            PRIMARY KEY (user_id, citekey));
        CREATE TABLE project_source_sections (
            citekey TEXT NOT NULL, section TEXT NOT NULL, section_type TEXT, chapter INTEGER,
            user_id TEXT NOT NULL DEFAULT '', PRIMARY KEY (user_id, citekey, section));
        CREATE TABLE project_fragments (
            fragment_id TEXT NOT NULL PRIMARY KEY, citekey TEXT, section TEXT, section_type TEXT,
            chapter INTEGER, relevance_score INTEGER DEFAULT 3, usage_hint TEXT,
            used_in_draft INTEGER DEFAULT 0);
        CREATE TABLE prune_verdicts (
            source_id TEXT NOT NULL, verdict TEXT NOT NULL, reason TEXT DEFAULT '',
            updated_at TEXT, user_id TEXT NOT NULL DEFAULT '', PRIMARY KEY (user_id, source_id));
        INSERT INTO project_sources (citekey, paper_id) VALUES ('k1', 'p1');
        INSERT INTO project_fragments (fragment_id, citekey, section, relevance_score, usage_hint)
            VALUES ('f1', 'k1', '2.4.1', 4, 'hint');
        PRAGMA user_version = 5;
    """)
    conn.commit()
    conn.close()


def test_v5_to_v6_migration_keeps_rows_and_marks_legacy(tmp_path):
    db = tmp_path / "project.db"
    _v5_db(db)
    store = LocalProjectStore(db)
    conn = sqlite3.connect(str(db))
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 6
    pk = [r[1] for r in conn.execute("PRAGMA table_info(project_fragments)") if r[5] > 0]
    assert set(pk) == {"user_id", "citekey", "fragment_id"}
    row = conn.execute("SELECT * FROM project_fragments").fetchone()
    cols = [d[0] for d in conn.execute("SELECT * FROM project_fragments").description]
    rec = dict(zip(cols, row))
    assert rec["legacy_section"] == "2.4.1" and rec["section_origin"] == "legacy_unknown"
    assert rec["relevance_score"] == 4 and rec["usage_hint"] == "hint"
    assert "active_run_id" in {r[1] for r in conn.execute("PRAGMA table_info(project_sources)")}
    conn.close()
    LocalProjectStore(db)  # idempotent reopen
    # legacy row is the active set while no run exists
    assert [r["fragment_id"] for r in store.get_project_fragments("k1")] == ["f1"]
    assert store.get_active_run_id("k1") is None


# ---------------------------------------------------------------------------
# Runs: lifecycle, publication, active set
# ---------------------------------------------------------------------------


@pytest.fixture
def pstore(tmp_path):
    return LocalProjectStore(tmp_path / "project.db")


def _publish(store, citekey, frags, *, partial=False, incomplete=False, verify=None, **kw):
    run_id = store.start_run(citekey, paper_id="p1", attempt_id=f"att-{citekey}-{len(frags)}",
                             mode="standard", ai_model="m")
    status = store.publish_run(
        run_id, [{"fragment_id": f, "model_section": "2.4", "relevance_score": 3} for f in frags],
        is_partial=partial, validation_incomplete=incomplete, verify_fragment=verify, **kw,
    )
    return run_id, status


def test_start_run_exists_before_any_publish(pstore):
    run_id = pstore.start_run("k1", paper_id="p1", attempt_id="a", request_fingerprint="fp",
                              prompt_hash="ph", ai_model="m", extractor_version="1")
    run = pstore.get_run(run_id)
    assert run["status"] == "running" and run["request_fingerprint"] == "fp"
    assert run["extractor_version"] == "1"
    pstore.fail_run(run_id, "budget", tokens_in=10)
    assert pstore.get_run(run_id)["status"] == "failed"
    assert pstore.get_run(run_id)["error"] == "budget"


def test_publish_switches_active_set_and_keeps_history(pstore):
    pstore.upsert_legacy_fragment("k1", "legacy-1", section="1.1")
    r1, s1 = _publish(pstore, "k1", ["f1", "f2"])
    assert s1 == "published" and pstore.get_active_run_id("k1") == r1
    assert {r["fragment_id"] for r in pstore.get_project_fragments("k1")} == {"f1", "f2"}
    r2, s2 = _publish(pstore, "k1", ["f2", "f3"])
    assert pstore.get_active_run_id("k1") == r2
    assert {r["fragment_id"] for r in pstore.get_project_fragments("k1")} == {"f2", "f3"}
    # history: old run snapshot intact, legacy row still present, all_runs lists links
    assert {r["fragment_id"] for r in pstore.get_project_fragments("k1", run_id=r1)} == {"f1", "f2"}
    all_rows = {r["fragment_id"]: r["run_ids"] for r in pstore.get_project_fragments("k1", all_runs=True)}
    assert set(all_rows) == {"legacy-1", "f1", "f2", "f3"}
    assert all_rows["legacy-1"] is None and set(all_rows["f2"].split(",")) == {str(r1), str(r2)}


def test_publish_is_atomic_on_integrity_failure(pstore):
    pstore.upsert_legacy_fragment("k1", "legacy-1")
    run_id = pstore.start_run("k1", paper_id="p1", attempt_id="a")
    with pytest.raises(RuntimeError):
        pstore.publish_run(
            run_id, [{"fragment_id": "ok"}, {"fragment_id": "missing"}],
            is_partial=False, validation_incomplete=False,
            verify_fragment=lambda fid: fid != "missing",
        )
    assert pstore.get_run_fragments(run_id) == []
    assert pstore.get_run(run_id)["status"] == "failed"
    assert pstore.get_run(run_id)["error"].startswith("integrity")
    assert pstore.get_active_run_id("k1") is None
    assert [r["fragment_id"] for r in pstore.get_project_fragments("k1")] == ["legacy-1"]


def test_partial_and_unvalidated_runs_stay_pending(pstore):
    r_partial, s = _publish(pstore, "k1", ["f1"], partial=True)
    assert s == "pending" and pstore.get_active_run_id("k1") is None
    r_unval, s = _publish(pstore, "k2", ["f1"], incomplete=True)
    assert s == "pending"
    r_both, s = _publish(pstore, "k3", ["f1"], partial=True, incomplete=True)
    assert s == "pending"
    # activate_partial refused while validation incomplete
    with pytest.raises(RuntimeError):
        pstore.activate_partial(r_both, "reviewed by hand")
    # clearing validation on the partial+unvalidated run keeps it pending
    assert pstore.clear_validation_incomplete(r_both) == "pending"
    pstore.activate_partial(r_both, "reviewed by hand")
    run = pstore.get_run(r_both)
    assert run["status"] == "published_partial" and run["activation_reason"] == "reviewed by hand"
    assert pstore.get_active_run_id("k3") == r_both
    # clearing validation on a complete run publishes it
    assert pstore.clear_validation_incomplete(r_unval) == "published"
    assert pstore.get_active_run_id("k2") == r_unval
    # a published run cannot be "activated" as partial
    with pytest.raises(RuntimeError):
        pstore.activate_partial(r_unval, "x")
    with pytest.raises(ValueError):
        pstore.activate_partial(r_partial, "   ")


def test_curated_section_survives_reruns_and_is_user_scoped(pstore):
    _publish(pstore, "k1", ["f1"])
    assert pstore.set_curated_section("k1", "f1", "3.2.2")
    row = pstore.get_project_fragments("k1")[0]
    assert row["curated_section"] == "3.2.2" and row["section_origin"] == "curated"
    _publish(pstore, "k1", ["f1"])  # rerun rewrites model section, not the curated one
    row = pstore.get_project_fragments("k1")[0]
    # effective section = curated; the model's own value is exposed separately
    assert row["curated_section"] == "3.2.2" and row["section"] == "3.2.2"
    assert row["run_model_section"] == "2.4"
    assert row["section_origin"] == "curated"
    # another user of the same project.db does not see the curation
    r_other = pstore.start_run("k1", user_id="u2", paper_id="p1", attempt_id="b")
    pstore.publish_run(r_other, [{"fragment_id": "f1"}], is_partial=False,
                       validation_incomplete=False)
    other = pstore.get_project_fragments("k1", user_id="u2")[0]
    assert other["curated_section"] is None
    assert pstore.get_active_run_id("k1") != pstore.get_active_run_id("k1", user_id="u2")


def test_replace_drops_only_legacy_project_rows(pstore):
    pstore.upsert_legacy_fragment("k1", "legacy-1")
    r1, _ = _publish(pstore, "k1", ["f1"])
    _publish(pstore, "k1", ["f2"], replace_legacy=True)
    ids = {r["fragment_id"] for r in pstore.get_project_fragments("k1", all_runs=True)}
    assert "legacy-1" not in ids and {"f1", "f2"} <= ids  # run-linked rows survive


def test_stale_running_rows_are_failed(pstore):
    run_id = pstore.start_run("k1", paper_id="p1", attempt_id="a")
    with pstore._conn() as conn:
        conn.execute(
            "UPDATE project_extraction_runs SET started_at=datetime('now', '-5 hours') WHERE run_id=?",
            (run_id,),
        )
    fresh = pstore.start_run("k2", paper_id="p1", attempt_id="b")
    assert pstore.mark_stale_runs(2.0) == 1
    assert pstore.get_run(run_id)["error"] == "stale"
    assert pstore.get_run(fresh)["status"] == "running"


# ---------------------------------------------------------------------------
# End-to-end: _process_single publishes a run through the real stores
# ---------------------------------------------------------------------------


def test_process_single_publishes_run_and_switches_active_set(tmp_path):
    from unittest.mock import MagicMock, patch

    from klemma.cli import _process_single
    from klemma.literature.models import ExtractionResult, Fragment
    from klemma.state import StateManager
    from klemma.stores.user_library import LocalUserLibrary

    state = StateManager(tmp_path / "klemma.db")
    state.register_sources(["k1"])
    lib = tmp_path / "library.db"
    paper_store, user_library = LocalPaperStore(lib), LocalUserLibrary(lib)
    project_store = LocalProjectStore(tmp_path / "project.db")
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")
    pages = ["Page one prose. " * 20, "Page two prose. " * 20]

    cfg = MagicMock()
    cfg.ai.max_pdf_chars = 50000
    cfg.ai.model = "test-model"
    cfg.ai.chunk_size, cfg.ai.chunk_overlap, cfg.ai.min_chunk_chars = 25000, 2000, 4000
    cfg.zotero.storage_path = str(tmp_path / "storage")
    cfg.processing.min_pdf_length = 10
    pdf_extractor = MagicMock()
    pdf_extractor.find_pdf.return_value = pdf
    pdf_extractor.extract_pages.return_value = pages
    pdf_extractor.format_for_ai.return_value = "\n".join(pages)
    library = MagicMock()
    library.entries.get.return_value = None
    library.pdf_paths = {}

    def _result(texts, failed=0):
        return ExtractionResult(
            source_id="k1", fragments=[Fragment(text=t, section="2.4", verbatim=True) for t in texts],
            chunk_total=2, failed_chunks=failed, coverage_ratio=1.0 if not failed else 0.5,
            spans=[(0, 10)] * len(texts), verbatim_statuses=["confirmed"] * len(texts),
        )

    def _run(result, **kw):
        with (
            patch("klemma.skills.extractor.extract_fragments", return_value=result),
            patch("klemma.skills.extractor.save_fragments_to_vault", return_value=None),
            patch("klemma.literature.metadata.lookup_s2", return_value=None),
        ):
            return _process_single(
                citekey="k1", cfg=cfg, state=state, vault=MagicMock(), ai=MagicMock(),
                pdf_extractor=pdf_extractor, library=library, quiet=True, klemma_home=None,
                paper_store=paper_store, user_library=user_library,
                project_store=project_store, **kw,
            )

    assert _run(_result(["alpha", "beta"])) == (2, "ok")
    run1 = project_store.get_active_run_id("k1")
    assert run1 is not None
    r = project_store.get_run(run1)
    assert r["status"] == "published" and r["attempt_id"] and r["request_fingerprint"]
    paper_id = user_library.resolve_paper_id("k1")
    assert paper_id and paper_store.get_attempt(r["attempt_id"])["status"] == "published"
    assert {x["fragment_text"] for x in paper_store.get_attempt_fragments(r["attempt_id"])} == {"alpha", "beta"}
    assert len(project_store.get_project_fragments("k1")) == 2

    # partial rerun → pending, active set unchanged, monolith merged not replaced
    assert _run(_result(["gamma"], failed=1), force=True) == (1, "ok")
    assert project_store.get_active_run_id("k1") == run1
    runs = project_store.get_runs("k1")
    assert runs[-1]["status"] == "pending" and runs[-1]["is_partial"] == 1
    # (extract_fragments is mocked, so the monolith mirror is not exercised here)
    all_ids = {x["fragment_id"] for x in project_store.get_project_fragments("k1", all_runs=True)}
    assert len(all_ids) == 3  # alpha, beta kept; gamma added under the pending run

    # complete rerun with --replace → new active set; legacy-only project rows dropped
    project_store.upsert_legacy_fragment("k1", "legacy-only")
    assert _run(_result(["delta"]), force=True, replace=True) == (1, "ok")
    run3 = project_store.get_active_run_id("k1")
    assert run3 not in (None, run1)
    ids = {x["fragment_id"] for x in project_store.get_project_fragments("k1", all_runs=True)}
    assert "legacy-only" not in ids
    assert [x["fragment_text"] for x in paper_store.get_attempt_fragments(project_store.get_run(run3)["attempt_id"])] == ["delta"]


# ---------------------------------------------------------------------------
# Codex review on PR-B (#447)
# ---------------------------------------------------------------------------


def test_legacy_set_keeps_fragments_linked_to_pending_runs(pstore):
    """P1: a pending rerun overlapping legacy rows must not hide them."""
    pstore.upsert_legacy_fragment("k1", "f1")
    pstore.upsert_legacy_fragment("k1", "f2")
    _publish(pstore, "k1", ["f1", "f9"], partial=True)  # pending, links f1
    ids = {r["fragment_id"] for r in pstore.get_project_fragments("k1")}
    assert {"f1", "f2"} <= ids


def test_active_set_reports_active_run_snapshot_not_pending_overwrite(pstore):
    """P1: a pending rerun rewriting the shared project row must not change
    what the active corpus reports."""
    r1 = pstore.start_run("k1", paper_id="p1", attempt_id="a1")
    pstore.publish_run(r1, [{"fragment_id": "f1", "model_section": "2.4", "relevance_score": 5,
                              "usage_hint": "orig"}], is_partial=False, validation_incomplete=False)
    r2 = pstore.start_run("k1", paper_id="p1", attempt_id="a2")
    pstore.publish_run(r2, [{"fragment_id": "f1", "model_section": "9.9", "relevance_score": 1,
                              "usage_hint": "changed"}], is_partial=True, validation_incomplete=False)
    assert pstore.get_active_run_id("k1") == r1
    row = pstore.get_project_fragments("k1")[0]
    assert row["section"] == "2.4" and row["relevance_score"] == 5 and row["usage_hint"] == "orig"
    pstore.set_curated_section("k1", "f1", "3.2.2")
    assert pstore.get_project_fragments("k1")[0]["section"] == "3.2.2"


def test_activation_updates_project_source_paper_id(pstore):
    pstore.ensure_source("k1", "synthetic-paper")
    r = pstore.start_run("k1", paper_id="real-paper", attempt_id="a")
    pstore.publish_run(r, [{"fragment_id": "f1"}], is_partial=False, validation_incomplete=False)
    with pstore._conn() as conn:
        pid = conn.execute("SELECT paper_id FROM project_sources WHERE citekey='k1'").fetchone()[0]
    assert pid == "real-paper"


def test_ensure_source_keeps_sections(pstore):
    pstore.set_source_sections("k1", "p", ["2.4"], [2])
    pstore.ensure_source("k1", "p2")
    assert pstore.get_source_sections("k1") == ["2.4"]


def test_stale_cleanup_reports_attempts_and_stranded_runs(pstore):
    run_id = pstore.start_run("k1", paper_id="p1", attempt_id="att-stale")
    with pstore._conn() as conn:
        conn.execute("UPDATE project_extraction_runs SET started_at=datetime('now', '-5 hours') WHERE run_id=?", (run_id,))
    rows = pstore.mark_stale_runs_detailed(2.0)
    assert rows[0]["attempt_id"] == "att-stale"
    assert [s["run_id"] for s in pstore.stranded_attempt_ids()] == [run_id]


def test_paper_store_hides_pending_attempt_fragments_from_cache(tmp_path):
    store = LocalPaperStore(tmp_path / "library.db")
    pid = store.register_paper(title="P", pdf_hash="h")
    ok, pend = _frag(pid, "published text"), _frag(pid, "pending text")
    store.start_attempt("a-ok", pid)
    store.save_attempt_fragments("a-ok", pid, [ok], [{"verbatim_status": "confirmed"}])
    store.finish_attempt("a-ok", status="published")
    store.start_attempt("a-pend", pid)
    store.save_attempt_fragments("a-pend", pid, [pend], [{"verbatim_status": "confirmed"}])
    store.finish_attempt("a-pend", status="pending")
    assert {f.fragment_text for f in store.get_fragments(pid)} == {"published text"}
    assert {f.fragment_text for f in store.get_fragments(pid, published_only=False)} == {"published text", "pending text"}
    store.finish_attempt("a-pend", status="published")
    assert len(store.get_fragments(pid)) == 2


def test_publish_finalizes_identity_from_rendered_prompt_and_model(tmp_path):
    from types import SimpleNamespace

    from klemma import extraction_runs as er
    from klemma.literature.models import Fragment

    lib = tmp_path / "library.db"
    ps = LocalPaperStore(lib)
    pj = LocalProjectStore(tmp_path / "project.db")
    pid = ps.register_paper(title="P", pdf_hash="h")
    cfg = SimpleNamespace(model="configured-model", chunk_size=25000, chunk_overlap=2000,
                          min_chunk_chars=4000, max_tokens_cap=8192, budget_max_input_tokens=0,
                          budget_max_output_tokens=0, budget_max_cost_usd=None, language="ru")
    h = er.start_run(project_store=pj, paper_store=ps, citekey="k1", paper_id=pid,
                     pages=["text"], config_ai=cfg, prompt_name="extract.md",
                     template_hash="tmpl", klemma_version="0.19")
    before = pj.get_run(h.run_id)["request_fingerprint"]
    result = SimpleNamespace(fragments=[Fragment(text="x", verbatim=True)], spans=[(0, 1)],
                             verbatim_statuses=["confirmed"], source_locators=[None],
                             failed_chunks=0, coverage_ratio=1.0, validation_incomplete=False,
                             chunk_total=1, tokens_in=1, tokens_out=1, cost_usd=None,
                             rendered_prompt_hash="rendered123", model="routed-model")
    assert er.publish_run(project_store=pj, paper_store=ps, handle=h, result=result) == "published"
    run = pj.get_run(h.run_id)
    assert run["prompt_hash"] == "rendered123" and run["ai_model"] == "routed-model"
    assert run["request_fingerprint"] != before
    att = ps.get_attempt(h.attempt_id)
    assert att["ai_model"] == "routed-model" and att["request_fingerprint"] == run["request_fingerprint"]
