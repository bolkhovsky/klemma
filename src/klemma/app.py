"""Klemma TUI — Textual application."""

from textual.app import App, ComposeResult
from textual.binding import Binding

from .config import KlemmaConfig
from .state import StateManager
from .vault import VaultAdapter


class KlemmaApp(App):
    """Klemma interactive TUI dashboard."""

    TITLE = "Klemma"
    SUB_TITLE = "AI Academic Assistant"
    CSS_PATH = None

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("d", "switch_screen('dashboard')", "Dashboard"),
        Binding("f", "switch_screen('fragments')", "Fragments"),
        Binding("r", "refresh", "Refresh"),
    ]

    DEFAULT_CSS = """
    Screen {
        background: $surface;
    }

    #header-bar {
        dock: top;
        height: 3;
        background: $primary;
        color: $text;
        content-align: center middle;
    }

    #footer-bar {
        dock: bottom;
        height: 1;
        background: $primary-darken-2;
    }

    .stat-box {
        width: 1fr;
        height: 5;
        border: round $primary;
        padding: 1;
        content-align: center middle;
    }

    .section-title {
        text-style: bold;
        margin: 1 0;
        color: $text;
    }

    DataTable {
        height: auto;
        max-height: 20;
    }

    #plan-panel {
        height: auto;
        border: round $accent;
        padding: 1;
        margin: 0 0 1 0;
    }

    #coverage-panel {
        height: auto;
        border: round $success;
        padding: 1;
    }

    #stats-row {
        layout: horizontal;
        height: 5;
        margin: 1 0;
    }
    """

    def __init__(self, cfg: KlemmaConfig, state: StateManager, vault: VaultAdapter, **kwargs):
        super().__init__(**kwargs)
        self.cfg = cfg
        self.state = state
        self.vault = vault

    def compose(self) -> ComposeResult:
        from .tui.dashboard import DashboardScreen
        yield DashboardScreen(self.cfg, self.state, self.vault)

    def action_switch_screen(self, screen_name: str):
        if screen_name == "dashboard":
            from .tui.dashboard import DashboardScreen
            self.query("DashboardScreen, FragmentScreen").remove()
            self.mount(DashboardScreen(self.cfg, self.state, self.vault))
        elif screen_name == "fragments":
            from .tui.fragments import FragmentScreen
            self.query("DashboardScreen, FragmentScreen").remove()
            self.mount(FragmentScreen(self.cfg, self.state))

    def action_refresh(self):
        """Refresh current screen."""
        self.query("DashboardScreen, FragmentScreen").remove()
        from .tui.dashboard import DashboardScreen
        self.mount(DashboardScreen(self.cfg, self.state, self.vault))
