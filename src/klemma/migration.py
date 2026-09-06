"""Monolith → three-tier migration with a per-row ledger (plan C2 / ADR-020).

Replaces the two older copies (``klemma migrate-library`` and the auto-migration
in ``cli._init_components``) which created ``project_fragments`` never, moved
only text/type/page/intent, lost relevance / usage_hint / section / verbatim /
spans / embeddings, and minted a synthetic ``migrated:<citekey>`` paper for
every source.

What this version does, in ``dry_run`` exactly like in ``apply`` (only the
writes are skipped):

* **paper identity** — real ``pdf_hash`` when the PDF is on disk, else DOI,
  else the citekey already registered in the user library, else the
  synthetic ``migrated:<citekey>`` paper if one exists, else a new paper
  (synthetic hash, flagged in the report);
* **fragments** — ``compute_content_hash(paper_id, text, page)`` as in the
  code; duplicates *inside one source* collapse (ledger status
  ``merged-duplicate``); every input row lands in the ledger;
* **legacy attempt** — one ``extraction_attempt`` per (paper, citekey) with a
  collision-safe id, links carry monolith spans/locators;
* **project rows** — ``project_fragments`` with ``legacy_section`` and
  ``section_origin='legacy_unknown'``, relevance, usage_hint, used_in_draft;
  ``project_sources`` for every source (also those without fragments);
* **embeddings** — monolith vectors go to ``fragment_embeddings`` keyed by
  the new fragment id and the stored model name;
* **report** — the N_* numbers the acceptance criteria are checked against
  plus per-field transfer counts and conflicts.
"""

from __future__ import annotations

import csv
import hashlib
import logging
import sqlite3
import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from .hashing import compute_content_hash, compute_pdf_hash
from .models import FragmentRecord

logger = logging.getLogger(__name__)

LEDGER_COLUMNS = (
    "mono_fragment_id", "citekey", "paper_id", "fragment_id", "status", "note",
)


@dataclass
class MigrationReport:
    dry_run: bool = True
    n_input: int = 0
    n_unique_fragments: int = 0
    n_attempt_fragment: int = 0
    n_project_fragment: int = 0
    n_embedding: int = 0
    sources_total: int = 0
    sources_with_fragments: int = 0
    n_sections: int = 0
    papers_matched: dict[str, int] = field(default_factory=dict)  # by: pdf_hash|doi|citekey|migrated|new
    field_transfer: dict[str, tuple[int, int]] = field(default_factory=dict)  # col → (in, out)
    conflicts: list[str] = field(default_factory=list)
    ledger: list[dict] = field(default_factory=list)
    # post-apply verification (actual store counts)
    verified: dict[str, int] = field(default_factory=dict)

    def summary_lines(self) -> list[str]:
        lines = [
            f"N_input={self.n_input} N_unique_fragments={self.n_unique_fragments} "
            f"N_attempt_fragment={self.n_attempt_fragment} N_project_fragment={self.n_project_fragment} "
            f"N_embedding={self.n_embedding}",
            f"sources={self.sources_total} with_fragments={self.sources_with_fragments} "
            f"papers_matched={dict(sorted(self.papers_matched.items()))}",
        ]
        for col, (i, o) in sorted(self.field_transfer.items()):
            lines.append(f"  field {col}: {o}/{i} transferred" + ("" if o == i else "  ← LOSS"))
        for c in self.conflicts:
            lines.append(f"  conflict: {c}")
        if self.verified:
            lines.append(f"verified={dict(sorted(self.verified.items()))}")
        return lines


def legacy_attempt_id(paper_id: str, citekey: str) -> str:
    """Collision-safe id for the synthetic attempt holding a source's legacy fragments."""
    return "legacy-" + hashlib.sha256(f"legacy|{paper_id}|{citekey}".encode("utf-8")).hexdigest()[:32]


def _read_monolith(mono_db: Path) -> tuple[list[dict], list[dict], dict[str, list[str]]]:
    conn = sqlite3.connect(str(mono_db))
    conn.row_factory = sqlite3.Row
    try:
        sources = [dict(r) for r in conn.execute("SELECT * FROM sources ORDER BY rowid")]
        fragments = [
            dict(r) | {"id": r["rowid"] if "id" not in r.keys() else r["id"]}
            for r in conn.execute("SELECT rowid, * FROM fragments ORDER BY rowid")
        ]
        has_sec = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='source_sections'"
        ).fetchone() is not None
        sections: dict[str, list[str]] = {}
        if has_sec:
            for r in conn.execute("SELECT source_id, section FROM source_sections ORDER BY rowid"):
                sections.setdefault(r["source_id"], []).append(r["section"])
    finally:
        conn.close()
    return sources, fragments, sections


