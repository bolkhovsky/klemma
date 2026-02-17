"""TUI Dashboard — main screen with plan, coverage, stats."""

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widget import Widget
from textual.widgets import DataTable, Footer, Header, Label, Static

from ..config import KlemmaConfig
from ..state import StateManager
from ..vault import VaultAdapter


class StatBox(Static):
    """Small stat display widget."""

    def __init__(self, label: str, value: str, style: str = "", **kwargs):
        super().__init__(**kwargs)
        self.label_text = label
        self.value_text = value
        self.box_style = style

    def compose(self) -> ComposeResult:
        yield Label(f"[bold]{self.value_text}[/bold]\n{self.label_text}")


class DashboardScreen(Widget):
    """Main dashboard screen."""

    def __init__(self, cfg: KlemmaConfig, state: StateManager, vault: VaultAdapter, **kwargs):
        super().__init__(**kwargs)
        self.cfg = cfg
        self.state = state
        self.vault = vault

    def compose(self) -> ComposeResult:
        yield Header()

        stats = self.state.get_stats()
        frag_stats = self.state.get_fragment_stats()
        plan = self.state.get_plan()

        # Stats row
        with Horizontal(id="stats-row"):
            yield StatBox(
                "Sources", str(stats.get("total", 0)),
                classes="stat-box",
            )
            yield StatBox(
                "Completed", str(stats.get("completed", 0)),
                classes="stat-box",
            )
            yield StatBox(
                "Fragments", str(frag_stats.get("total", 0)),
                classes="stat-box",
            )
            yield StatBox(
                "Today", str(stats.get("today", 0)),
                classes="stat-box",
            )

        # Today's plan
        if plan:
            plan_text = (
                f"[bold]Dissertation:[/bold] {plan.get('dissertation_task', 'No plan')}\n"
                f"[bold]Assistant:[/bold] {plan.get('assistant_task', '-')}\n"
                f"[bold]Reading:[/bold] {plan.get('reading_target', '-')}"
            )
        else:
            plan_text = "[dim]No plan for today. Run: klemma morning[/dim]"

        yield Static(plan_text, id="plan-panel")

        # Coverage table
        coverage = self.state.get_coverage_stats()
        coverage_lines = []
        for ch in range(1, 5):
            count = coverage["chapters"].get(ch, 0)
            name = self.cfg.dissertation.chapters.get(ch, "")
            marker = "+" if count >= 10 else "~" if count >= 5 else "-"
            coverage_lines.append(f"  {marker} Ch {ch}: {name} — {count} sources")

        coverage_text = "[bold]Coverage[/bold]\n" + "\n".join(coverage_lines)
        yield Static(coverage_text, id="coverage-panel")

        # Fragment distribution
        if frag_stats["by_chapter"]:
            frag_lines = ["[bold]Fragments by Chapter[/bold]"]
            for ch, cnt in sorted(frag_stats["by_chapter"].items()):
                frag_lines.append(f"  Ch {ch}: {cnt} fragments")
            yield Static("\n".join(frag_lines))

        yield Footer()
