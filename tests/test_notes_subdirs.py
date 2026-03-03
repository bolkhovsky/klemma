"""Tests for notes/ subdirectory layout (#34).

Verifies researcher, librarian, and agent scanner respect the new
project_root/notes/{research,library,agents}/ layout with legacy fallback.
"""

from pathlib import Path

from klemma.skills.agent import _scan_project_reports, update_agents_index
from klemma.skills.researcher import _load_previous_research, _save_report

# ---------------------------------------------------------------------------
# researcher — save
# ---------------------------------------------------------------------------


def test_researcher_saves_to_notes_research(tmp_path: Path):
    """_save_report writes to notes/research/, not project root."""
    path = _save_report("1.2", "# Report", tmp_path)
    assert path == tmp_path / "notes" / "research" / "Research_1.2.md"
    assert path.exists()
    assert path.read_text(encoding="utf-8") == "# Report"
    # Must NOT exist at the old flat location
    assert not (tmp_path / "Research_1.2.md").exists()


# ---------------------------------------------------------------------------
# researcher — load (new path preferred over legacy)
# ---------------------------------------------------------------------------


def test_researcher_load_new_path_preferred(tmp_path: Path):
    """When file exists in both locations, notes/research/ wins."""
    # Legacy location
    (tmp_path / "Research_1.2.md").write_text("old", encoding="utf-8")
    # New location
    notes_dir = tmp_path / "notes" / "research"
    notes_dir.mkdir(parents=True)
    (notes_dir / "Research_1.2.md").write_text(
        "## ✏️ Что нового\n\nnew notes\n\n## 📋 История изменений\n",
        encoding="utf-8",
    )

    result = _load_previous_research("1.2", 1, None, tmp_path)
    assert result is not None
    assert "new notes" in result["user_notes"]


def test_researcher_load_fallback_legacy(tmp_path: Path):
    """When file exists only at root, legacy path is still found."""
    (tmp_path / "Research_1.2.md").write_text(
        "## ✏️ Что нового\n\nlegacy notes\n\n## 📋 История изменений\n",
        encoding="utf-8",
    )
    result = _load_previous_research("1.2", 1, None, tmp_path)
    assert result is not None
    assert "legacy notes" in result["user_notes"]


# ---------------------------------------------------------------------------
# librarian — save
# ---------------------------------------------------------------------------


def test_librarian_saves_to_notes_library(tmp_path: Path):
    """_save_report_to_vault writes to notes/library/ when project_root set."""
    from unittest.mock import MagicMock

    from klemma.literature.models import LibraryReport
    from klemma.skills.librarian import _save_report_to_vault

    report = LibraryReport(
        mode="status",
        report_text="body",
    )
    vault = MagicMock()

    result = _save_report_to_vault(
        report, vault, mode="status", section=None,
        project_name="test", project_root=tmp_path,
    )
    assert result is not None

    lib_dir = tmp_path / "notes" / "library"
    assert lib_dir.is_dir()
    files = list(lib_dir.glob("Library_*.md"))
    assert len(files) == 1
    # Must NOT exist at the old flat location
    assert list(tmp_path.glob("Library_*.md")) == []


# ---------------------------------------------------------------------------
# agent scanner — notes/ subdirectories
# ---------------------------------------------------------------------------


def test_scan_finds_notes_subdirs(tmp_path: Path):
    """Reports in notes/{research,library,agents}/ appear in report_index."""
    # Create notes subdirs with reports
    for subdir, name in [
        ("research", "Research_1.2.md"),
        ("library", "Library_test_status_2026-03-01.md"),
        ("agents", "Agent_2026-03-01.md"),
    ]:
        d = tmp_path / "notes" / subdir
        d.mkdir(parents=True)
        (d / name).write_text("content", encoding="utf-8")

    result = _scan_project_reports(tmp_path)
    names = [r["name"] for r in result["report_index"]]
    assert "notes/research/Research_1.2.md" in names
    assert "notes/library/Library_test_status_2026-03-01.md" in names
    assert "notes/agents/Agent_2026-03-01.md" in names


def test_scan_backward_compat(tmp_path: Path):
    """Legacy flat reports at project root are still found."""
    (tmp_path / "Research_1.1.md").write_text("old", encoding="utf-8")
    (tmp_path / "Library_test_status_2026-01-01.md").write_text("old", encoding="utf-8")

    result = _scan_project_reports(tmp_path)
    names = [r["name"] for r in result["report_index"]]
    assert "Research_1.1.md" in names
    assert "Library_test_status_2026-01-01.md" in names


# ---------------------------------------------------------------------------
# AGENTS.md auto-index
# ---------------------------------------------------------------------------


def test_agents_index_generated(tmp_path: Path):
    """update_agents_index creates notes/AGENTS.md from agent files."""
    agents_dir = tmp_path / "notes" / "agents"
    agents_dir.mkdir(parents=True)

    # File with frontmatter
    (agents_dir / "Agent_test_2026-03-01_literature.md").write_text(
        "---\ntype: agent\ndate: 2026-03-01\nquery: What are ice methods?\n---\n\n# Body\n",
        encoding="utf-8",
    )
    # File without frontmatter (date from filename)
    (agents_dir / "Agent_test_2026-03-02_search.md").write_text(
        "# No frontmatter\n",
        encoding="utf-8",
    )

    result = update_agents_index(tmp_path)
    assert result == tmp_path / "notes" / "AGENTS.md"
    assert result.exists()

    content = result.read_text(encoding="utf-8")
    assert "Agent Reports Index" in content
    assert "Agent_test_2026-03-01_literature.md" in content
    assert "Agent_test_2026-03-02_search.md" in content
    assert "2026-03-01" in content
    assert "What are ice methods?" in content


def test_agents_index_no_dir(tmp_path: Path):
    """update_agents_index returns None when notes/agents/ doesn't exist."""
    result = update_agents_index(tmp_path)
    assert result is None


def test_agents_index_empty_dir(tmp_path: Path):
    """update_agents_index returns None when no Agent_*.md files exist."""
    agents_dir = tmp_path / "notes" / "agents"
    agents_dir.mkdir(parents=True)
    (agents_dir / "README.md").write_text("not an agent file", encoding="utf-8")

    result = update_agents_index(tmp_path)
    assert result is None


def test_agents_index_sorted_reverse(tmp_path: Path):
    """AGENTS.md lists newest files first."""
    agents_dir = tmp_path / "notes" / "agents"
    agents_dir.mkdir(parents=True)

    (agents_dir / "Agent_2026-01-01_old.md").write_text(
        "---\ndate: 2026-01-01\nquery: old query\n---\n",
        encoding="utf-8",
    )
    (agents_dir / "Agent_2026-03-01_new.md").write_text(
        "---\ndate: 2026-03-01\nquery: new query\n---\n",
        encoding="utf-8",
    )

    update_agents_index(tmp_path)
    content = (tmp_path / "notes" / "AGENTS.md").read_text(encoding="utf-8")
    lines = content.splitlines()
    table_rows = [row for row in lines if row.startswith("| 20")]
    # Newest first (reverse alphabetical of Agent_ filenames)
    assert "2026-03-01" in table_rows[0]
    assert "2026-01-01" in table_rows[1]