def _section_chapter(sec: str) -> Optional[int]:
    try:
        return int(str(sec).split(".")[0])
    except (ValueError, IndexError, AttributeError):
        return None


def resolve_paper(
    src: dict,
    *,
    paper_store,
    user_library,
    pdf_resolver: Optional[Callable[[dict], Optional[Path]]],
    user_id: Optional[str],
    apply: bool,
    report: MigrationReport,
) -> tuple[str, str]:
    """Return (paper_id, matched_by). In dry-run an unresolved source gets a
    placeholder id ``NEW:<citekey>`` so uniqueness numbers are identical."""
    citekey = src["id"]
    pdf_hash = None
    if pdf_resolver is not None:
        try:
            pdf_path = pdf_resolver(src)
            if pdf_path and Path(pdf_path).exists():
                pdf_hash = compute_pdf_hash(Path(pdf_path))
        except Exception as exc:  # noqa: BLE001
            report.conflicts.append(f"{citekey}: pdf hash failed: {exc}")

    if pdf_hash:
        rec = paper_store.find_paper(pdf_hash=pdf_hash)
        if rec:
            return rec.paper_id, "pdf_hash"
    doi = (src.get("doi") or "").strip() or None
    if doi:
        rec = paper_store.find_paper(doi=doi)
        if rec:
            if pdf_hash and rec.pdf_hash and rec.pdf_hash != pdf_hash and not rec.pdf_hash.startswith("migrated:"):
                report.conflicts.append(
                    f"{citekey}: DOI matches paper {rec.paper_id} with a different pdf_hash"
                )
            elif pdf_hash and apply and (not rec.pdf_hash or rec.pdf_hash.startswith("migrated:")):
                paper_store.set_pdf_hash(rec.paper_id, pdf_hash)
            return rec.paper_id, "doi"
    existing = user_library.resolve_paper_id(citekey, user_id=user_id) if user_library else None
    if existing:
        rec = paper_store.get_paper_by_id(existing)
        if rec is not None:
            if pdf_hash and apply and (not rec.pdf_hash or rec.pdf_hash.startswith("migrated:")):
                other = paper_store.find_paper(pdf_hash=pdf_hash)
                if other is None:
                    paper_store.set_pdf_hash(rec.paper_id, pdf_hash)
            return rec.paper_id, "citekey"
    rec = paper_store.find_paper(pdf_hash=f"migrated:{citekey}")
    if rec:
        if pdf_hash and apply and paper_store.find_paper(pdf_hash=pdf_hash) is None:
            paper_store.set_pdf_hash(rec.paper_id, pdf_hash)
        return rec.paper_id, "migrated"
    if not apply:
        return f"NEW:{citekey}", "new"
    paper_id = paper_store.register_paper(
        title=src.get("title") or citekey,
        authors=src.get("authors") or "",
        year=src.get("year"),
        doi=doi,
        abstract=src.get("abstract") or "",
        pdf_hash=pdf_hash or f"migrated:{citekey}",
    )
    return paper_id, "new"


