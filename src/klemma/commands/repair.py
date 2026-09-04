"""klemma repair — backfill the claim-provenance substrate for existing sources.

Historical state (docs/plans/2026-08-13-claim-provenance-gap.md): 98 % of
sources have no saved full text and 0 of 2450 fragments carry an honest
``verbatim`` flag, so the deterministic citation-check layer has nothing to
verify against. This command retrofits already-processed sources:

* step ``sidecar`` — find the PDF, extract pages, write the raw sidecar and
  record ``pdf_text_length`` (skips sources whose sidecar already exists);
* step ``verbatim`` — recompute the ``verbatim`` flag for EVERY fragment of
  the source against the sidecar canonical text, including the downgrade
  ``true→false`` (honesty over "never downgrade"); confirmed fragments get
  a char span into the sidecar plus a human-readable source locator;
* step ``embeddings`` — re-embed the source's missing fragment/source
  vectors; a source that was marked ``degraded`` returns to ``completed``
  once all its recorded failed steps are verifiably fixed.

``--scan`` audits history instead of repairing: completed sources without a
sidecar or with unembedded fragments get flagged as ``degraded`` so
`klemma status` stops presenting them as healthy.

All data mutations are counted and printed — silent repair is not repair.
"""

from __future__ import annotations

import glob as _glob
import json
from dataclasses import dataclass, field
from pathlib import Path

import click

from ..cli import _auto_embed_after_process, _get_context, console, main
from ..literature.locator import derive_locator
from ..literature.sidecar import load_sidecar_doc, write_pdf_sidecar
from ..skills.citation_checker import _CITE_REF_RE, _extract_citekeys_from_ref
from ..skills.extractor import locate_fragment_span

_KNOWN_STEPS = ("sidecar", "verbatim", "embeddings")


@dataclass
class RepairStats:
    """Aggregate counters for one repair run — printed, not just logged."""

    sources_seen: int = 0
    sidecars_written: int = 0
    sidecars_existing: int = 0
    sidecars_no_pdf: int = 0
    fragments_checked: int = 0
    verbatim_confirmed: int = 0
    verbatim_upgraded: int = 0
    verbatim_downgraded: int = 0
    spans_written: int = 0
    locators_written: int = 0
    sources_no_sidecar: int = 0
    embeddings_created: int = 0
    embeddings_failed: int = 0
    degraded_cleared: int = 0
    warnings: list[str] = field(default_factory=list)


def collect_cited_citekeys(patterns: tuple[str, ...]) -> tuple[set[str], list[str]]:
    """Scan markdown files for ``[@citekey]`` references.

    Each pattern is a path or a glob; directories are walked for ``*.md``.
    Returns (citekeys, warnings) — a missing/empty pattern is a warning,
    not an error, so a stale glob doesn't abort the whole run.
    """
    citekeys: set[str] = set()
    warnings: list[str] = []

    files: list[Path] = []
    for pattern in patterns:
        expanded = Path(pattern).expanduser()
        if expanded.is_dir():
            matched = sorted(expanded.rglob("*.md"))
        elif expanded.is_file():
            matched = [expanded]
        else:
            matched = sorted(
                Path(p) for p in _glob.glob(str(expanded), recursive=True)
                if Path(p).is_file()
            )
        if not matched:
            warnings.append(f"--cited {pattern}: no files matched")
        files.extend(f for f in matched if f.suffix == ".md")

    for md in files:
        try:
            text = md.read_text(encoding="utf-8")
        except OSError as exc:
            warnings.append(f"--cited {md}: {exc}")
            continue
        for m in _CITE_REF_RE.finditer(text):
            citekeys.update(_extract_citekeys_from_ref(m.group(1)))

    return citekeys, warnings


