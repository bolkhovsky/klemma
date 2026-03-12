# `klemma coach` Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a `klemma coach` command that gives methodology-driven research guidance using pure heuristics (zero AI calls), plus inline hints in `add`, `draft`, and `research` commands.

**Architecture:** New `skills/coach.py` skill with `CoachReport` dataclass. Receives pre-computed data via arguments (no state.py imports). CLI gathers data, calls skill, formats output. Shared `_coach_section_hint()` helper in cli.py for inline hints across 3 commands.

**Tech Stack:** Python, Click CLI, dataclasses, pytest with parametrize

---

### Task 1: `skills/coach.py` — CoachReport dataclass + constants

**Files:**
- Create: `src/klemma/skills/coach.py`
- Test: `tests/test_coach.py`

**Step 1: Write the failing test**

```python
# tests/test_coach.py
"""Tests for klemma coach — contextual research advisor (#123)."""

from klemma.skills.coach import (
    INTENT_BALANCE_THRESHOLD,
    SOURCE_ADEQUACY_CHAPTER,
    SOURCE_ADEQUACY_SUBSECTION,
    CoachFinding,
    CoachReport,
)


def test_coach_report_defaults():
    """CoachReport has empty defaults."""
    report = CoachReport()
    assert report.findings == []
    assert report.section is None


def test_coach_finding_fields():
    """CoachFinding has required fields."""
    f = CoachFinding(
        category="adequacy",
        section="1.1",
        message="Section 1.1 has 25 sources — consider starting a draft",
        severity="info",
    )
    assert f.category == "adequacy"
    assert f.severity == "info"


def test_constants():
    """Heuristic thresholds from methodology papers."""
    assert SOURCE_ADEQUACY_CHAPTER == (15, 30)
    assert SOURCE_ADEQUACY_SUBSECTION == (5, 10)
    assert INTENT_BALANCE_THRESHOLD == 0.7
```

**Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_coach.py::test_coach_report_defaults -v`
Expected: FAIL — module not found

**Step 3: Write minimal implementation**

```python
# src/klemma/skills/coach.py
"""Contextual research advisor — methodology-driven heuristics (#123).

Zero AI calls. Thresholds derived from 21 methodology papers:
- Pautasso 2013: source adequacy (15-30/chapter, 5-10/subsection)
- Cohan 2019: citation intent balance (<70% background = healthy)
- Kallestinova 2011: writing readiness (>10 sources + fragments + intents)
"""

import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# --- Heuristic thresholds (from methodology papers) ---

SOURCE_ADEQUACY_CHAPTER: tuple[int, int] = (15, 30)  # Pautasso 2013
SOURCE_ADEQUACY_SUBSECTION: tuple[int, int] = (5, 10)  # Pautasso 2013
INTENT_BALANCE_THRESHOLD: float = 0.7  # Cohan 2019: <70% background = healthy
WRITING_READINESS_MIN_SOURCES: int = 10  # Kallestinova 2011
SATURATION_THRESHOLD: int = 30  # above this, stop adding


@dataclass
class CoachFinding:
    """Single actionable finding from coach analysis."""

    category: str  # adequacy, intent_balance, writing_readiness, saturation, gap_priority
    section: str | None  # section ID or None for project-wide
    message: str  # human-readable recommendation
    severity: str  # info, warning, action


@dataclass
class CoachReport:
    """Structured coach analysis results."""

    findings: list[CoachFinding] = field(default_factory=list)
    section: str | None = None  # None = project-wide health check
```

**Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_coach.py -v`
Expected: PASS (3 tests)

**Step 5: Commit**

```bash
git add src/klemma/skills/coach.py tests/test_coach.py
git commit -m "feat(coach): add CoachReport dataclass and heuristic constants (#123)"
```

---

### Task 2: `analyze_section()` — section-level heuristic analysis

**Files:**
- Modify: `src/klemma/skills/coach.py`
- Modify: `tests/test_coach.py`

**Step 1: Write the failing tests**

