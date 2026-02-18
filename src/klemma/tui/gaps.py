"""TUI Gaps — sections with insufficient source coverage."""

from textual.app import ComposeResult
from textual.widget import Widget
from textual.widgets import DataTable, Footer, Header, Static

from ..config import KlemmaConfig
from ..state import StateManager


class GapsScreen(Widget):
    """Sections with insufficient source coverage."""

    def __init__(self, cfg: KlemmaConfig, state: StateManager, **kwargs):
        super().__init__(**kwargs)
        self.cfg = cfg
        self.state = state

    def compose(self) -> ComposeResult:
        yield Header()

        min_sources = self.cfg.dissertation.min_sources_per_section
        gaps = self.state.get_gaps(min_sources=min_sources)

        yield Static(f"[bold]Sections with < {min_sources} sources[/bold]")

        if not gaps:
            yield Static(
                f"\n[green]All sections have >= {min_sources} sources.[/green]"
            )
        else:
            table = DataTable(id="gaps-table")
            table.add_columns("Section", "Sources", "Needed")

            for gap in gaps:
                needed = min_sources - gap["count"]
                table.add_row(
                    gap["section"],
                    str(gap["count"]),
                    f"-{needed}",
                )

            yield table

            yield Static(f"\n[dim]{len(gaps)} sections need more sources[/dim]")

        yield Footer()