def repair_sidecar(
    kctx, citekey: str, pdf_extractor, stats: RepairStats, dry_run: bool,
) -> None:
    """Backfill the raw PDF sidecar + pdf_text_length for one source."""
    project_root = kctx.project_root
    state = kctx.state

    sidecar_path = project_root / ".klemma" / "pdfs" / f"{citekey}.md"
    if sidecar_path.exists():
        stats.sidecars_existing += 1
        return

    source = state.get_source(citekey) or {}
    if source.get("source_type") == "online":
        stats.warnings.append(f"{citekey}: online source — no PDF sidecar")
        return

    entry = kctx.library.entries.get(citekey) if kctx.library else None
    pdf_lookup = kctx.library.pdf_paths if kctx.library else None
    storage = Path(kctx.config.zotero.storage_path or "")
    pdf_path = pdf_extractor.find_pdf(
        citekey,
        [storage],
        entry_title=(entry.title if entry else "") or "",
        direct_path=source.get("pdf_path") or (entry.pdf_path if entry else None),
        pdf_lookup=pdf_lookup,
    )
    if not pdf_path:
        stats.sidecars_no_pdf += 1
        console.print(f"  [yellow]{citekey}: PDF not found — sidecar skipped[/yellow]")
        return

    pages = pdf_extractor.extract_pages(pdf_path)
    if not pages or not any(p.strip() for p in pages):
        stats.warnings.append(f"{citekey}: PDF text extraction returned nothing")
        console.print(f"  [yellow]{citekey}: empty extraction — sidecar skipped[/yellow]")
        return

    if dry_run:
        stats.sidecars_written += 1
        console.print(
            f"  [dim]{citekey}: would write sidecar ({len(pages)} pages)[/dim]"
        )
        return

    write_pdf_sidecar(
        project_root,
        citekey,
        pages,
        {
            "title": (entry.title if entry else None) or source.get("title") or citekey,
            "authors": (entry.authors_str if entry else None) or source.get("authors"),
            "year": (entry.year if entry else None) or source.get("year"),
            "doi": (entry.DOI if entry else None) or source.get("doi"),
            "source": str(pdf_path),
        },
    )
    state.set_pdf_text_length(citekey, sum(len(p) for p in pages))
    stats.sidecars_written += 1
    console.print(f"  [green]{citekey}: sidecar written ({len(pages)} pages)[/green]")


def repair_verbatim(
    kctx, citekey: str, stats: RepairStats, dry_run: bool,
) -> None:
    """Recompute verbatim/span/locator for every fragment of one source.

    Every fragment is validated against the sidecar canonical text —
    including flips in both directions. Confirmed fragments get a raw-text
    span and a derived locator; failed ones lose the flag (and any stale
    span, see FragmentRepository.update_fragment_provenance).
    """
    state = kctx.state
    doc = load_sidecar_doc(kctx.project_root, citekey)
    if doc is None:
        stats.sources_no_sidecar += 1
        console.print(f"  [yellow]{citekey}: no sidecar — verbatim skipped[/yellow]")
        return

    fragments = state.get_fragments(source_id=citekey, limit=1_000_000)
    if not fragments:
        return

    # Dual-write target: library.db keeps its own verbatim flag per
    # content-addressed fragment (compat with SaaS worker reads).
    paper_id = None
    if kctx.paper_store is not None and kctx.user_library is not None:
        try:
            paper_id = kctx.user_library.resolve_paper_id(citekey)
        except Exception as exc:  # noqa: BLE001 — library.db is best-effort here
            stats.warnings.append(f"{citekey}: library.db lookup failed: {exc}")

    confirmed = upgraded = downgraded = spans = 0
    for frag in fragments:
        stats.fragments_checked += 1
        was_verbatim = bool(frag.get("verbatim"))
        span = locate_fragment_span(frag["fragment_text"], doc.text)

        if span is not None:
            char_start, char_end = span
            page = doc.page_for(char_start) or frag.get("page_number")
            loc = derive_locator(doc.text, char_start, page=page)
            confirmed += 1
            spans += 1
            if not was_verbatim:
                upgraded += 1
            if loc:
                stats.locators_written += 1
            if not dry_run:
                state.update_fragment_provenance(
                    frag["id"],
                    verbatim=True,
                    char_start=char_start,
                    char_end=char_end,
                    source_locator=loc,
                )
            new_verbatim = True
        else:
            if was_verbatim:
                downgraded += 1
                if not dry_run:
                    state.update_fragment_provenance(frag["id"], verbatim=False)
            new_verbatim = False

        if paper_id and not dry_run:
            from ..hashing import compute_content_hash

            try:
                kctx.paper_store.update_fragment_verbatim(
                    compute_content_hash(
                        paper_id, frag["fragment_text"], frag.get("page_number")
                    ),
                    new_verbatim,
                )
            except Exception as exc:  # noqa: BLE001
                stats.warnings.append(
                    f"{citekey}: library.db verbatim dual-write failed: {exc}"
                )

    # Three-tier substrate (plan C2): the same verdicts go to the library
    # attempt links so the new source of truth carries spans too. Legacy
    # fragments belong to the synthetic legacy attempt of (paper, citekey).
    if paper_id and not dry_run and kctx.paper_store is not None:
        try:
            _write_attempt_provenance(kctx, citekey, paper_id, fragments, doc, stats)
        except Exception as exc:  # noqa: BLE001
            stats.warnings.append(f"{citekey}: library attempt provenance failed: {exc}")

    stats.verbatim_confirmed += confirmed
    stats.verbatim_upgraded += upgraded
    stats.verbatim_downgraded += downgraded
    stats.spans_written += spans

    arrow_up = f", [green]{upgraded}↑[/green]" if upgraded else ""
    arrow_down = f", [red]{downgraded}↓[/red]" if downgraded else ""
    console.print(
        f"  {citekey}: {len(fragments)} fragments — "
        f"{confirmed} verbatim confirmed{arrow_up}{arrow_down}, {spans} spans"
    )