def migrate_monolith(
    mono_db: Path,
    *,
    paper_store,
    user_library,
    project_store,
    apply: bool,
    pdf_resolver: Optional[Callable[[dict], Optional[Path]]] = None,
    ledger_path: Optional[Path] = None,
    user_id: Optional[str] = None,
    progress: Optional[Callable[[str], None]] = None,
) -> MigrationReport:
    """Migrate every source and fragment of ``mono_db`` into the three stores.

    Idempotent: re-running re-links the same content-hash rows and upserts
    project rows; nothing is deleted. ``dry_run`` (apply=False) computes the
    same report without writing.
    """
    report = MigrationReport(dry_run=not apply)
    sources, fragments, sections = _read_monolith(mono_db)
    report.sources_total = len(sources)
    report.n_input = len(fragments)
    by_source: dict[str, list[dict]] = {}
    for f in fragments:
        by_source.setdefault(f["source_id"], []).append(f)
    report.sources_with_fragments = sum(1 for s in sources if by_source.get(s["id"]))
    report.n_sections = sum(len(v) for v in sections.values())

    tracked = ("section", "relevance_score", "usage_hint", "chapter", "verbatim",
               "char_start", "citation_intent", "page_number", "embedding")
    field_in = {c: 0 for c in tracked}
    field_out = {c: 0 for c in tracked}

    unique_fragment_ids: set[str] = set()
    attempt_links = 0
    project_rows = 0
    embeddings = 0

    for idx, src in enumerate(sources, 1):
        citekey = src["id"]
        if progress and idx % 50 == 0:
            progress(f"{idx}/{len(sources)} sources")
        paper_id, matched = resolve_paper(
            src, paper_store=paper_store, user_library=user_library,
            pdf_resolver=pdf_resolver, user_id=user_id, apply=apply, report=report,
        )
        report.papers_matched[matched] = report.papers_matched.get(matched, 0) + 1

        if apply:
            user_library.add_source(
                paper_id, citekey,
                status=src.get("status") or "pending",
                pdf_path=src.get("pdf_path"),
                note_path=src.get("note_path"),
                quality_score=src.get("quality_score"),
                user_id=user_id,
            )
        secs = sections.get(citekey, [])
        chaps = [c for c in (_section_chapter(s) for s in secs) if c is not None]
        if apply:
            if secs:
                project_store.set_source_sections(citekey, paper_id, secs, chaps, user_id=user_id)
            else:
                # Register without replacing assignments: a rerun must not wipe
                # sections curated in project.db after the first migration.
                project_store.ensure_source(citekey, paper_id, user_id=user_id)

        rows = by_source.get(citekey, [])
        seen_in_source: set[str] = set()
        records: list[FragmentRecord] = []
        links: list[dict] = []
        kept_rows: list[dict] = []  # monolith rows aligned with `records`
        vectors: list[tuple[str, list[float], str]] = []
        for f in rows:
            text = f.get("fragment_text") or ""
            page = f.get("page_number")
            fid = compute_content_hash(paper_id, text, page)
            entry = {
                "mono_fragment_id": f.get("id"), "citekey": citekey, "paper_id": paper_id,
                "fragment_id": fid, "status": "migrated", "note": "",
            }
            for col in tracked:
                if f.get(col) not in (None, "", 0) or (col == "verbatim" and f.get(col)):
                    field_in[col] += 1
            if not text.strip():
                entry["status"] = "failed"
                entry["note"] = "empty text"
                report.ledger.append(entry)
                continue
            if fid in seen_in_source:
                entry["status"] = "merged-duplicate"
                entry["note"] = "same (text, page) within source"
                report.ledger.append(entry)
                continue
            seen_in_source.add(fid)
            unique_fragment_ids.add(fid)
            kept_rows.append(f)
            records.append(FragmentRecord(
                fragment_id=fid, paper_id=paper_id, fragment_text=text,
                fragment_type=f.get("fragment_type") or "key_idea", page_number=page,
                citation_intent=f.get("citation_intent"), verbatim=bool(f.get("verbatim")),
                content_hash=fid,
            ))
            links.append({
                "char_start": f.get("char_start"), "char_end": f.get("char_end"),
                "source_locator": f.get("source_locator"),
                "verbatim_status": "confirmed" if f.get("verbatim") else "unverified",
            })
            blob = f.get("embedding")
            if blob:
                try:
                    n = len(blob) // 4
                    vec = list(struct.unpack(f"{n}f", blob))
                    vectors.append((fid, vec, f.get("embedding_model") or "unknown"))
                except (struct.error, TypeError) as exc:
                    entry["note"] = f"embedding unreadable: {exc}"
            for col in tracked:
                if f.get(col) not in (None, "", 0) or (col == "verbatim" and f.get(col)):
                    field_out[col] += 1
            attempt_links += 1
            project_rows += 1
            report.ledger.append(entry)

        if records and apply:
            att = legacy_attempt_id(paper_id, citekey)
            paper_store.start_attempt(
                att, paper_id, prompt_name="legacy", ai_model="legacy", mode="legacy",
                request_fingerprint="", extractor_version="0",
            )
            paper_store.save_attempt_fragments(att, paper_id, records, links)
            paper_store.finish_attempt(att, status="published")
            for rec, f in zip(records, kept_rows):
                project_store.upsert_legacy_fragment(
                    citekey, rec.fragment_id, user_id=user_id,
                    section=f.get("section"), section_type=f.get("section_type"),
                    chapter=f.get("chapter"), relevance_score=int(f.get("relevance_score") or 3),
                    usage_hint=f.get("usage_hint"), used_in_draft=bool(f.get("used_in_draft")),
                )
            for fid, vec, model in vectors:
                paper_store.save_fragment_embedding(fid, vec, model)
        embeddings += len({(fid, model) for fid, _, model in vectors})

    report.n_unique_fragments = len(unique_fragment_ids)
    report.n_attempt_fragment = attempt_links
    report.n_project_fragment = project_rows
    report.n_embedding = embeddings
    report.field_transfer = {c: (field_in[c], field_out[c]) for c in tracked}

    if apply:
        report.verified = {
            "project_fragments": project_store.count_project_fragments(),
            "project_sources": project_store.count_sources(user_id) if user_id is not None else project_store.count_sources(),
            "user_sources": user_library.count(user_id) if user_id is not None else user_library.count(),
        }
    if ledger_path is not None:
        ledger_path.parent.mkdir(parents=True, exist_ok=True)
        with open(ledger_path, "w", encoding="utf-8", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=LEDGER_COLUMNS)
            w.writeheader()
            w.writerows(report.ledger)
    return report