```python
# append to tests/test_coach.py
import pytest

from klemma.skills.coach import analyze_section


class TestAnalyzeSection:
    """Test section-level heuristic analysis."""

    @pytest.mark.parametrize(
        "source_count,level,expected_category",
        [
            (3, "subsection", "adequacy"),       # below min (5)
            (7, "subsection", None),             # within range — no finding
            (35, "chapter", "saturation"),        # above max (30)
            (20, "chapter", None),                # within range
            (2, "chapter", "adequacy"),           # below min (15)
        ],
    )
    def test_source_adequacy(self, source_count, level, expected_category):
        """Source adequacy heuristic (Pautasso 2013)."""
        findings = analyze_section(
            section="1.1",
            source_count=source_count,
            level=level,
            intent_counts={},
            fragment_count=0,
            has_draft=False,
        )
        adequacy = [f for f in findings if f.category in ("adequacy", "saturation")]
        if expected_category:
            assert len(adequacy) == 1
            assert adequacy[0].category == expected_category
        else:
            assert len(adequacy) == 0

    def test_intent_balance_unhealthy(self):
        """Too many background citations (Cohan 2019)."""
        findings = analyze_section(
            section="2.1",
            source_count=20,
            level="subsection",
            intent_counts={"background": 18, "method": 1, "result_comparison": 1},
            fragment_count=20,
            has_draft=False,
        )
        balance = [f for f in findings if f.category == "intent_balance"]
        assert len(balance) == 1
        assert "background" in balance[0].message

    def test_intent_balance_healthy(self):
        """Healthy intent mix — no finding."""
        findings = analyze_section(
            section="2.1",
            source_count=10,
            level="subsection",
            intent_counts={"background": 5, "method": 3, "result_comparison": 2},
            fragment_count=10,
            has_draft=False,
        )
        balance = [f for f in findings if f.category == "intent_balance"]
        assert len(balance) == 0

    def test_writing_readiness_ready(self):
        """Section ready to draft (Kallestinova 2011)."""
        findings = analyze_section(
            section="1.1",
            source_count=15,
            level="subsection",
            intent_counts={"background": 5, "method": 5, "result_comparison": 5},
            fragment_count=20,
            has_draft=False,
        )
        ready = [f for f in findings if f.category == "writing_readiness"]
        assert len(ready) == 1
        assert ready[0].severity == "info"

    def test_writing_readiness_not_ready(self):
        """Section not ready — too few sources, no fragments."""
        findings = analyze_section(
            section="3.1",
            source_count=3,
            level="subsection",
            intent_counts={},
            fragment_count=0,
            has_draft=False,
        )
        ready = [f for f in findings if f.category == "writing_readiness"]
        assert len(ready) == 0  # not ready = no "ready to draft" finding

    def test_empty_section(self):
        """Section with zero sources — adequacy warning only."""
        findings = analyze_section(
            section="4.1",
            source_count=0,
            level="subsection",
            intent_counts={},
            fragment_count=0,
            has_draft=False,
        )
        assert len(findings) >= 1
        assert findings[0].category == "adequacy"
```

**Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_coach.py::TestAnalyzeSection -v`
Expected: FAIL — `analyze_section` not found

**Step 3: Write implementation**

Add to `src/klemma/skills/coach.py`:

```python
def analyze_section(
    section: str,
    source_count: int,
    level: str,  # "chapter" or "subsection"
    intent_counts: dict[str, int],  # {background: N, method: N, ...}
    fragment_count: int,
    has_draft: bool,
) -> list[CoachFinding]:
    """Analyze a single section and return actionable findings.

    Uses heuristics from methodology papers — zero AI calls.
    """
    findings: list[CoachFinding] = []
    min_src, max_src = (
        SOURCE_ADEQUACY_CHAPTER if level == "chapter" else SOURCE_ADEQUACY_SUBSECTION
    )

    # 1. Source adequacy (Pautasso 2013)
    if source_count < min_src:
        findings.append(CoachFinding(
            category="adequacy",
            section=section,
            message=(
                f"Section {section} has {source_count} sources "
                f"(recommended: {min_src}–{max_src})"
            ),
            severity="warning",
        ))
    elif source_count > SATURATION_THRESHOLD:
        findings.append(CoachFinding(
            category="saturation",
            section=section,
            message=(
                f"Section {section} has {source_count} sources — "
                f"stop adding, start writing"
            ),
            severity="action",
        ))

    # 2. Citation intent balance (Cohan 2019)
    total_intents = sum(intent_counts.values())
    if total_intents > 0:
        bg_ratio = intent_counts.get("background", 0) / total_intents
        if bg_ratio > INTENT_BALANCE_THRESHOLD:
            pct = int(bg_ratio * 100)
            findings.append(CoachFinding(
                category="intent_balance",
                section=section,
                message=(
                    f"Section {section}: {pct}% background citations — "
                    f"need more method/result comparisons"
                ),
                severity="warning",
            ))

    # 3. Writing readiness (Kallestinova 2011)
    has_intents = any(
        intent_counts.get(k, 0) > 0
        for k in ("method", "result_comparison")
    )
    if (
        source_count >= WRITING_READINESS_MIN_SOURCES
        and fragment_count > 0
        and has_intents
        and not has_draft
    ):
        findings.append(CoachFinding(
            category="writing_readiness",
            section=section,
            message=(
                f"Section {section} ready to draft: "
                f"{source_count} sources, {fragment_count} fragments"
            ),
            severity="info",
        ))

    return findings
```

**Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_coach.py -v`
Expected: PASS (all tests)

**Step 5: Commit**

```bash
git add src/klemma/skills/coach.py tests/test_coach.py
git commit -m "feat(coach): add analyze_section() heuristic analysis (#123)"
```

---

### Task 3: `analyze_project()` — project-wide health check

**Files:**
- Modify: `src/klemma/skills/coach.py`
- Modify: `tests/test_coach.py`

**Step 1: Write the failing tests**

```python
# append to tests/test_coach.py
from klemma.skills.coach import analyze_project


class TestAnalyzeProject:
    """Test project-wide health check."""

    def test_basic_health_check(self):
        """Returns CoachReport with findings across sections."""
        report = analyze_project(
            coverage_stats={
                "sections": {"1.1": 25, "2.1": 3, "3.1": 0},
            },
            intent_coverage={
                "1.1": {"background": 20, "method": 3, "result_comparison": 2},
                "2.1": {"background": 3},
            },
            fragment_stats={"total": 50, "embedded": 40},
            gap_summary={"open_count": 5, "top_ref": "Tilling 2016", "top_count": 3},
            section_levels={"1.1": "subsection", "2.1": "subsection", "3.1": "subsection"},
            drafts=set(),
        )
        assert isinstance(report, CoachReport)
        assert report.section is None  # project-wide
        assert len(report.findings) > 0

    def test_gap_priority_included(self):
        """Top ref-gap included in findings."""
        report = analyze_project(
            coverage_stats={"sections": {"1.1": 10}},
            intent_coverage={},
            fragment_stats={"total": 10, "embedded": 10},
            gap_summary={"open_count": 5, "top_ref": "Tilling 2016", "top_count": 3},
            section_levels={"1.1": "subsection"},
            drafts=set(),
        )
        gap_findings = [f for f in report.findings if f.category == "gap_priority"]
        assert len(gap_findings) == 1
        assert "Tilling 2016" in gap_findings[0].message

    def test_no_gaps_no_finding(self):
        """No gap findings when no open gaps."""
        report = analyze_project(
            coverage_stats={"sections": {"1.1": 10}},
            intent_coverage={},
            fragment_stats={"total": 10, "embedded": 10},
            gap_summary={"open_count": 0, "top_ref": None, "top_count": 0},
            section_levels={"1.1": "subsection"},
            drafts=set(),
        )
        gap_findings = [f for f in report.findings if f.category == "gap_priority"]
        assert len(gap_findings) == 0

    def test_empty_project(self):
        """Empty project returns minimal report."""
        report = analyze_project(
            coverage_stats={"sections": {}},
            intent_coverage={},
            fragment_stats={"total": 0, "embedded": 0},
            gap_summary={"open_count": 0, "top_ref": None, "top_count": 0},
            section_levels={},
            drafts=set(),
        )
        assert isinstance(report, CoachReport)
        assert len(report.findings) == 0
```

**Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_coach.py::TestAnalyzeProject -v`
Expected: FAIL — `analyze_project` not found

**Step 3: Write implementation**

Add to `src/klemma/skills/coach.py`:

```python
def analyze_project(
    coverage_stats: dict,
    intent_coverage: dict[str, dict[str, int]],
    fragment_stats: dict,
    gap_summary: dict,
    section_levels: dict[str, str],  # {section: "chapter"|"subsection"}
    drafts: set[str],  # set of section IDs with existing drafts
) -> CoachReport:
    """Project-wide health check — analyzes all sections + ref-gaps.

    Zero AI calls. Uses existing state data passed via arguments.
    """
    findings: list[CoachFinding] = []

    # Per-section analysis
    sections = coverage_stats.get("sections", {})
    for sec, count in sorted(sections.items()):
        level = section_levels.get(sec, "subsection")
        intents = intent_coverage.get(sec, {})
        # Fragment count: sum of intents + unclassified
        frag_count = sum(intents.values()) if intents else 0
        has_draft = sec in drafts
        findings.extend(analyze_section(
            section=sec,
            source_count=count,
            level=level,
            intent_counts=intents,
            fragment_count=frag_count,
            has_draft=has_draft,
        ))

    # Ref-gap prioritization
    open_count = gap_summary.get("open_count", 0)
    top_ref = gap_summary.get("top_ref")
    top_count = gap_summary.get("top_count", 0)
    if open_count > 0 and top_ref:
        findings.append(CoachFinding(
            category="gap_priority",
            section=None,
            message=(
                f"Resolve {top_ref} — cited {top_count}× across sources "
                f"({open_count} open gaps total)"
            ),
            severity="action",
        ))

    return CoachReport(findings=findings)
```

**Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_coach.py -v`
Expected: PASS (all tests)

**Step 5: Commit**

```bash
git add src/klemma/skills/coach.py tests/test_coach.py
git commit -m "feat(coach): add analyze_project() health check (#123)"
```

---

### Task 4: `coach_section_hint()` — 1-line hint generator

**Files:**
- Modify: `src/klemma/skills/coach.py`
- Modify: `tests/test_coach.py`

**Step 1: Write the failing tests**

```python
# append to tests/test_coach.py
from klemma.skills.coach import coach_section_hint


class TestCoachSectionHint:
    """Test 1-line hint generator for inline use."""

    def test_hint_low_sources(self):
        """Hint when section has too few sources."""
        hint = coach_section_hint(
            section="2.1",
            source_count=3,
            level="subsection",
            intent_counts={},
            fragment_count=0,
            has_draft=False,
        )
        assert hint is not None
        assert "2.1" in hint
        assert "sources" in hint

    def test_hint_saturated(self):
        """Hint when section is saturated."""
        hint = coach_section_hint(
            section="1.1",
            source_count=35,
            level="subsection",
            intent_counts={"background": 30, "method": 5},
            fragment_count=35,
            has_draft=False,
        )
        assert hint is not None
        assert "draft" in hint.lower() or "writing" in hint.lower() or "stop" in hint.lower()

    def test_hint_ready_to_draft(self):
        """Hint when section is ready to draft."""
        hint = coach_section_hint(
            section="1.1",
            source_count=15,
            level="subsection",
            intent_counts={"background": 5, "method": 5, "result_comparison": 5},
            fragment_count=20,
            has_draft=False,
        )
        assert hint is not None
        assert "draft" in hint.lower()

    def test_no_hint_when_ok(self):
        """No hint when section is in normal range."""
        hint = coach_section_hint(
            section="1.1",
            source_count=8,
            level="subsection",
            intent_counts={"background": 3, "method": 3, "result_comparison": 2},
            fragment_count=10,
            has_draft=False,
        )
        assert hint is None

    def test_no_hint_already_drafted(self):
        """No writing_readiness hint when draft exists."""
        hint = coach_section_hint(
            section="1.1",
            source_count=15,
            level="subsection",
            intent_counts={"background": 5, "method": 5, "result_comparison": 5},
            fragment_count=20,
            has_draft=True,
        )
        # Should not get "ready to draft" since already drafted
        # May still get other hints (intent balance, etc.) or None
        if hint:
            assert "ready to draft" not in hint.lower()
```

**Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_coach.py::TestCoachSectionHint -v`
Expected: FAIL — `coach_section_hint` not found

**Step 3: Write implementation**

Add to `src/klemma/skills/coach.py`:

```python
def coach_section_hint(
    section: str,
    source_count: int,
    level: str,
    intent_counts: dict[str, int],
    fragment_count: int,
    has_draft: bool,
) -> str | None:
    """Generate a 1-line hint for a section, or None if nothing to say.

    Used by `add`, `draft`, `research` for inline guidance.
    """
    findings = analyze_section(
        section=section,
        source_count=source_count,
        level=level,
        intent_counts=intent_counts,
        fragment_count=fragment_count,
        has_draft=has_draft,
    )
    if not findings:
        return None

    # Pick highest-priority finding: action > warning > info
    priority = {"action": 0, "warning": 1, "info": 2}
    findings.sort(key=lambda f: priority.get(f.severity, 3))
    return findings[0].message
```

**Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_coach.py -v`
Expected: PASS (all tests)

**Step 5: Commit**

```bash
git add src/klemma/skills/coach.py tests/test_coach.py
git commit -m "feat(coach): add coach_section_hint() for inline guidance (#123)"
```

---

### Task 5: `klemma coach` CLI command

**Files:**
- Modify: `src/klemma/cli.py`
- Modify: `tests/test_coach.py`

**Step 1: Write the failing test**

```python
# append to tests/test_coach.py
from unittest.mock import MagicMock, patch
from pathlib import Path

from click.testing import CliRunner

from klemma.cli import main as klemma_cli
from klemma.state import StateManager


def _make_coach_ctx(state, vault=None):
    """Helper to build mock KlemmaContext for coach tests."""
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
    """Test klemma coach CLI command."""

    def test_coach_help(self):
        """klemma coach --help is accessible."""
        runner = CliRunner()
        result = runner.invoke(klemma_cli, ["coach", "--help"])
        assert result.exit_code == 0
        assert "research advisor" in result.output.lower() or "coach" in result.output.lower()

    def test_coach_default_health_check(self, tmp_path):
        """klemma coach shows project health check."""
        db = tmp_path / "state.db"
        sm = StateManager(db)
        sm.register_sources(["paper1", "paper2", "paper3"])
        sm.mark_completed("paper1", "n/a")
        sm.mark_completed("paper2", "n/a")
        sm.set_source_sections("paper1", ["1.1"], [1])
        sm.set_source_sections("paper2", ["1.1"], [1])

        mock_ctx = _make_coach_ctx(sm)
        runner = CliRunner()
        with patch("klemma.cli._get_context", return_value=mock_ctx), \
             patch("klemma.cli._init_components", return_value=mock_ctx), \
             patch("klemma.cli.discover_project_root", return_value=tmp_path), \
             patch("klemma.cli._sync_sections"):
            result = runner.invoke(klemma_cli, ["coach"])

        assert result.exit_code == 0, result.output

    def test_coach_section_focus(self, tmp_path):
        """klemma coach -s 1.1 shows section analysis."""
        db = tmp_path / "state.db"
        sm = StateManager(db)
        sm.register_sources(["p1", "p2", "p3"])
        for p in ["p1", "p2", "p3"]:
            sm.mark_completed(p, "n/a")
            sm.set_source_sections(p, ["1.1"], [1])

        mock_ctx = _make_coach_ctx(sm)
        runner = CliRunner()
        with patch("klemma.cli._get_context", return_value=mock_ctx), \
             patch("klemma.cli._init_components", return_value=mock_ctx), \
             patch("klemma.cli.discover_project_root", return_value=tmp_path), \
             patch("klemma.cli._sync_sections"):
            result = runner.invoke(klemma_cli, ["coach", "-s", "1.1"])

        assert result.exit_code == 0, result.output
        assert "1.1" in result.output

    def test_coach_json_output(self, tmp_path):
        """klemma coach --json outputs valid JSON."""
        import json

        db = tmp_path / "state.db"
        sm = StateManager(db)

        mock_ctx = _make_coach_ctx(sm)
        runner = CliRunner()
        with patch("klemma.cli._get_context", return_value=mock_ctx), \
             patch("klemma.cli._init_components", return_value=mock_ctx), \
             patch("klemma.cli.discover_project_root", return_value=tmp_path), \
             patch("klemma.cli._sync_sections"):
            result = runner.invoke(klemma_cli, ["coach", "--json"])

        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert "findings" in data
```

**Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_coach.py::TestCoachCLI -v`
Expected: FAIL — coach command not found

**Step 3: Write implementation**

Add to `src/klemma/cli.py` (after the `add` command, before `acquire`):

```python
@main.command()
@click.option("--section", "-s", help="Focus on a specific section")
@click.option("--json", "json_output", is_flag=True, help="Output as JSON")
@click.pass_context
def coach(ctx, section, json_output):
    """Research advisor — methodology-driven guidance (zero AI).

    Default: project-wide health check.
    With -s: section-specific analysis.
    """
    import json as json_mod

    from .skills.coach import analyze_project, analyze_section, CoachReport

    kctx = _get_context(ctx)
    state = kctx.state

    _sync_sections(kctx)

    if section:
        # Section focus mode
        sources = state.get_section_sources(section)
        source_count = len(sources)
        intent = state.get_intent_coverage().get(section, {})
        frags = state.get_fragments(section=section)
        fragment_count = len(frags)
        # Detect level: "chapter" if section has no dot, else "subsection"
        level = "chapter" if "." not in section else "subsection"
        has_draft = (
            kctx.project_root
            and (kctx.project_root / "notes" / "drafts" / f"Draft_{section}.md").exists()
        ) if kctx.project_root else False

        findings = analyze_section(
            section=section,
            source_count=source_count,
            level=level,
            intent_counts=intent,
            fragment_count=fragment_count,
            has_draft=has_draft,
        )
        report = CoachReport(findings=findings, section=section)
    else:
        # Project-wide health check
        coverage = state.get_coverage_stats()
        intent_coverage = state.get_intent_coverage()
        fragment_stats = state.get_fragment_embedding_stats()
        gap_summary = state.get_gap_summary()

        # Determine section levels
        sections = coverage.get("sections", {})
        section_levels = {
            s: ("chapter" if "." not in s else "subsection")
            for s in sections
        }

        # Find existing drafts
        drafts: set[str] = set()
        if kctx.project_root:
            drafts_dir = kctx.project_root / "notes" / "drafts"
            if drafts_dir.exists():
                for f in drafts_dir.glob("Draft_*.md"):
                    sec_id = f.stem.replace("Draft_", "")
                    drafts.add(sec_id)

        report = analyze_project(
            coverage_stats=coverage,
            intent_coverage=intent_coverage,
            fragment_stats=fragment_stats,
            gap_summary=gap_summary,
            section_levels=section_levels,
            drafts=drafts,
        )

    # Output
    if json_output:
        data = {
            "section": report.section,
            "findings": [
                {
                    "category": f.category,
                    "section": f.section,
                    "message": f.message,
                    "severity": f.severity,
                }
                for f in report.findings
            ],
        }
        click.echo(json_mod.dumps(data, indent=2))
        return

    if not report.findings:
        console.print("[green]All sections look good.[/green]")
        return

    for f in report.findings:
        style = {
            "action": "bold red",
            "warning": "yellow",
            "info": "dim",
        }.get(f.severity, "")
        prefix = {
            "action": "→",
            "warning": "⚠",
            "info": "ℹ",
        }.get(f.severity, "•")
        console.print(f"  [{style}]{prefix} {f.message}[/{style}]")
```

**Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_coach.py -v`
Expected: PASS (all tests)

**Step 5: Run ruff**

Run: `ruff check src/klemma/cli.py tests/test_coach.py`
Expected: clean

**Step 6: Commit**

```bash
git add src/klemma/cli.py tests/test_coach.py
git commit -m "feat(coach): add klemma coach CLI command (#123)"
```

---