def _write_attempt_provenance(kctx, citekey: str, paper_id: str, fragments, doc, stats: RepairStats) -> None:
    """Mirror verbatim/span/locator verdicts into ``extraction_attempt_fragments``.

    Every attempt of the paper that links a fragment gets the fresh verdict
    (spans are only valid for the sidecar generation they were computed
    against, so all links are refreshed together). Fragments without any
    attempt link are attached to the legacy attempt of (paper, citekey).
    """
    from ..hashing import compute_content_hash
    from ..migration import legacy_attempt_id
    from ..models import FragmentRecord

    ps = kctx.paper_store
    legacy = legacy_attempt_id(paper_id, citekey)
    linked_by_attempt: dict[str, set[str]] = {}
    for att in ps.get_attempts(paper_id):
        linked_by_attempt[att["attempt_id"]] = {
            r["fragment_id"] for r in ps.get_attempt_fragments(att["attempt_id"])
        }
    legacy_records: list[FragmentRecord] = []
    legacy_links: list[dict] = []
    for frag in fragments:
        fid = compute_content_hash(paper_id, frag["fragment_text"], frag.get("page_number"))
        span = locate_fragment_span(frag["fragment_text"], doc.text)
        if span is not None:
            page = doc.page_for(span[0]) or frag.get("page_number")
            link = {
                "char_start": span[0], "char_end": span[1],
                "source_locator": derive_locator(doc.text, span[0], page=page),
                "verbatim_status": "confirmed",
            }
        else:
            link = {"char_start": None, "char_end": None, "source_locator": None,
                    "verbatim_status": "downgraded" if frag.get("verbatim") else "unverified"}
        owners = [a for a, ids in linked_by_attempt.items() if fid in ids]
        if not owners:
            legacy_records.append(FragmentRecord(
                fragment_id=fid, paper_id=paper_id, fragment_text=frag["fragment_text"],
                fragment_type=frag.get("fragment_type") or "key_idea",
                page_number=frag.get("page_number"), citation_intent=frag.get("citation_intent"),
                verbatim=span is not None, content_hash=fid,
            ))
            legacy_links.append(link)
            continue
        for att in owners:
            ps.update_attempt_fragment_provenance(att, fid, **link)
    if legacy_records:
        if ps.get_attempt(legacy) is None:
            ps.start_attempt(legacy, paper_id, prompt_name="legacy", ai_model="legacy",
                             mode="legacy", extractor_version="0")
            ps.finish_attempt(legacy, status="published")
        ps.save_attempt_fragments(legacy, paper_id, legacy_records, legacy_links)


