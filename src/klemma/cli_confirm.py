"""Shared interactive confirmation UI for mutation commands (ADR-011).

Pattern: suggest → review → apply. All mutation commands with --apply
use this module for consistent per-item confirmation UX.
"""

from dataclasses import dataclass, field
from typing import Callable, Optional

import click
from rich.console import Console


@dataclass
class ReviewItem:
    """One item to present for user confirmation.

    Attributes:
        key: Unique identifier (e.g. citekey, fragment ID).
        header: Bold first line (e.g. "@citekey" or "paper title").
        details: Lines of context shown before the prompt.
            Each entry is (label, value) — rendered as "  label: value".
        action_label: What accepting means (e.g. "Add section 1.4", "Delete").
        data: Arbitrary payload passed through to the apply callback.
    """
    key: str
    header: str
    details: list[tuple[str, str]] = field(default_factory=list)
    action_label: str = ""
    data: dict = field(default_factory=dict)


@dataclass
class ReviewResult:
    """Outcome of an interactive review session."""
    accepted: list[ReviewItem] = field(default_factory=list)
    skipped: int = 0
    quit_early: bool = False


def interactive_review(
    items: list[ReviewItem],
    *,
    console: Console,
    title: str = "Review items",
    default_choice: str = "y",
    yes: bool = False,
    on_accept: Optional[Callable[[ReviewItem], Optional[str]]] = None,
) -> ReviewResult:
    """Run a per-item y/n/q interactive review loop.

    Args:
        items: Items to review.
        console: Rich console for output.
        title: Header printed before the loop.
        default_choice: Default for the prompt ("y" or "n").
        yes: Skip prompts, auto-accept all (for --yes flag).
        on_accept: Optional callback called immediately when item is accepted.
            Returns an optional status message to display (e.g. "[red]Deleted[/red]").

    Returns:
        ReviewResult with accepted items, skip count, and quit flag.
    """
    if not items:
        console.print("[dim]No items to review.[/dim]")
        return ReviewResult()

    console.print(f"\n[bold]{title} ({len(items)} items)[/bold]")
    console.print("[dim]y = accept, n = skip, q = quit[/dim]\n")

    result = ReviewResult()

    for i, item in enumerate(items, 1):
        # Header
        console.print(f"[bold]── [{i}/{len(items)}] {item.header} ──[/bold]")

        # Detail lines
        for label, value in item.details:
            if label:
                console.print(f"  [dim]{label}:[/dim] {value}")
            else:
                console.print(f"  {value}")

        # Action description
        if item.action_label:
            console.print(f"  [bold]Action:[/bold] {item.action_label}")

        if yes:
            choice = "y"
        else:
            choice = click.prompt(
                "  Accept?",
                type=click.Choice(["y", "n", "q"]),
                default=default_choice,
            )

        if choice == "q":
            console.print("[dim]Stopped.[/dim]")
            result.quit_early = True
            break
        elif choice == "y":
            result.accepted.append(item)
            if on_accept:
                msg = on_accept(item)
                if msg:
                    console.print(f"  {msg}")
        else:
            result.skipped += 1
            console.print("  [dim]Skipped[/dim]")

        console.print()

    return result
