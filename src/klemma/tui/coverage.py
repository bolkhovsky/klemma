"""TUI Coverage — dissertation coverage by chapter and section."""

from textual.app import ComposeResult
from textual.widget import Widget
from textual.widgets import DataTable, Footer, Header, Static

from ..config import KlemmaConfig
from ..state import StateManager


class CoverageScreen(Widget):
    """Dissertation coverage by chapter and section."""

    def __init__(self, cfg: KlemmaConfig, state: StateManager, **kwargs):
        super().__init__(**kwargs)
        self.cfg = cfg
        self.state = state

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static("[bold]Coverage by Chapter[/bold]")

        cov = self.state.get_coverage_stats()

        # Chapters table
        ch_table = DataTable(id="coverage-chapters")
        ch_table.add_columns("Chapter", "Sources", "Status")

        for ch in sorted(self.cfg.dissertation.chapters.keys()):
            count = cov["chapters"].get(ch, 0)
            name = self.cfg.dissertation.chapters.get(ch, "")
            if count >= 10:
                status = "[green]Good[/green]"
            elif count >= 5:
                status = "[yellow]OK[/yellow]"
            else:
                status = "[red]Low[/red]"
            ch_table.add_row(f"Ch {ch}: {name}", str(count), status)

        yield ch_table

        # Sections table
        if cov["sections"]:
            yield Static("\n[bold]Coverage by Section[/bold]")

            sec_table = DataTable(id="coverage-sections")
            sec_table.add_columns("Section", "Sources", "Status")

            min_sources = self.cfg.dissertation.min_sources_per_section
            for section, count in sorted(cov["sections"].items()):
                if count >= min_sources:
                    status = "[green]OK[/green]"
                elif count >= 1:
                    status = "[yellow]Low[/yellow]"
                else:
                    status = "[red]Gap[/red]"
                sec_table.add_row(section, str(count), status)

            yield sec_table

        # Relevance stats
        if cov.get("nr1"):
            nr1_parts = [f"{k}: {v}" for k, v in sorted(cov["nr1"].items())]
            nr2_parts = [f"{k}: {v}" for k, v in sorted(cov["nr2"].items())]
            yield Static(f"\n[dim]NR1 relevance: {', '.join(nr1_parts)}[/dim]")
            yield Static(f"[dim]NR2 relevance: {', '.join(nr2_parts)}[/dim]")

        yield Footer()