### Task 6: `_coach_section_hint()` CLI helper + integration in `add`

**Files:**
- Modify: `src/klemma/cli.py`
- Modify: `tests/test_coach.py` (or `tests/test_cli_add.py`)

**Step 1: Write the failing test**

```python
# append to tests/test_coach.py (or tests/test_cli_add.py)

class TestCoachHintInAdd:
    """Test coach hint appended to klemma add output."""

    def test_add_shows_coach_hint(self, tmp_path):
        """klemma add <citekey> --section 1.1 shows coach hint when section has few sources."""
        db = tmp_path / "state.db"
        sm = StateManager(db)
        sm.register_sources(["paper1"])
        sm.mark_completed("paper1", "notes/paper1.md")

        mock_ctx = _make_coach_ctx(sm)
        runner = CliRunner()
        with patch("klemma.cli._get_context", return_value=mock_ctx), \
             patch("klemma.cli._init_components", return_value=mock_ctx), \
             patch("klemma.cli.discover_project_root", return_value=tmp_path):
            result = runner.invoke(klemma_cli, ["add", "paper1", "--section", "1.1"])

        assert result.exit_code == 0
        # With only 1 source, coach should hint about low adequacy
        assert "1.1" in result.output
        assert "sources" in result.output.lower()
```

**Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_coach.py::TestCoachHintInAdd -v`
Expected: FAIL — no hint in output

**Step 3: Write implementation**

Add helper to `src/klemma/cli.py` (near other helper functions):

```python
def _coach_section_hint(state, section: str, project_root=None) -> str | None:
    """Generate 1-line coach hint for a section. Returns None if nothing to say."""
    from .skills.coach import coach_section_hint

    sources = state.get_section_sources(section)
    intent = state.get_intent_coverage().get(section, {})
    frags = state.get_fragments(section=section)
    level = "chapter" if "." not in section else "subsection"
    has_draft = (
        project_root
        and (project_root / "notes" / "drafts" / f"Draft_{section}.md").exists()
    ) if project_root else False

    return coach_section_hint(
        section=section,
        source_count=len(sources),
        level=level,
        intent_counts=intent,
        fragment_count=len(frags),
        has_draft=has_draft,
    )
```

Then in the `add()` command, after the summary line (line ~4659), insert:

```python
    # --- Coach hint ---
    if sections and citekey:
        for sec in sections:
            hint = _coach_section_hint(state, sec, kctx.project_root)
            if hint:
                console.print(f"[dim]💡 {hint}[/dim]")
                break  # one hint is enough
```

**Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_coach.py tests/test_cli_add.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/klemma/cli.py tests/test_coach.py
git commit -m "feat(coach): add inline hint to klemma add (#123)"
```

---

### Task 7: Coach hint in `draft` and `research`

**Files:**
- Modify: `src/klemma/cli.py`
- Modify: `tests/test_coach.py`

**Step 1: Write the failing tests**

```python
# append to tests/test_coach.py

class TestCoachHintInDraft:
    """Test coach hint in draft command."""

    def test_draft_shows_pre_draft_hint(self, tmp_path):
        """klemma draft -s 2.1 shows hint when section has too few sources."""
        db = tmp_path / "state.db"
        sm = StateManager(db)
        # Only 2 sources — below threshold
        sm.register_sources(["p1", "p2"])
        sm.mark_completed("p1", "n/a")
        sm.mark_completed("p2", "n/a")
        sm.set_source_sections("p1", ["2.1"], [2])
        sm.set_source_sections("p2", ["2.1"], [2])

        mock_ctx = _make_coach_ctx(sm)
        mock_ctx.embeddings = None
        mock_ctx.project_root = tmp_path

        runner = CliRunner()
        with patch("klemma.cli._get_context", return_value=mock_ctx), \
             patch("klemma.cli._init_components", return_value=mock_ctx), \
             patch("klemma.cli.discover_project_root", return_value=tmp_path), \
             patch("klemma.cli._sync_sections"), \
             patch("klemma.cli._init_ai", side_effect=Exception("no AI")):
            result = runner.invoke(klemma_cli, ["draft", "-s", "2.1"])

        # Draft will fail (no AI) but hint should appear before AI call
        assert "2.1" in result.output
        # The hint about low sources should be present
        assert "sources" in result.output.lower()
```

**Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_coach.py::TestCoachHintInDraft -v`
Expected: FAIL

**Step 3: Write implementation**

In `src/klemma/cli.py`, in the `draft()` function, after `_sync_sections()` call and before the AI init, add:

```python
    # Coach hint (informational, before AI call)
    hint = _coach_section_hint(state, section, kctx.project_root)
    if hint:
        console.print(f"[dim]💡 {hint}[/dim]")
```

In the `research()` function, at the very end (after save), add:

```python
    # Coach hint (informational, after research)
    if section:
        hint = _coach_section_hint(state, section, kctx.project_root)
        if hint:
            console.print(f"\n[dim]💡 {hint}[/dim]")
```

**Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_coach.py -v`
Expected: PASS

**Step 5: Run full test suite**

Run: `python -m pytest tests/ -q`
Expected: all pass

**Step 6: Commit**

```bash
git add src/klemma/cli.py tests/test_coach.py
git commit -m "feat(coach): add inline hints to draft and research (#123)"
```

---

### Task 8: Documentation updates

**Files:**
- Modify: `src/klemma/skills/CLAUDE.md`
- Modify: `src/klemma/CLAUDE.md`
- Modify: `tests/CLAUDE.md`

**Step 1: Update `src/klemma/skills/CLAUDE.md`**

Add after the `work_context.py` entry:

```markdown
### coach.py (~100 lines)
Contextual research advisor — methodology-driven heuristics (zero AI calls). Thresholds from 21 methodology papers (Pautasso 2013, Cohan 2019, Kallestinova 2011).
- `CoachFinding` — dataclass: category, section, message, severity
- `CoachReport` — dataclass: findings list, optional section focus
- `analyze_section(section, source_count, level, intent_counts, fragment_count, has_draft)` → `list[CoachFinding]` — per-section heuristics: adequacy (Pautasso), intent balance (Cohan), writing readiness (Kallestinova), saturation
- `analyze_project(coverage_stats, intent_coverage, fragment_stats, gap_summary, section_levels, drafts)` → `CoachReport` — project-wide health check: iterates all sections + ref-gap priority
- `coach_section_hint(section, source_count, level, intent_counts, fragment_count, has_draft)` → `str | None` — 1-line hint for inline use in `add`, `draft`, `research`
- Constants: `SOURCE_ADEQUACY_CHAPTER` (15–30), `SOURCE_ADEQUACY_SUBSECTION` (5–10), `INTENT_BALANCE_THRESHOLD` (0.7), `WRITING_READINESS_MIN_SOURCES` (10), `SATURATION_THRESHOLD` (30)
```

**Step 2: Update `src/klemma/CLAUDE.md`**

Add to cli.py entry:
```
- `coach` command: methodology-driven research advisor (zero AI). Default: project-wide health check. `-s X.X`: section focus. `--json`: structured output
- `_coach_section_hint(state, section, project_root)` — generates 1-line hint for inline use in `add`, `draft`, `research`
```

**Step 3: Update `tests/CLAUDE.md`**

Add entry:
```
- `test_coach.py` (~200 lines) — coach skill heuristics (parametrized adequacy/intent/readiness), project health check, section hint generator, CLI integration (help, health check, section focus, JSON output, inline hints in add/draft)
```

**Step 4: Commit**

```bash
git add src/klemma/skills/CLAUDE.md src/klemma/CLAUDE.md tests/CLAUDE.md
git commit -m "docs: document klemma coach skill and CLI (#123)"
```

---

### Task 9: Final verification

**Step 1: Lint**

Run: `ruff check src/ tests/`
Expected: clean

**Step 2: Full test suite**

Run: `python -m pytest tests/ -q`
Expected: all pass (previous count + ~20 new coach tests)

**Step 3: Manual smoke test**

Run: `klemma coach` in a real project
Run: `klemma coach -s 1.1`
Run: `klemma coach --json`
Run: `klemma add <citekey> -s 1.1` — verify hint appears
