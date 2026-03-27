"""Tests for library sync — local DB read/write."""

from __future__ import annotations

import sqlite3
from unittest.mock import MagicMock

import pytest

from klemma_cli.sync import (
    pull_drafts,
    read_local_fragments,
    read_local_sources,
)


@pytest.fixture
def project_with_db(tmp_path):
    """Create a project with a populated library.db."""
    klemma_dir = tmp_path / ".klemma" / "data"
    klemma_dir.mkdir(parents=True)

    db_path = klemma_dir / "library.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript("""
        CREATE TABLE papers (
            paper_id TEXT PRIMARY KEY, pdf_hash TEXT UNIQUE,
            doi TEXT, s2_paper_id TEXT, title TEXT NOT NULL DEFAULT '',
            authors TEXT, year INTEGER, abstract TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE user_sources (
            citekey TEXT PRIMARY KEY, paper_id TEXT NOT NULL,
            status TEXT DEFAULT 'pending', pdf_path TEXT, note_path TEXT,
            quality_score INTEGER, added_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now')),
            project_id TEXT, user_id TEXT
        );
        CREATE TABLE user_source_sections (
            citekey TEXT NOT NULL, section TEXT NOT NULL,
            PRIMARY KEY (citekey, section)
        );
        CREATE TABLE fragments (
            fragment_id TEXT PRIMARY KEY, paper_id TEXT NOT NULL,
            extraction_id TEXT, fragment_text TEXT NOT NULL,
            fragment_type TEXT, page_number INTEGER, citation_intent TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );

        INSERT INTO papers (paper_id, title, authors, year)
        VALUES ('paper-1', 'Test Paper', 'Smith', 2022);

        INSERT INTO user_sources (citekey, paper_id, status)
        VALUES ('smith2022', 'paper-1', 'completed');

        INSERT INTO user_source_sections (citekey, section)
        VALUES ('smith2022', '1.1'), ('smith2022', '2.3');

        INSERT INTO fragments (fragment_id, paper_id, fragment_text, fragment_type, page_number)
        VALUES ('frag-1', 'paper-1', 'Important finding about X.', 'key_idea', 5),
               ('frag-2', 'paper-1', 'Method described in Y.', 'methodology', 12);
    """)
    conn.commit()
    conn.close()
    return tmp_path


def _make_client(file_list: list[dict], contents: dict[str, str]) -> MagicMock:
    """Build a mock KlemmaClient for pull_drafts tests."""
    def _get(path, **_kwargs):
        resp = MagicMock()
        resp.status_code = 200
        if path.endswith("/drafts"):
            resp.json.return_value = {"files": file_list}
        else:
            filename = path.rsplit("/", 1)[-1]
            resp.json.return_value = {
                "name": filename,
                "content": contents.get(filename, ""),
                "word_count": len(contents.get(filename, "").split()),
            }
        return resp

    client = MagicMock()
    client.get.side_effect = _get
    return client


class TestPullDrafts:
    def test_downloads_new_files(self, tmp_path):
        contents = {"chapter_1.md": "## 1. Intro\n\nText here."}
        client = _make_client(
            [{"name": "chapter_1.md", "word_count": 3}],
            contents,
        )
        result = pull_drafts(client, tmp_path, "proj-123")
        assert result["files"] == 1
        assert (tmp_path / "draft" / "chapter_1.md").read_text() == contents["chapter_1.md"]

    def test_skips_unchanged_files(self, tmp_path):
        content = "## 1. Intro\n\nText here."
        (tmp_path / "draft").mkdir()
        (tmp_path / "draft" / "chapter_1.md").write_text(content, encoding="utf-8")
        client = _make_client(
            [{"name": "chapter_1.md", "word_count": 3}],
            {"chapter_1.md": content},
        )
        result = pull_drafts(client, tmp_path, "proj-123")
        assert result["files"] == 0  # already up to date

    def test_updates_changed_file(self, tmp_path):
        (tmp_path / "draft").mkdir()
        (tmp_path / "draft" / "chapter_1.md").write_text("old content", encoding="utf-8")
        new_content = "## 1. New Content\n\nUpdated."
        client = _make_client(
            [{"name": "chapter_1.md", "word_count": 3}],
            {"chapter_1.md": new_content},
        )
        result = pull_drafts(client, tmp_path, "proj-123")
        assert result["files"] == 1
        assert (tmp_path / "draft" / "chapter_1.md").read_text() == new_content

    def test_skips_unsafe_filenames(self, tmp_path):
        client = _make_client(
            [{"name": "../evil.md", "word_count": 0},
             {"name": "safe.md", "word_count": 2}],
            {"safe.md": "## Safe\n\nOk."},
        )
        result = pull_drafts(client, tmp_path, "proj-123")
        assert result["files"] == 1
        assert not (tmp_path / "evil.md").exists()  # path traversal blocked

    def test_empty_list_returns_zero(self, tmp_path):
        client = _make_client([], {})
        result = pull_drafts(client, tmp_path, "proj-123")
        assert result == {"files": 0, "words": 0}


class TestReadLocalSources:
    def test_reads_sources(self, project_with_db):
        sources = read_local_sources(project_with_db)
        assert len(sources) == 1
        assert sources[0].citekey == "smith2022"
        assert sources[0].title == "Test Paper"
        assert sources[0].year == 2022
        assert set(sources[0].sections) == {"1.1", "2.3"}

    def test_no_project_db(self, tmp_path, monkeypatch):
        # Prevent fallback to ~/.klemma/
        monkeypatch.setenv("HOME", str(tmp_path))
        sources = read_local_sources(tmp_path)
        assert sources == []


class TestReadLocalFragments:
    def test_reads_fragments(self, project_with_db):
        fragments = read_local_fragments(project_with_db)
        assert len(fragments) == 2
        assert fragments[0].fragment_id == "frag-1"
        assert fragments[0].text == "Important finding about X."
        assert fragments[0].page == 5

    def test_no_project_db(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        fragments = read_local_fragments(tmp_path)
        assert fragments == []
