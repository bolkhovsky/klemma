"""Tests for shared interactive confirmation UI (ADR-011)."""

from unittest.mock import patch

from rich.console import Console

from klemma.cli_confirm import ReviewItem, interactive_review


def _items(n=3):
    return [
        ReviewItem(
            key=f"item-{i}",
            header=f"Item {i}",
            details=[("Label", f"Detail {i}")],
            action_label=f"Do thing {i}",
            data={"index": i},
        )
        for i in range(1, n + 1)
    ]


class TestInteractiveReview:

    def test_accept_all_with_yes_flag(self):
        items = _items(3)
        console = Console(file=open("/dev/null", "w"))
        result = interactive_review(items, console=console, yes=True)
        assert len(result.accepted) == 3
        assert result.skipped == 0
        assert result.quit_early is False

    @patch("klemma.cli_confirm.click.prompt", return_value="n")
    def test_skip_all(self, mock_prompt):
        items = _items(2)
        console = Console(file=open("/dev/null", "w"))
        result = interactive_review(items, console=console)
        assert len(result.accepted) == 0
        assert result.skipped == 2

    @patch("klemma.cli_confirm.click.prompt", side_effect=["y", "n", "y"])
    def test_mixed_choices(self, mock_prompt):
        items = _items(3)
        console = Console(file=open("/dev/null", "w"))
        result = interactive_review(items, console=console)
        assert len(result.accepted) == 2
        assert result.skipped == 1
        assert result.accepted[0].key == "item-1"
        assert result.accepted[1].key == "item-3"

    @patch("klemma.cli_confirm.click.prompt", side_effect=["y", "q"])
    def test_quit_early(self, mock_prompt):
        items = _items(5)
        console = Console(file=open("/dev/null", "w"))
        result = interactive_review(items, console=console)
        assert len(result.accepted) == 1
        assert result.quit_early is True

    def test_empty_items(self):
        console = Console(file=open("/dev/null", "w"))
        result = interactive_review([], console=console)
        assert len(result.accepted) == 0
        assert result.skipped == 0

    def test_on_accept_callback(self):
        called_with = []

        def on_accept(item):
            called_with.append(item.key)
            return "[green]Done[/green]"

        items = _items(2)
        console = Console(file=open("/dev/null", "w"))
        result = interactive_review(
            items, console=console, yes=True, on_accept=on_accept,
        )
        assert called_with == ["item-1", "item-2"]
        assert len(result.accepted) == 2

    def test_data_preserved(self):
        items = [ReviewItem(
            key="test",
            header="Test",
            data={"citekey": "smith2023", "section": "1.4"},
        )]
        console = Console(file=open("/dev/null", "w"))
        result = interactive_review(items, console=console, yes=True)
        assert result.accepted[0].data == {"citekey": "smith2023", "section": "1.4"}
