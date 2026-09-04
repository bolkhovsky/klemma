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
    assert row["curated_section"] == "3.2.2" and row["section"] == "2.4"
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