def repair_run(kctx, run_id: int, stats: RepairStats, dry_run: bool) -> None:
    """``--run N``: full verbatim/span check for one (possibly unpublished) run.

    Validates every fragment linked to the run's attempt against the sidecar,
    refreshes the attempt links and, when nothing is partial, lets the
    project store publish the run (``clear_validation_incomplete``).
    """
    from ..hashing import compute_content_hash  # noqa: F401  (kept for symmetry)

    pj, ps = kctx.project_store, kctx.paper_store
    if pj is None or ps is None:
        stats.warnings.append("--run needs the three-tier stores")
        return
    run = pj.get_run(run_id)
    if run is None:
        stats.warnings.append(f"run {run_id} not found")
        return
    citekey = run["citekey"]
    doc = load_sidecar_doc(kctx.project_root, citekey)
    if doc is None:
        stats.sources_no_sidecar += 1
        console.print(f"  [yellow]{citekey}: no sidecar — run {run_id} not validated[/yellow]")
        return
    attempt_id = run.get("attempt_id")
    if not attempt_id:
        stats.warnings.append(f"run {run_id}: no attempt_id (failed before library write)")
        return
    linked = ps.get_attempt_fragments(attempt_id)
    confirmed = downgraded = 0
    for row in linked:
        stats.fragments_checked += 1
        span = locate_fragment_span(row["fragment_text"], doc.text)
        if span is not None:
            page = doc.page_for(span[0]) or row.get("page_number")
            confirmed += 1
            if not dry_run:
                ps.update_attempt_fragment_provenance(
                    attempt_id, row["fragment_id"], char_start=span[0], char_end=span[1],
                    source_locator=derive_locator(doc.text, span[0], page=page),
                    verbatim_status="confirmed",
                )
                stats.spans_written += 1
        else:
            if row.get("verbatim_status") == "confirmed":
                downgraded += 1
            if not dry_run:
                ps.update_attempt_fragment_provenance(
                    attempt_id, row["fragment_id"], char_start=None, char_end=None,
                    source_locator=None, verbatim_status="downgraded",
                )
    stats.verbatim_confirmed += confirmed
    stats.verbatim_downgraded += downgraded
    if dry_run:
        console.print(f"  [dim]run {run_id} (@{citekey}): would validate {len(linked)} fragment(s)[/dim]")
        return
    ps.finish_attempt(attempt_id, status=run["status"], validation_incomplete=False,
                      coverage_json=run.get("coverage_json") or "")
    new_status = pj.clear_validation_incomplete(run_id)
    console.print(
        f"  run {run_id} (@{citekey}): {confirmed} confirmed, {downgraded} downgraded → "
        f"[{'green' if new_status == 'published' else 'yellow'}]{new_status}[/]"
    )

def repair_embeddings(kctx, citekey: str, stats: RepairStats, dry_run: bool) -> None:
    """Re-embed the source's missing vectors (fragments + source itself)."""
    state = kctx.state
    emb = kctx.embeddings

    fragments = state.get_fragments(source_id=citekey, limit=1_000_000)
    missing = [f for f in fragments if not f.get("embedding")]
    source = state.get_source(citekey) or {}
    source_vec_missing = source.get("embedding") is None and bool(
        source.get("abstract")
    )

    if not missing and not source_vec_missing:
        return

    if dry_run:
        stats.embeddings_created += len(missing) + (1 if source_vec_missing else 0)
        console.print(
            f"  [dim]{citekey}: would embed {len(missing)} fragment(s)"
            + (" + source vector" if source_vec_missing else "")
            + "[/dim]"
        )
        return

    if source_vec_missing:
        try:
            vec = emb.embed(source.get("title") or citekey, source.get("abstract") or "")
            if vec:
                state.save_embedding(citekey, vec, emb.model_name)
                stats.embeddings_created += 1
            else:
                stats.embeddings_failed += 1
        except Exception as exc:  # noqa: BLE001 — счётчик, не тихий отказ
            stats.embeddings_failed += 1
            stats.warnings.append(f"{citekey}: source embedding failed: {exc}")

    count, failed = _auto_embed_after_process(
        citekey,
        state,
        emb,
        quiet=True,
        paper_store=kctx.paper_store,
        user_library=kctx.user_library,
    )
    stats.embeddings_created += count
    stats.embeddings_failed += failed
    console.print(
        f"  {citekey}: embedded {count} fragment(s)"
        + (f", [yellow]{failed} failed[/yellow]" if failed else "")
    )


def _sidecar_missing(kctx, citekey: str) -> bool:
    return not (kctx.project_root / ".klemma" / "pdfs" / f"{citekey}.md").exists()


def _unembedded_count(state, citekey: str) -> int:
    fragments = state.get_fragments(source_id=citekey, limit=1_000_000)
    return sum(1 for f in fragments if not f.get("embedding"))


def reconcile_degraded(kctx, citekey: str, stats: RepairStats) -> None:
    """Re-check a degraded source's recorded failed steps against reality.

    Clears the ``degraded`` status only when every recorded step is
    verifiably fixed (sidecar file present / no unembedded fragments);
    otherwise rewrites ``degraded_steps`` down to what still fails. Trusting
    the on-disk/DB state instead of "the step ran" keeps repair honest when
    a step ran but failed again.
    """
    state = kctx.state
    source = state.get_source(citekey) or {}
    if source.get("status") != "degraded":
        return
    try:
        recorded = json.loads(source.get("degraded_steps") or "[]")
    except ValueError:
        recorded = []

    remaining = []
    for step in recorded:
        if step == "sidecar":
            if _sidecar_missing(kctx, citekey):
                remaining.append(step)
        elif step == "embeddings":
            if _unembedded_count(state, citekey) > 0:
                remaining.append(step)
        else:
            remaining.append(step)  # unknown step — keep, never silently drop

    if not remaining:
        state.clear_degraded(citekey)
        stats.degraded_cleared += 1
        console.print(f"  [green]{citekey}: degraded → completed[/green]")
    elif set(remaining) != set(recorded):
        state.mark_degraded(citekey, remaining)


