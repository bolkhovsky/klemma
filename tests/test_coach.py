"""Tests for klemma coach — contextual research advisor (#123)."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from klemma.cli import main as klemma_cli
from klemma.skills.coach import (
    INTENT_BALANCE_THRESHOLD,
    SATURATION_THRESHOLD,
    SOURCE_ADEQUACY_CHAPTER,
    SOURCE_ADEQUACY_SUBSECTION,
    WRITING_READINESS_MIN_SOURCES,
    CoachFinding,
    CoachReport,
    analyze_project,
    analyze_section,
    coach_section_hint,
)


def test_coach_report_defaults():
    report = CoachReport()
    assert report.findings == []
    assert report.section is None


def test_coach_finding_fields():
    f = CoachFinding(
        category="adequacy",
        section="1.1",
        message="Too few sources",
        severity="warning",
    )
    assert f.category == "adequacy"
    assert f.section == "1.1"
    assert f.message == "Too few sources"
    assert f.severity == "warning"


def test_constants():
    assert SOURCE_ADEQUACY_CHAPTER == (15, 30)
    assert SOURCE_ADEQUACY_SUBSECTION == (5, 10)
    assert INTENT_BALANCE_THRESHOLD == 0.7
    assert WRITING_READINESS_MIN_SOURCES == 10
    assert SATURATION_THRESHOLD == 30


class TestAnalyzeSection:
    """Tests for analyze_section()."""

    @pytest.mark.parametrize(
        "level,source_count,expect_warning",
        [
            ("chapter", 10, True),   # below 15
            ("chapter", 15, False),  # at lower bound
            ("chapter", 20, False),  # within range
            ("subsection", 3, True),  # below 5
            ("subsection", 5, False),  # at lower bound
            ("subsection", 8, False),  # within range
        ],
    )
    def test_adequacy_thresholds(self, level, source_count, expect_warning):
        findings = analyze_section(
            section="1.1",
            source_count=source_count,
            level=level,
            intent_counts={},
            fragment_count=0,
            has_draft=False,
        )
        adequacy = [f for f in findings if f.category == "adequacy"]
        assert bool(adequacy) == expect_warning

    def test_saturation(self):
        findings = analyze_section(
            section="1.1",
            source_count=35,
            level="chapter",
            intent_counts={},
            fragment_count=0,
            has_draft=False,
        )
        sat = [f for f in findings if f.category == "saturation"]
        assert len(sat) == 1
        assert sat[0].severity == "action"
        assert "stop adding" in sat[0].message

    def test_intent_balance_unhealthy(self):
        findings = analyze_section(
            section="2.1",
            source_count=20,
            level="chapter",
            intent_counts={"background": 8, "method": 1, "result_comparison": 1},
            fragment_count=10,
            has_draft=False,
        )
        balance = [f for f in findings if f.category == "intent_balance"]
        assert len(balance) == 1
        assert balance[0].severity == "warning"
        assert "80%" in balance[0].message

    def test_intent_balance_healthy(self):
        findings = analyze_section(
            section="2.1",
            source_count=20,
            level="chapter",
            intent_counts={"background": 5, "method": 3, "result_comparison": 2},
            fragment_count=10,
            has_draft=False,
        )
        balance = [f for f in findings if f.category == "intent_balance"]
        assert len(balance) == 0

    def test_writing_readiness_ready(self):
        findings = analyze_section(
            section="3.1",
            source_count=12,
            level="subsection",
            intent_counts={"background": 3, "method": 2, "result_comparison": 1},
            fragment_count=5,
            has_draft=False,
        )
        ready = [f for f in findings if f.category == "writing_readiness"]
        assert len(ready) == 1
        assert ready[0].severity == "info"
        assert "ready to draft" in ready[0].message

    def test_writing_readiness_not_ready_too_few_sources(self):
        findings = analyze_section(
            section="3.1",
            source_count=5,
            level="subsection",
            intent_counts={"background": 3, "method": 2},
            fragment_count=5,
            has_draft=False,
        )
        ready = [f for f in findings if f.category == "writing_readiness"]
        assert len(ready) == 0

    def test_writing_readiness_not_ready_no_fragments(self):
        findings = analyze_section(
            section="3.1",
            source_count=12,
            level="subsection",
            intent_counts={"background": 3, "method": 2},
            fragment_count=0,
            has_draft=False,
        )
        ready = [f for f in findings if f.category == "writing_readiness"]
        assert len(ready) == 0

    def test_writing_readiness_not_ready_no_method_intents(self):
        findings = analyze_section(
            section="3.1",
            source_count=12,
            level="subsection",
            intent_counts={"background": 5},
            fragment_count=5,
            has_draft=False,
        )
        ready = [f for f in findings if f.category == "writing_readiness"]
        assert len(ready) == 0

    def test_writing_readiness_skipped_when_drafted(self):
        findings = analyze_section(
            section="3.1",
            source_count=12,
            level="subsection",
            intent_counts={"background": 3, "method": 2, "result_comparison": 1},
            fragment_count=5,
            has_draft=True,
        )
        ready = [f for f in findings if f.category == "writing_readiness"]
        assert len(ready) == 0

    def test_empty_section(self):
        findings = analyze_section(
            section="4.1",
            source_count=0,
            level="subsection",
            intent_counts={},
            fragment_count=0,
            has_draft=False,
        )
        adequacy = [f for f in findings if f.category == "adequacy"]
        assert len(adequacy) == 1
        # No intent balance or readiness findings for empty section
        assert len(findings) == 1


class TestAnalyzeProject:
    """Tests for analyze_project()."""

    def test_basic_health_check(self):
        report = analyze_project(
            coverage_stats={"sections": {"1.1": 3, "2.1": 20}},
            intent_coverage={
                "1.1": {"background": 3},
                "2.1": {"background": 5, "method": 3, "result_comparison": 2},
            },
            fragment_stats={},
            gap_summary={"open_count": 5, "top_ref": "Smith2020", "top_count": 3},
            section_levels={"1.1": "subsection", "2.1": "chapter"},
            drafts=set(),
        )
        assert isinstance(report, CoachReport)
        assert report.section is None
        categories = {f.category for f in report.findings}
        assert "adequacy" in categories  # 1.1 has 3 sources < 5
        assert "gap_priority" in categories

    def test_gap_priority(self):
        report = analyze_project(
            coverage_stats={"sections": {}},
            intent_coverage={},
            fragment_stats={},
            gap_summary={"open_count": 10, "top_ref": "Jones2021", "top_count": 7},
            section_levels={},
            drafts=set(),
        )
        gaps = [f for f in report.findings if f.category == "gap_priority"]
        assert len(gaps) == 1
        assert "Jones2021" in gaps[0].message
        assert "7×" in gaps[0].message

    def test_no_gaps(self):
        report = analyze_project(
            coverage_stats={"sections": {}},
            intent_coverage={},
            fragment_stats={},
            gap_summary={"open_count": 0},
            section_levels={},
            drafts=set(),
        )
        gaps = [f for f in report.findings if f.category == "gap_priority"]
        assert len(gaps) == 0

    def test_empty_project(self):
        report = analyze_project(
            coverage_stats={},
            intent_coverage={},
            fragment_stats={},
            gap_summary={},
            section_levels={},
            drafts=set(),
        )
        assert report.findings == []


class TestCoachSectionHint:
    """Tests for coach_section_hint()."""

    def test_hint_low_sources(self):
        hint = coach_section_hint(
            section="1.1",
            source_count=2,
            level="subsection",
            intent_counts={},
            fragment_count=0,
            has_draft=False,
        )
        assert hint is not None
        assert "1.1" in hint
        assert "2 sources" in hint

    def test_hint_saturated(self):
        hint = coach_section_hint(
            section="1.1",
            source_count=35,
            level="chapter",
            intent_counts={},
            fragment_count=0,
            has_draft=False,
        )
        assert hint is not None
        assert "stop adding" in hint

    def test_hint_ready_to_draft(self):
        hint = coach_section_hint(
            section="1.1",
            source_count=15,
            level="chapter",
            intent_counts={"background": 3, "method": 2},
            fragment_count=5,
            has_draft=False,
        )
        assert hint is not None
        assert "ready to draft" in hint

    def test_no_hint_when_ok(self):
        hint = coach_section_hint(
            section="1.1",
            source_count=20,
            level="chapter",
            intent_counts={"background": 3, "method": 3, "result_comparison": 4},
            fragment_count=0,
            has_draft=False,
        )
        assert hint is None

    def test_no_readiness_hint_when_drafted(self):
        hint = coach_section_hint(
            section="1.1",
            source_count=15,
            level="chapter",
            intent_counts={"background": 3, "method": 2},
            fragment_count=5,
            has_draft=True,
        )
        # With draft, no readiness finding; sources in range, intents balanced
        assert hint is None

    def test_priority_action_over_warning(self):
        """Action severity should be returned over warning."""
        hint = coach_section_hint(
            section="1.1",
            source_count=35,
            level="chapter",
            intent_counts={"background": 9, "method": 1},
            fragment_count=5,
            has_draft=False,
        )
        assert hint is not None
        # Saturation (action) should take priority over intent_balance (warning)
        assert "stop adding" in hint


# --- CLI tests ---


def _make_coach_ctx(state, vault=None):
    mock_ctx = MagicMock()
    mock_ctx.state = state
    mock_ctx.config = MagicMock()
    mock_ctx.config.obsidian.notes_folder = ""
    mock_ctx.vault = vault
    mock_ctx.project_root = Path("/tmp/test_project")
    mock_ctx.project = MagicMock()
    mock_ctx.project.type = "dissertation"
    return mock_ctx


class TestCoachCLI:
    def test_coach_help(self):
        runner = CliRunner()
        result = runner.invoke(klemma_cli, ["coach", "--help"])
        assert result.exit_code == 0

    def test_coach_default_health_check(self, tmp_path):
        from klemma.state import StateManager

        db = tmp_path / "state.db"
        sm = StateManager(db)
        sm.register_sources(["paper1", "paper2"])
        sm.mark_completed("paper1", "n/a")
        sm.set_source_sections("paper1", ["1.1"], [1])

        mock_ctx = _make_coach_ctx(sm)
        runner = CliRunner()
        with (
            patch("klemma.cli._get_context", return_value=mock_ctx),
            patch("klemma.cli._init_components", return_value=mock_ctx),
            patch("klemma.cli.discover_project_root", return_value=tmp_path),
            patch("klemma.cli._sync_sections"),
        ):
            result = runner.invoke(klemma_cli, ["coach"])
        assert result.exit_code == 0, result.output

    def test_coach_section_focus(self, tmp_path):
        from klemma.state import StateManager

        db = tmp_path / "state.db"
        sm = StateManager(db)
        sm.register_sources(["p1", "p2", "p3"])
        for p in ["p1", "p2", "p3"]:
            sm.mark_completed(p, "n/a")
            sm.set_source_sections(p, ["1.1"], [1])

        mock_ctx = _make_coach_ctx(sm)
        runner = CliRunner()
        with (
            patch("klemma.cli._get_context", return_value=mock_ctx),
            patch("klemma.cli._init_components", return_value=mock_ctx),
            patch("klemma.cli.discover_project_root", return_value=tmp_path),
            patch("klemma.cli._sync_sections"),
        ):
            result = runner.invoke(klemma_cli, ["coach", "-s", "1.1"])
        assert result.exit_code == 0, result.output
        assert "1.1" in result.output

    def test_coach_json_output(self, tmp_path):
        import json

        from klemma.state import StateManager

        db = tmp_path / "state.db"
        sm = StateManager(db)

        mock_ctx = _make_coach_ctx(sm)
        runner = CliRunner()
        with (
            patch("klemma.cli._get_context", return_value=mock_ctx),
            patch("klemma.cli._init_components", return_value=mock_ctx),
            patch("klemma.cli.discover_project_root", return_value=tmp_path),
            patch("klemma.cli._sync_sections"),
        ):
            result = runner.invoke(klemma_cli, ["coach", "--json"])
        assert result.exit_code == 0, result.output
        # Extract JSON from output (status line may precede it)
        json_start = result.output.index("{")
        data = json.loads(result.output[json_start:])
        assert "findings" in data


class TestCoachHintInAdd:
    def test_add_shows_coach_hint(self, tmp_path):
        from klemma.state import StateManager

        db = tmp_path / "state.db"
        sm = StateManager(db)
        sm.register_sources(["paper1"])
        sm.mark_completed("paper1", "notes/paper1.md")

        mock_ctx = _make_coach_ctx(sm)
        runner = CliRunner()
        with (
            patch("klemma.cli._get_context", return_value=mock_ctx),
            patch("klemma.cli._init_components", return_value=mock_ctx),
            patch("klemma.cli.discover_project_root", return_value=tmp_path),
        ):
            result = runner.invoke(klemma_cli, ["add", "paper1", "--section", "1.1"])
        assert result.exit_code == 0
        # With only 1 source, coach should hint about low adequacy
        assert "sources" in result.output.lower() or "1.1" in result.output
