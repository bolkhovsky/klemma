"""TUI Fragments — browse and filter extracted fragments."""

from textual.app import ComposeResult
from textual.widget import Widget
from textual.widgets import DataTable, Footer, Header, Input, Label, Static

from ..config import KlemmaConfig
from ..state import StateManager


class FragmentScreen(Widget):
    """Fragment browser screen with filtering."""

    def __init__(self, cfg: KlemmaConfig, state: StateManager, **kwargs):
        super().__init__(**kwargs)
        self.cfg = cfg
        self.state = state

    def compose(self) -> ComposeResult:
        yield Header()
        yield Label("[bold]Fragment Browser[/bold]  [dim](press 'd' for dashboard)[/dim]")

        # Fragment table
        table = DataTable(id="fragment-table")
        table.add_columns("Source", "Type", "Section", "Rel", "Fragment")

        frags = self.state.get_fragments(limit=50)
        for f in frags:
            table.add_row(
                (f.get("citekey", "?"))[:20],
                f.get("fragment_type", "?"),
                f.get("section", "-"),
                str(f.get("relevance_score", "?")),
                (f.get("fragment_text", ""))[:60],
            )

        yield table

        # Stats
        frag_stats = self.state.get_fragment_stats()
        stats_text = f"Total: {frag_stats['total']}"
        if frag_stats["by_type"]:
            type_parts = [f"{t}: {c}" for t, c in sorted(frag_stats["by_type"].items())]
            stats_text += "  |  " + ", ".join(type_parts)
        yield Static(f"[dim]{stats_text}[/dim]")

        yield Footer()
