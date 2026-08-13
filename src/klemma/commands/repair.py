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
  a char span into the sidecar plus a human-readable source locator.

All data mutations are counted and printed — silent repair is not repair.
"""

from __future__ import annotations

import glob as _glob
from dataclasses import dataclass, field
from pathlib import Path

import click

from ..cli import _get_context, console, main
from ..literature.locator import derive_locator
from ..literature.sidecar import load_sidecar_doc, write_pdf_sidecar
from ..skills.citation_checker import _CITE_REF_RE, _extract_citekeys_from_ref
from ..skills.extractor import locate_fragment_span

_KNOWN_STEPS = ("sidecar", "verbatim")


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

    return stats


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
    default="sidecar,verbatim",
    show_default=True,
    help="Какие шаги выполнять (через запятую): " + ", ".join(_KNOWN_STEPS),
)
@click.option("--dry-run", is_flag=True, help="Показать, что будет сделано, ничего не записывая")
@click.pass_context
def repair(ctx, citekeys, cited, steps_csv, dry_run):
    """Дообработать источники: sidecar с полным текстом, честный verbatim, spans.

    CITEKEYS: явный список источников. --cited собирает citekey из цитат
    [@citekey] в markdown-файлах (рукопись/черновики). Без того и другого
    обрабатываются все источники проектной БД.

    \b
    Examples:
      klemma repair gost2025iceservice
      klemma repair --cited draft/ --cited "papers/**/*.md"
      klemma repair --steps verbatim --dry-run
    """
    steps = [s.strip() for s in steps_csv.split(",") if s.strip()]
    unknown_steps = [s for s in steps if s not in _KNOWN_STEPS]
    if unknown_steps:
        console.print(
            f"[red]Unknown step(s): {', '.join(unknown_steps)}. "
            f"Known: {', '.join(_KNOWN_STEPS)}[/red]"
        )
        raise SystemExit(1)

    kctx = _get_context(ctx)
    stats = run_repair(kctx, citekeys, cited, steps, dry_run)
    print_repair_summary(stats, steps, dry_run)
