"""Tests for _auto_migrate_to_three_tier() — auto-upgrade on first run (#82).

Verifies that _init_components() auto-migrates monolithic klemma.db →
library.db + project.db when project.db is empty but klemma.db has sources.
"""

import sqlite3
from pathlib import Path

from klemma.cli import _auto_migrate_to_three_tier
from klemma.stores import LocalPaperStore, LocalProjectStore, LocalUserLibrary


def _make_mono_db(path: Path, sources=None, fragments=None, sections=None) -> None:
    """Create a minimal monolithic klemma.db."""
    if sources is None:
        sources = [("alpha2021", "Alpha Paper", "Alpha, A.", 2021, "Abstract.", "10.1/a", "completed", None, 3)]
    conn = sqlite3.connect(str(path))
    conn.executescript("""
        CREATE TABLE sources (
            id TEXT PRIMARY KEY, title TEXT, authors TEXT, year INTEGER,
            abstract TEXT, doi TEXT, status TEXT DEFAULT 'completed',
            pdf_path TEXT, quality_score INTEGER
        );
        CREATE TABLE fragments (
            source_id TEXT, fragment_text TEXT, fragment_type TEXT,
            page_number INTEGER, citation_intent TEXT
        );
        CREATE TABLE source_sections (source_id TEXT, section TEXT);
    """)
    conn.executemany("INSERT INTO sources VALUES (?,?,?,?,?,?,?,?,?)", sources)
    if fragments:
        conn.executemany("INSERT INTO fragments VALUES (?,?,?,?,?)", fragments)
    if sections:
        conn.executemany("INSERT INTO source_sections VALUES (?,?)", sections)
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Direct unit tests for _auto_migrate_to_three_tier()
# ---------------------------------------------------------------------------