def run_repair(
    kctx,
    citekeys: tuple[str, ...],
    cited: tuple[str, ...],
    steps: list[str],
    dry_run: bool,
) -> RepairStats:
    """Execute repair steps over the selected sources; returns counters."""
    state = kctx.state
    stats = RepairStats()

    selected: set[str] = set(citekeys)
    if cited:
        cited_keys, cite_warnings = collect_cited_citekeys(cited)
        stats.warnings.extend(cite_warnings)
        selected |= cited_keys

    known = state.get_existing_source_ids()
    if not selected:
        selected = set(known)
    else:
        unknown = sorted(selected - known)
        if unknown:
            stats.warnings.append(
                f"not in project DB (skipped): {', '.join(unknown)}"
            )
        selected &= known

    steps = list(steps)
    if "embeddings" in steps and kctx.embeddings is None:
        stats.warnings.append(
            "no embeddings backend configured — embeddings step skipped"
        )
        steps.remove("embeddings")

    pdf_extractor = None
    if "sidecar" in steps:
        from ..literature.pdf import PDFExtractor

        pdf_extractor = PDFExtractor(max_chars=kctx.config.ai.max_pdf_chars)

    for citekey in sorted(selected):
        stats.sources_seen += 1
        if "sidecar" in steps:
            repair_sidecar(kctx, citekey, pdf_extractor, stats, dry_run)
        if "verbatim" in steps:
            repair_verbatim(kctx, citekey, stats, dry_run)
        if "embeddings" in steps:
            repair_embeddings(kctx, citekey, stats, dry_run)
        if not dry_run:
            reconcile_degraded(kctx, citekey, stats)

    return stats


def run_scan(kctx, citekeys: tuple[str, ...], dry_run: bool) -> None:
    """Audit completed sources for silent degradation and flag them.

    Historical backfill: sources processed before the sidecar/degraded
    machinery existed look 'completed' while their full text or vectors
    are missing. The embeddings criterion only applies when an embeddings
    backend is configured — without one, missing vectors are a config
    choice, not degradation.
    """
    state = kctx.state
    check_embeddings = kctx.embeddings is not None

    flagged = 0
    completed = state.get_completed_sources()
    for citekey in completed:
        source = state.get_source(citekey) or {}
        if citekeys and citekey not in citekeys:
            continue
        issues: list[str] = []
        if source.get("source_type") != "online" and _sidecar_missing(kctx, citekey):
            issues.append("sidecar")
        if check_embeddings and _unembedded_count(state, citekey) > 0:
            issues.append("embeddings")
        if not issues:
            continue
        flagged += 1
        console.print(f"  [yellow]{citekey}[/yellow]: {', '.join(issues)}")
        if not dry_run:
            state.mark_degraded(citekey, issues)

    # Orphan attempts: library rows no project run references — leftovers of
    # a crash between the library write and the project publish (plan C2).
    pj, ps = getattr(kctx, "project_store", None), getattr(kctx, "paper_store", None)
    if pj is not None and ps is not None:
        try:
            orphans = ps.find_orphan_attempts(pj.referenced_attempt_ids())
            orphans = [o for o in orphans if o.get("mode") != "legacy"]
            if orphans:
                console.print(
                    f"  [dim]{len(orphans)} orphan extraction attempt(s) in library.db "
                    f"(unreferenced by any run; harmless)[/dim]"
                )
        except Exception as exc:  # noqa: BLE001
            console.print(f"  [dim]orphan scan skipped: {exc}[/dim]")

    already = len(state.get_degraded_sources())
    verb = "found" if dry_run else "flagged as degraded"
    console.print(
        f"\n[bold]Scan: {flagged} completed source(s) {verb}.[/bold]"
        + (f" [dim]({already} degraded total)[/dim]" if already else "")
    )
    if not check_embeddings:
        console.print(
            "[dim]No embeddings backend configured — only sidecar presence was checked.[/dim]"
        )
    if flagged and dry_run:
        console.print("[dim]Re-run without --dry-run to mark them, then 'klemma repair' to fix.[/dim]")


