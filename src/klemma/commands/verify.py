"""klemma check-citations + claims — citation verifier and claims ledger (ADR-018)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import click
from rich.table import Table

from ..cli import _get_context, console, main
from ..skills.citation_checker import (
    build_claim_entries,
    build_judge_provider,
    check_citations_file,
)

_SEVERITY_COLOR = {
    "ok": "green",
    "unverifiable": "dim",
    "soft_warn": "yellow",
    "hard_warn": "red",
    "error": "bold red",
}

_GATE_SEVERITY_RANK = {"soft_warn": 2, "hard_warn": 3, "error": 4}


def _manuscript_rel_path(md_path: Path, project_root: Path) -> str | None:
    """Ledger key: manuscript path relative to project root (POSIX form).

    Files outside the project have no stable key — returns None and the
    caller skips persistence with a visible warning.
    """
    try:
        return md_path.resolve().relative_to(project_root.resolve()).as_posix()
    except (ValueError, OSError):
        return None


@main.command("check-citations")
@click.argument("targets", nargs=-1, type=click.Path(exists=True, path_type=Path))
@click.option("--no-ai", is_flag=True, help="Только детерминированная проверка (без LLM-judge)")
@click.option(
    "--incremental",
    is_flag=True,
    help="Реплеить живые вердикты из реестра claims вместо повторных вызовов LLM-judge",
)
@click.option(
    "--fail-on",
    default="hard_warn",
    show_default=True,
    type=click.Choice(["soft_warn", "hard_warn", "error", "never"]),
    help="Минимальный severity для ненулевого exit-кода",
)
@click.option("--strict", is_flag=True, help="Синоним --fail-on soft_warn")
@click.option("--json", "as_json", is_flag=True, help="Вывод в JSON вместо таблицы")
@click.option("--recursive", "-r", is_flag=True, help="Рекурсивно обойти все .md файлы в директориях")
@click.pass_context
def check_citations(ctx, targets, no_ai, incremental, fail_on, strict, as_json, recursive):
    """Проверить цитаты в черновиках на числовые ошибки и дефинициональные отклонения.

    TARGETS: один или несколько .md файлов (или директорий с --recursive).

    По умолчанию ищет и вызывает LLM-judge для числовых и дефинициональных якорей.
    --no-ai пропускает LLM и возвращает только детерминированные результаты (quote + numeric-absent).
    Каждый прогон записывается в реестр claims (см. `klemma claims status`);
    --incremental переиспользует живые вердикты из реестра и тратит бюджет
    LLM-вызовов только на новые или отредактированные клеймы.

    Exit codes:
      0 — все проверки прошли (или нет якорей)
      1 — найдены проблемы на уровне fail-on или выше
      2 — внутренняя ошибка (файл не найден, сбой движка)

    Example:
      klemma check-citations draft/chapter_1.md
      klemma check-citations --no-ai --recursive draft/
    """
    if strict:
        fail_on = "soft_warn"

    kctx = _get_context(ctx)
    cfg = kctx.config
    state = kctx.state
    project_root = kctx.project_root
    klemma_home = kctx.klemma_home
    project_chain = kctx.project_chain

    # Resolve target files
    md_files: list[Path] = []
    for t in targets:
        t = Path(t)
        if t.is_dir():
            if recursive:
                md_files.extend(sorted(t.rglob("*.md")))
            else:
                console.print(f"[yellow]{t} is a directory — pass --recursive to scan it[/yellow]")
        else:
            md_files.append(t)

    if not md_files:
        # Default: current project's draft/ directory
        draft_dir = project_root / "draft"
        if draft_dir.exists():
            md_files = sorted(draft_dir.glob("*.md"))
        if not md_files:
            console.print("[yellow]No .md targets found. Pass file paths or use --recursive.[/yellow]")
            sys.exit(0)

    # Build judge provider (once, shared across all files)
    judge_ai = None
    if not no_ai:
        judge_ai = build_judge_provider(cfg)
        if judge_ai is None:
            console.print(
                "[yellow]Citation AI judge unavailable (degraded mode). "
                "Numeric-drift and definitional anchors will be unverifiable.[/yellow]"
            )

    # Resolve three-tier stores
    paper_store = getattr(kctx, "paper_store", None)
    user_library = getattr(kctx, "user_library", None)

    all_reports = []
    engine_error = False

    for md_path in md_files:
        rel_path = _manuscript_rel_path(md_path, project_root)

        # --incremental: live ledger rows keyed by (claim_hash, anchor_key) —
        # the checker replays their definitive verdicts instead of re-judging.
        replay = None
        if incremental and rel_path is not None:
            saved = state.get_claims(rel_path, include_stale=False)
            replay = {(r["claim_hash"], r["anchor_key"]): r for r in saved}

        report = check_citations_file(
            md_path,
            config=cfg,
            state=state,
            paper_store=paper_store,
            user_library=user_library,
            judge_ai=judge_ai,
            project_root=project_root,
            klemma_home=klemma_home,
            project_chain=project_chain,
            use_ai=not no_ai,
            fail_on=fail_on,
            replay=replay,
        )
        all_reports.append(report)
        if report.status == "error":
            engine_error = True
            # An errored run is not a trustworthy snapshot of the manuscript —
            # persisting it could mark every existing ledger row stale.
            continue

        if rel_path is None:
            console.print(
                f"[yellow]{md_path}: вне корня проекта — реестр claims не обновлён[/yellow]"
            )
            continue

        entries = build_claim_entries(report.claims, report.verdicts)
        state.record_claim_check(rel_path, entries, judge_model=report.model)
        stale_n = state.mark_claims_stale(
            rel_path, {e["claim_hash"] for e in entries}
        )
        if not as_json:
            note = f"  [dim]claims: {len(entries)} записано"
            if stale_n:
                note += f", {stale_n} помечено stale"
            console.print(note + "[/dim]")

    if as_json:
        output = []
        for r in all_reports:
            output.append({
                "target": r.target,
                "status": r.status,
                "summary": r.summary,
                "errors": r.errors,
                "input_tokens": r.input_tokens,
                "output_tokens": r.output_tokens,
                "model": r.model,
                "verdicts": [
                    {
                        "citekey": v.citekey,
                        "location": v.location,
                        "anchor_kind": v.anchor.kind,
                        "anchor_raw": v.anchor.raw,
                        "severity": v.severity,
                        "reason": v.reason,
                        "offending_span": v.offending_span,
                        "ai_used": v.ai_used,
                        "claim_sentence": v.claim_sentence,
                    }
                    for v in r.verdicts
                ],
            })
        click.echo(json.dumps(output, ensure_ascii=False, indent=2))
    else:
        _print_reports(all_reports)

    # Determine exit code
    if engine_error:
        sys.exit(2)

    severity_rank = {
        "ok": 0, "unverifiable": 1, "soft_warn": 2, "hard_warn": 3, "error": 4
    }
    fail_rank = severity_rank.get(fail_on, 3)

    for report in all_reports:
        for v in report.verdicts:
            if severity_rank.get(v.severity, 0) >= fail_rank and fail_on != "never":
                sys.exit(1)

    sys.exit(0)


def _print_reports(reports) -> None:
    for report in reports:
        _print_single_report(report)


def _print_single_report(report) -> None:
    status_color = {"ok": "green", "degraded": "yellow", "error": "red"}.get(report.status, "white")
    console.print(
        f"\n[bold]{report.target}[/bold] "
        f"[{status_color}]{report.status.upper()}[/{status_color}]"
        f" — {report.summary}"
    )

    if report.errors:
        for err in report.errors:
            console.print(f"  [dim red]⚠ {err}[/dim red]")

    if not report.verdicts:
        console.print("  [dim]No anchors found[/dim]")
        return

    table = Table(show_header=True, header_style="bold", box=None, padding=(0, 1))
    table.add_column("Severity", width=12)
    table.add_column("Citekey", width=16)
    table.add_column("Kind", width=12)
    table.add_column("Anchor", width=30, overflow="fold")
    table.add_column("Reason", overflow="fold")

    for v in sorted(report.verdicts, key=lambda x: (
        {"hard_warn": 0, "soft_warn": 1, "error": 0, "unverifiable": 2, "ok": 3}.get(x.severity, 4),
        x.citekey,
    )):
        color = _SEVERITY_COLOR.get(v.severity, "white")
        table.add_row(
            f"[{color}]{v.severity}[/{color}]",
            v.citekey,
            v.anchor.kind,
            v.anchor.raw[:60],
            v.reason,
        )

    console.print(table)

    if report.model:
        tok_info = f"[dim]judge: {report.model} ({report.input_tokens}→{report.output_tokens} tok)[/dim]"
        console.print(f"  {tok_info}")


# ---------------------------------------------------------------------------
# klemma claims — ledger status and submission gate
# ---------------------------------------------------------------------------


@main.group("claims")
def claims_group():
    """Реестр клеймов рукописей — состояние инкрементального аудита цитат."""


@claims_group.command("status")
@click.argument("target", required=False, type=click.Path(path_type=Path))
@click.option(
    "--gate",
    is_flag=True,
    help="Режим ворот перед подачей: exit 1 при stale/unchecked/вердиктах ≥ fail-on",
)
@click.option(
    "--fail-on",
    default="hard_warn",
    show_default=True,
    type=click.Choice(["soft_warn", "hard_warn", "error"]),
    help="Минимальный severity вердикта, проваливающий ворота",
)
@click.pass_context
def claims_status(ctx, target, gate, fail_on):
    """Сводка реестра claims по манускриптам: ok/warn/unverifiable/stale/unchecked.

    TARGET: необязательный путь к манускрипту (по умолчанию — все манускрипты).

    --gate — ворота перед подачей: рукопись не готова, пока в реестре есть
    stale-строки (текст правился после проверки), unchecked-клеймы (цитата
    без вердикта) или вердикты уровня fail-on и выше.

    Exit codes (--gate):
      0 — реестр чист, рукопись готова к подаче
      1 — есть stale/unchecked/вердикты ≥ fail-on, либо аудит не проводился
    """
    kctx = _get_context(ctx)
    state = kctx.state
    project_root = kctx.project_root

    manuscript = None
    if target is not None:
        # Accept both a real path (resolved against project root) and a raw
        # ledger key (the file may have been renamed/removed since the audit).
        manuscript = _manuscript_rel_path(Path(target), project_root) or str(target)

    rows = state.get_claims_status_summary(manuscript)

    if not rows:
        scope = f" для {manuscript}" if manuscript else ""
        console.print(f"[yellow]Реестр claims пуст{scope} — запустите klemma check-citations.[/yellow]")
        sys.exit(1 if gate else 0)

    table = Table(show_header=True, header_style="bold", box=None, padding=(0, 1))
    table.add_column("Manuscript", overflow="fold")
    table.add_column("ok", justify="right", style="green")
    table.add_column("soft_warn", justify="right", style="yellow")
    table.add_column("hard_warn", justify="right", style="red")
    table.add_column("unverifiable", justify="right", style="dim")
    table.add_column("unchecked", justify="right")
    table.add_column("stale", justify="right")
    table.add_column("last_verified", style="dim")

    gate_failures: list[str] = []
    fail_rank = _GATE_SEVERITY_RANK[fail_on]

    for row in rows:
        table.add_row(
            row["manuscript_path"],
            str(row["ok"] or 0),
            str(row["soft_warn"] or 0),
            str(row["hard_warn"] or 0),
            str(row["unverifiable"] or 0),
            str(row["unchecked"] or 0),
            str(row["stale"] or 0),
            row["last_verified"] or "—",
        )

        reasons = []
        if row["stale"]:
            reasons.append(f"{row['stale']} stale")
        if row["unchecked"]:
            reasons.append(f"{row['unchecked']} unchecked")
        for severity, rank in _GATE_SEVERITY_RANK.items():
            if rank >= fail_rank and (row[severity] or 0):
                reasons.append(f"{row[severity]} {severity}")
        if reasons:
            gate_failures.append(f"{row['manuscript_path']}: {', '.join(reasons)}")

    console.print(table)

    if gate:
        if gate_failures:
            console.print("\n[red]Ворота не пройдены:[/red]")
            for line in gate_failures:
                console.print(f"  [red]✗ {line}[/red]")
            sys.exit(1)
        console.print("\n[green]Ворота пройдены — все клеймы проверены и живы.[/green]")
    sys.exit(0)
