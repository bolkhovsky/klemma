"""TUI Stats — processing and fragment statistics."""

from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.widget import Widget
from textual.widgets import DataTable, Footer, Header, Static

from ..config import KlemmaConfig
from ..state import StateManager


class StatsScreen(Widget):
    """Processing and fragment statistics screen."""

    def __init__(self, cfg: KlemmaConfig, state: StateManager, **kwargs):
        super().__init__(**kwargs)
        self.cfg = cfg
        self.state = state

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static("[bold]Processing Statistics[/bold]")

        proc_stats = self.state.get_stats()

        # Processing table
        proc_table = DataTable(id="proc-stats-table")
        proc_table.add_columns("Status", "Count")

        styles = {
            "completed": "green",
            "pending": "yellow",
            "failed": "red",
            "skipped": "dim",
            "processing": "blue",
        }
        for status, count in proc_stats.items():
            if status in ("total", "today"):
                continue
            proc_table.add_row(status.title(), str(count))

        proc_table.add_row("[bold]Total[/bold]", f"[bold]{proc_stats.get('total', 0)}[/bold]")
        proc_table.add_row("Today", str(proc_stats.get("today", 0)))

        yield proc_table

        # Fragment stats
        frag_stats = self.state.get_fragment_stats()
        if frag_stats["total"] > 0:
            yield Static("\n[bold]Fragment Statistics[/bold]")

            frag_table = DataTable(id="frag-stats-table")
            frag_table.add_columns("Category", "Count")
            frag_table.add_row("Total fragments", str(frag_stats["total"]))
            for ftype, cnt in sorted(frag_stats["by_type"].items()):
                frag_table.add_row(f"  {ftype}", str(cnt))

            if frag_stats["by_chapter"]:
                frag_table.add_row("", "")
                for ch, cnt in sorted(frag_stats["by_chapter"].items()):
                    name = self.cfg.dissertation.chapters.get(ch, "")
                    frag_table.add_row(f"  Ch {ch}: {name}", str(cnt))

            yield frag_table
        else:
            yield Static("\n[dim]No fragments extracted yet. Run: klemma extract <citekey>[/dim]")

        yield Footer()