def print_repair_summary(stats: RepairStats, steps: list[str], dry_run: bool) -> None:
    """Human-visible mutation summary — the CLI contract of this command."""
    prefix = "Would repair" if dry_run else "Repaired"
    console.print(f"\n[bold]{prefix} {stats.sources_seen} source(s).[/bold]")
    if "sidecar" in steps:
        line = (
            f"  Sidecars: [green]{stats.sidecars_written} "
            f"{'to write' if dry_run else 'written'}[/green], "
            f"{stats.sidecars_existing} already present"
        )
        if stats.sidecars_no_pdf:
            line += f", [yellow]{stats.sidecars_no_pdf} without PDF[/yellow]"
        console.print(line)
    if "verbatim" in steps:
        line = (
            f"  Verbatim: {stats.fragments_checked} fragments checked, "
            f"[green]{stats.verbatim_confirmed} confirmed "
            f"({stats.verbatim_upgraded}↑)[/green], "
            f"[red]{stats.verbatim_downgraded} downgraded↓[/red], "
            f"{stats.spans_written} spans, {stats.locators_written} locators"
        )
        if stats.sources_no_sidecar:
            line += f", [yellow]{stats.sources_no_sidecar} source(s) without sidecar[/yellow]"
        console.print(line)
    if "embeddings" in steps and (stats.embeddings_created or stats.embeddings_failed):
        line = (
            f"  Embeddings: [green]{stats.embeddings_created} "
            f"{'to create' if dry_run else 'created'}[/green]"
        )
        if stats.embeddings_failed:
            line += f", [red]{stats.embeddings_failed} failed[/red]"
        console.print(line)
    if stats.degraded_cleared:
        console.print(
            f"  [green]{stats.degraded_cleared} source(s) degraded → completed[/green]"
        )
    for w in stats.warnings:
        console.print(f"  [yellow]⚠ {w}[/yellow]")
    if dry_run:
        console.print("[dim]Dry run — nothing was written.[/dim]")


@main.command("repair")
@click.argument("citekeys", nargs=-1)
@click.option(
    "--cited",
    multiple=True,
    help="Файл/директория/glob с *.md — источники собираются по цитатам [@citekey] (повторяемый)",
)
@click.option(
    "--steps",
    "steps_csv",
    default="sidecar,verbatim,embeddings",
    show_default=True,
    help="Какие шаги выполнять (через запятую): " + ", ".join(_KNOWN_STEPS),
)
@click.option(
    "--scan",
    is_flag=True,
    help="Не чинить, а найти completed-источники без sidecar / с фрагментами без векторов и пометить degraded",
)
@click.option("--dry-run", is_flag=True, help="Показать, что будет сделано, ничего не записывая")
@click.option("--run", "run_id", type=int, default=None,
              help="Полная проверка дословности одного прогона (в т. ч. неопубликованного) по sidecar")
@click.pass_context
def repair(ctx, citekeys, cited, steps_csv, scan, dry_run, run_id):
    """Дообработать источники: sidecar с полным текстом, честный verbatim, spans.

    CITEKEYS: явный список источников. --cited собирает citekey из цитат
    [@citekey] в markdown-файлах (рукопись/черновики). Без того и другого
    обрабатываются все источники проектной БД. Источники со статусом
    degraded возвращаются в completed, когда их шаги реально починены.

    \b
    Examples:
      klemma repair gost2025iceservice
      klemma repair --cited draft/ --cited "papers/**/*.md"
      klemma repair --steps verbatim --dry-run
      klemma repair --scan
    """
    kctx = _get_context(ctx)

    if scan:
        run_scan(kctx, citekeys, dry_run)
        return
    if run_id is not None:
        stats = RepairStats()
        repair_run(kctx, run_id, stats, dry_run)
        print_repair_summary(stats, ["verbatim"], dry_run)
        return

    steps = [s.strip() for s in steps_csv.split(",") if s.strip()]
    unknown_steps = [s for s in steps if s not in _KNOWN_STEPS]
    if unknown_steps:
        console.print(
            f"[red]Unknown step(s): {', '.join(unknown_steps)}. "
            f"Known: {', '.join(_KNOWN_STEPS)}[/red]"
        )
        raise SystemExit(1)

    stats = run_repair(kctx, citekeys, cited, steps, dry_run)
    print_repair_summary(stats, steps, dry_run)
