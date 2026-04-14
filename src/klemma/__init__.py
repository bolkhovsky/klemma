"""Klemma — AI academic assistant."""

__version__ = "0.16.0"

_BANNER_LINES = [
    ("#e0f2fe", "██  ██  ██      ██████  ██    ██  ██    ██   ████ "),
    ("#7dd3fc", "██ ██   ██      ██      ███  ███  ███  ███  ██  ██"),
    ("#38bdf8", "████    ██      █████   ████████  ████████  ██████"),
    ("#0369a1", "██ ██   ██      ██      ██ ██ ██  ██ ██ ██  ██  ██"),
    ("#1e3a5f", "██  ██  ██████  ██████  ██    ██  ██    ██  ██  ██"),
]


def get_banner(cwd: str = "", **_kw) -> str:
    """Return Rich-formatted banner string with gradient block-letter logo."""
    lines = []
    for color, text in _BANNER_LINES:
        lines.append(f"[{color}]{text}[/{color}]")
    lines.append("")
    lines.append(f"   AI Academic Assistant — [dim]v{__version__}[/dim]")
    if cwd:
        lines.append(f"   [dim]{cwd}[/dim]")
    return "\n".join(lines)
