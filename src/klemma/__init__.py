"""Klemma — AI academic assistant."""

__version__ = "0.7.1"

# Two mascot variants — user picks one later, both shipped for now.
# Each is a list of (mascot_line, info_line) tuples for side-by-side rendering.

_SQUIRREL = [
    ("[red]    /\\  /\\ [/red]", ""),
    ("[red]   ([/red] o  o [red])[/red]", "  [bold]Klemma[/bold] [dim]v{version}[/dim]"),
    ("[red]   (  >>  )[/red]", "  AI Academic Assistant"),
    ("[red]    / || \\ [/red]", "  [dim]{cwd}[/dim]"),
    ("[red]   (_/  \\_)[/red]", ""),
]

_GIRL = [
    ("[red]    .---.[/red]", ""),
    ("[red]   /[/red] o=o [red]\\ [/red]", "  [bold]Klemma[/bold] [dim]v{version}[/dim]"),
    ("[red]   |[/red]  <  [red]|[/red]", "  AI Academic Assistant"),
    ("[red]   |[/red] '-' [red]|[/red]", "  [dim]{cwd}[/dim]"),
    ("[red]    '---'[/red]", ""),
]


def get_banner(variant: str = "squirrel", cwd: str = "") -> str:
    """Return Rich-formatted banner string with mascot + app info."""
    mascot = _SQUIRREL if variant == "squirrel" else _GIRL
    lines = []
    for art, info in mascot:
        line = art + info.format(version=__version__, cwd=cwd)
        lines.append(line)
    return "\n".join(lines)
