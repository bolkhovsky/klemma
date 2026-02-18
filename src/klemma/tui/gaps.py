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

        # Reference gaps
        ref_gaps = self.state.get_reference_gaps(limit=20)
        yield Static("\n[bold]Reference Gaps (missing from library)[/bold]")

        if ref_gaps:
            ref_table = DataTable(id="ref-gaps-table")
            ref_table.add_columns("Score", "Count", "Authors", "Year", "Title", "Why")

            for g in ref_gaps:
                ref_table.add_row(
                    f"{g['score']:.1f}",
                    str(g["count"]),
                    (g.get("ref_authors") or "")[:20],
                    str(g.get("ref_year") or ""),
                    (g.get("ref_title") or "")[:35],
                    (g.get("why_relevant") or "")[:30],
                )

            yield ref_table
            yield Static(f"\n[dim]{len(ref_gaps)} reference gaps tracked[/dim]")
        else:
            yield Static("\n[dim]No reference gaps tracked yet.[/dim]")

        yield Footer()