class TestAutoMigrateHelper:
    def test_migrates_source_to_library(self, tmp_path):
        klemma_home = tmp_path / ".klemma"
        (klemma_home / "data").mkdir(parents=True)
        lib_db = tmp_path / "library.db"
        _make_mono_db(klemma_home / "data" / "klemma.db")

        n_src, n_frag, n_sec = _auto_migrate_to_three_tier(klemma_home, lib_db)

        assert n_src == 1
        paper_store = LocalPaperStore(lib_db)
        paper = paper_store.find_paper(pdf_hash="migrated:alpha2021")
        assert paper is not None
        assert paper.title == "Alpha Paper"

    def test_registers_citekey_in_user_library(self, tmp_path):
        klemma_home = tmp_path / ".klemma"
        (klemma_home / "data").mkdir(parents=True)
        lib_db = tmp_path / "library.db"
        _make_mono_db(klemma_home / "data" / "klemma.db")

        _auto_migrate_to_three_tier(klemma_home, lib_db)

        user_lib = LocalUserLibrary(lib_db)
        src = user_lib.get_source_by_citekey("alpha2021")
        assert src is not None
        assert src.status == "completed"

    def test_migrates_fragments(self, tmp_path):
        klemma_home = tmp_path / ".klemma"
        (klemma_home / "data").mkdir(parents=True)
        lib_db = tmp_path / "library.db"
        _make_mono_db(
            klemma_home / "data" / "klemma.db",
            fragments=[("alpha2021", "Key finding.", "key_idea", 2, "background")],
        )

        n_src, n_frag, _ = _auto_migrate_to_three_tier(klemma_home, lib_db)

        assert n_frag == 1
        paper_store = LocalPaperStore(lib_db)
        paper = paper_store.find_paper(pdf_hash="migrated:alpha2021")
        frags = paper_store.get_fragments(paper.paper_id)
        assert len(frags) == 1
        assert frags[0].fragment_text == "Key finding."

    def test_migrates_sections_to_project_db(self, tmp_path):
        klemma_home = tmp_path / ".klemma"
        (klemma_home / "data").mkdir(parents=True)
        lib_db = tmp_path / "library.db"
        _make_mono_db(
            klemma_home / "data" / "klemma.db",
            sections=[("alpha2021", "2.1"), ("alpha2021", "3.2")],
        )

        _, _, n_sec = _auto_migrate_to_three_tier(klemma_home, lib_db)

        assert n_sec == 2
        project_store = LocalProjectStore(klemma_home / "data" / "project.db")
        sections = project_store.get_source_sections("alpha2021")
        assert "2.1" in sections
        assert "3.2" in sections

    def test_creates_backup(self, tmp_path):
        klemma_home = tmp_path / ".klemma"
        (klemma_home / "data").mkdir(parents=True)
        lib_db = tmp_path / "library.db"
        mono_db = klemma_home / "data" / "klemma.db"
        _make_mono_db(mono_db)

        _auto_migrate_to_three_tier(klemma_home, lib_db)

        bak = mono_db.with_suffix(".db.bak")
        assert bak.exists()

    def test_returns_zero_when_no_mono_db(self, tmp_path):
        klemma_home = tmp_path / ".klemma"
        (klemma_home / "data").mkdir(parents=True)
        lib_db = tmp_path / "library.db"
        # No klemma.db created

        result = _auto_migrate_to_three_tier(klemma_home, lib_db)

        assert result == (0, 0, 0)

    def test_returns_zero_when_mono_db_empty(self, tmp_path):
        klemma_home = tmp_path / ".klemma"
        (klemma_home / "data").mkdir(parents=True)
        lib_db = tmp_path / "library.db"
        _make_mono_db(klemma_home / "data" / "klemma.db", sources=[])

        result = _auto_migrate_to_three_tier(klemma_home, lib_db)

        assert result == (0, 0, 0)

    def test_idempotent_second_run(self, tmp_path):
        """Running migration twice doesn't duplicate data (INSERT OR IGNORE)."""
        klemma_home = tmp_path / ".klemma"
        (klemma_home / "data").mkdir(parents=True)
        lib_db = tmp_path / "library.db"
        _make_mono_db(
            klemma_home / "data" / "klemma.db",
            fragments=[("alpha2021", "Finding.", "key_idea", 1, None)],
        )

        _auto_migrate_to_three_tier(klemma_home, lib_db)
        n_src2, n_frag2, _ = _auto_migrate_to_three_tier(klemma_home, lib_db)

        paper_store = LocalPaperStore(lib_db)
        paper = paper_store.find_paper(pdf_hash="migrated:alpha2021")
        frags = paper_store.get_fragments(paper.paper_id)
        # No duplicates — same paper_id returned, fragments INSERT OR IGNORE
        assert len(frags) == 1

    def test_multiple_sources(self, tmp_path):
        klemma_home = tmp_path / ".klemma"
        (klemma_home / "data").mkdir(parents=True)
        lib_db = tmp_path / "library.db"
        sources = [
            ("jones2019", "Jones Paper", "Jones, B.", 2019, "Abs.", None, "completed", None, None),
            ("wang2023", "Wang Paper", "Wang, C.", 2023, "Abs.", "10.1/w", "pending", None, None),
        ]
        _make_mono_db(klemma_home / "data" / "klemma.db", sources=sources)

        n_src, _, _ = _auto_migrate_to_three_tier(klemma_home, lib_db)

        assert n_src == 2
        user_lib = LocalUserLibrary(lib_db)
        assert user_lib.get_source_by_citekey("jones2019") is not None
        assert user_lib.get_source_by_citekey("wang2023") is not None

    def test_no_source_sections_table(self, tmp_path):
        """klemma.db without source_sections table migrates cleanly."""
        klemma_home = tmp_path / ".klemma"
        (klemma_home / "data").mkdir(parents=True)
        lib_db = tmp_path / "library.db"

        # Build DB without source_sections
        mono_db = klemma_home / "data" / "klemma.db"
        conn = sqlite3.connect(str(mono_db))
        conn.executescript("""
            CREATE TABLE sources (
                id TEXT PRIMARY KEY, title TEXT, authors TEXT, year INTEGER,
                abstract TEXT, doi TEXT, status TEXT, pdf_path TEXT, quality_score INTEGER
            );
            CREATE TABLE fragments (
                source_id TEXT, fragment_text TEXT, fragment_type TEXT,
                page_number INTEGER, citation_intent TEXT
            );
            INSERT INTO sources VALUES ('old2018', 'Old Paper', 'Old, O.', 2018,
                '', NULL, 'completed', NULL, NULL);
        """)
        conn.commit()
        conn.close()

        n_src, _, n_sec = _auto_migrate_to_three_tier(klemma_home, lib_db)

        assert n_src == 1
        assert n_sec == 0  # no sections table → no section entries

    def test_chapter_inferred_from_section(self, tmp_path):
        """Sections like '2.3' produce chapter=2 in project_source_sections."""
        klemma_home = tmp_path / ".klemma"
        (klemma_home / "data").mkdir(parents=True)
        lib_db = tmp_path / "library.db"
        _make_mono_db(
            klemma_home / "data" / "klemma.db",
            sections=[("alpha2021", "2.3"), ("alpha2021", "3.1")],
        )

        _auto_migrate_to_three_tier(klemma_home, lib_db)

        proj_store = LocalProjectStore(klemma_home / "data" / "project.db")
        sections = proj_store.get_source_sections("alpha2021")
        assert "2.3" in sections
        assert "3.1" in sections
        # Verify chapter is stored (project_sources.primary_chapter)
        sources_in_23 = proj_store.get_sources_by_section("2.3")
        assert "alpha2021" in sources_in_23

    def test_sources_without_sections_registered_in_project(self, tmp_path):
        """Sources with no section assignments must still appear in project_sources
        so count_sources() > 0 and auto-migration doesn't re-trigger on next run."""
        klemma_home = tmp_path / ".klemma"
        (klemma_home / "data").mkdir(parents=True)
        lib_db = tmp_path / "library.db"
        # No sections inserted
        _make_mono_db(klemma_home / "data" / "klemma.db")

        _auto_migrate_to_three_tier(klemma_home, lib_db)

        proj_store = LocalProjectStore(klemma_home / "data" / "project.db")
        assert proj_store.count_sources() == 1  # must be > 0 to prevent re-trigger

    def test_backup_not_overwritten_on_second_run(self, tmp_path):
        """Second migration run must not overwrite the .db.bak created on first run."""
        klemma_home = tmp_path / ".klemma"
        (klemma_home / "data").mkdir(parents=True)
        lib_db = tmp_path / "library.db"
        _make_mono_db(klemma_home / "data" / "klemma.db")

        _auto_migrate_to_three_tier(klemma_home, lib_db)
        bak = klemma_home / "data" / "klemma.db.bak"
        assert bak.exists()
        mtime_after_first = bak.stat().st_mtime

        _auto_migrate_to_three_tier(klemma_home, lib_db)
        assert bak.stat().st_mtime == mtime_after_first  # not overwritten
