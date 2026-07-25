"""klemma check-citations — standalone citation integrity verifier (ADR-018)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import click
from rich.table import Table

from ..cli import _get_context, console, main
from ..skills.citation_checker import build_judge_provider, check_citations_file

_SEVERITY_COLOR = {
    "ok": "green",
    "unverifiable": "dim",
    "soft_warn": "yellow",
    "hard_warn": "red",
    "error": "bold red",
}


@main.command("check-citations")
@click.argument("targets", nargs=-1, type=click.Path(exists=True, path_type=Path))
@click.option("--no-ai", is_flag=True, help="Только детерминированная проверка (без LLM-judge)")
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
def check_citations(ctx, targets, no_ai, fail_on, strict, as_json, recursive):
    """Проверить цитаты в черновиках на числовые ошибки и дефинициональные отклонения.

    TARGETS: один или несколько .md файлов (или директорий с --recursive).

    По умолчанию ищет и вызывает LLM-judge для числовых и дефинициональных якорей.
    --no-ai пропускает LLM и возвращает только детерминированные результаты (quote + numeric-absent).

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
        )
        all_reports.append(report)
        if report.status == "error":
            engine_error = True

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
