"""Monolith → three-tier migration (plan C2): every field, ledger, dry-run parity."""

from __future__ import annotations

import csv
import struct

from klemma.hashing import compute_content_hash
from klemma.migration import legacy_attempt_id, migrate_monolith
from klemma.state import StateManager
from klemma.stores.paper_store import LocalPaperStore
from klemma.stores.project_store import LocalProjectStore
from klemma.stores.user_library import LocalUserLibrary


def _monolith(tmp_path):
    """Two sources: one with 3 fragments (one duplicate), sections, spans and
    embeddings; one with none (must still land in project_sources)."""
    state = StateManager(tmp_path / "klemma.db")
    state.register_sources(["alpha2020", "empty2021"])
    state.update_source_info("alpha2020", title="Alpha paper", authors="A", year=2020, doi="10.1/a")
    state.save_fragments("alpha2020", [
        {"text": "First claim.", "type": "quote", "chapter": 2, "section": "2.4.1",
         "relevance": 5, "usage_hint": "h1", "page": 3, "verbatim": True},
        {"text": "Second claim.", "type": "key_idea", "chapter": 2, "section": "2.4.2",
         "relevance": 4, "usage_hint": "h2", "page": 4, "verbatim": False},
        {"text": "First claim.", "type": "quote", "page": 3},  # INSERT OR IGNORE dup
    ])
    rows = state.get_fragments(source_id="alpha2020", limit=100)
    first = next(r for r in rows if r["fragment_text"] == "First claim.")
    state.update_fragment_provenance(first["id"], verbatim=True, char_start=10, char_end=22,
                                     source_locator="с. 3")
    state.save_fragment_embedding(first["id"], [0.1, 0.2, 0.3], "bge-m3")
    with state._conn() as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS source_sections (source_id TEXT, chapter INTEGER, section TEXT, section_type TEXT, PRIMARY KEY (source_id, section))")
        conn.execute("INSERT OR IGNORE INTO source_sections (source_id, chapter, section) VALUES ('alpha2020', 2, '2.4')")
        # a second monolith row with the same (text, page) — simulate an old duplicate
        conn.execute(
            "INSERT OR IGNORE INTO fragments (source_id, fragment_text, fragment_type, page_number) "
            "VALUES ('alpha2020', 'Second claim. ', 'key_idea', 4)"
        )
    return tmp_path / "klemma.db"


def _stores(tmp_path):
    lib = tmp_path / "library.db"
    return LocalPaperStore(lib), LocalUserLibrary(lib), LocalProjectStore(tmp_path / "project.db")


def test_dry_run_and_apply_report_the_same_numbers(tmp_path):
    mono = _monolith(tmp_path)
    ps, ul, pj = _stores(tmp_path)
    dry = migrate_monolith(mono, paper_store=ps, user_library=ul, project_store=pj, apply=False)
    assert dry.dry_run and pj.count_project_fragments() == 0 and ul.count() == 0
    live = migrate_monolith(
        mono, paper_store=ps, user_library=ul, project_store=pj, apply=True,
        ledger_path=tmp_path / "ledger.csv",
    )
    for attr in ("n_input", "n_unique_fragments", "n_attempt_fragment", "n_project_fragment",
                 "n_embedding", "sources_total", "sources_with_fragments", "n_sections"):
        assert getattr(dry, attr) == getattr(live, attr), attr
    assert live.n_input == 3          # 2 distinct + 'Second claim. ' variant
    assert live.n_unique_fragments == 3
    assert live.sources_total == 2 and live.sources_with_fragments == 1
    assert live.n_embedding == 1 and live.n_sections == 1
    assert live.verified["project_fragments"] == 3
    assert live.verified["user_sources"] == 2 and live.verified["project_sources"] == 2


def test_every_field_is_transferred_and_ledger_lists_every_row(tmp_path):
    mono = _monolith(tmp_path)
    ps, ul, pj = _stores(tmp_path)
    rep = migrate_monolith(mono, paper_store=ps, user_library=ul, project_store=pj, apply=True,
                           ledger_path=tmp_path / "ledger.csv")
    for col, (i, o) in rep.field_transfer.items():
        assert i == o, f"{col}: {o}/{i}"
    with open(tmp_path / "ledger.csv", encoding="utf-8") as fh:
        ledger = list(csv.DictReader(fh))
    assert len(ledger) == rep.n_input
    assert {r["status"] for r in ledger} == {"migrated"}

    paper_id = ul.resolve_paper_id("alpha2020")
    fid = compute_content_hash(paper_id, "First claim.", 3)
    rows = {r["fragment_id"]: r for r in pj.get_project_fragments("alpha2020")}
    assert rows[fid]["legacy_section"] == "2.4.1" and rows[fid]["section_origin"] == "legacy_unknown"
    assert rows[fid]["relevance_score"] == 5 and rows[fid]["usage_hint"] == "h1"
    assert pj.get_active_run_id("alpha2020") is None  # legacy stays active until a run publishes

    att = legacy_attempt_id(paper_id, "alpha2020")
    links = {r["fragment_id"]: r for r in ps.get_attempt_fragments(att)}
    assert links[fid]["char_start"] == 10 and links[fid]["source_locator"] == "с. 3"
    assert links[fid]["verbatim_status"] == "confirmed"
    assert ps.get_attempt(att)["mode"] == "legacy"
    emb = ps.get_fragment_embeddings(paper_id, "bge-m3")
    assert [round(x, 3) for x in emb[fid]] == [0.1, 0.2, 0.3]
    assert ul.get_source_by_citekey("empty2021") is not None
    assert pj.get_source_sections("alpha2020") == ["2.4"]


def test_migration_is_idempotent_and_merges_by_existing_citekey(tmp_path):
    mono = _monolith(tmp_path)
    ps, ul, pj = _stores(tmp_path)
    # A previous (old-style) migration already created a synthetic paper for alpha2020.
    pre = ps.register_paper(title="Alpha paper", pdf_hash="migrated:alpha2020")
    ul.add_source(pre, "alpha2020", status="completed")
    r1 = migrate_monolith(mono, paper_store=ps, user_library=ul, project_store=pj, apply=True)
    assert r1.papers_matched.get("citekey") == 1 and ul.resolve_paper_id("alpha2020") == pre
    r2 = migrate_monolith(mono, paper_store=ps, user_library=ul, project_store=pj, apply=True)
    assert r2.n_project_fragment == r1.n_project_fragment
    assert pj.count_project_fragments() == 3 and len(ps.get_fragments(pre)) == 3
    assert len(ps.get_attempts(pre)) == 1


def test_real_pdf_hash_upgrades_synthetic_paper(tmp_path):
    mono = _monolith(tmp_path)
    ps, ul, pj = _stores(tmp_path)
    pdf = tmp_path / "alpha.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")
    from klemma.hashing import compute_pdf_hash

    real = compute_pdf_hash(pdf)
    pre = ps.register_paper(title="Alpha paper", pdf_hash="migrated:alpha2020")
    rep = migrate_monolith(
        mono, paper_store=ps, user_library=ul, project_store=pj, apply=True,
        pdf_resolver=lambda src: pdf if src["id"] == "alpha2020" else None,
    )
    assert rep.papers_matched.get("migrated") == 1
    assert ps.get_paper_by_id(pre).pdf_hash == real
    assert rep.conflicts == []


def test_embedding_blob_roundtrip_helper():
    blob = struct.pack("3f", 0.5, 0.25, 0.125)
    assert list(struct.unpack("3f", blob)) == [0.5, 0.25, 0.125]
