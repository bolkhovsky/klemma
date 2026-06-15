"""Tests for the bundled CLI library sync helpers."""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "cli" / "src"))

from klemma_cli.sync import pull_library, read_local_fragments


def _init_library_db(db_path: Path, with_verbatim: bool = True) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    try:
        if with_verbatim:
            conn.execute(
                """CREATE TABLE fragments (
                    fragment_id TEXT PRIMARY KEY,
                    paper_id TEXT NOT NULL,
                    fragment_text TEXT NOT NULL,
                    fragment_type TEXT,
                    page_number INTEGER,
                    citation_intent TEXT,
                    verbatim INTEGER NOT NULL DEFAULT 0
                )"""
            )
        else:
            conn.execute(
                """CREATE TABLE fragments (
                    fragment_id TEXT PRIMARY KEY,
                    paper_id TEXT NOT NULL,
                    fragment_text TEXT NOT NULL,
                    fragment_type TEXT,
                    page_number INTEGER,
                    citation_intent TEXT
                )"""
            )
        conn.commit()
    finally:
        conn.close()


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


class _FakeClient:
    def __init__(self, payload):
        self.payload = payload

    def get(self, _path, params=None):  # pragma: no cover - params are irrelevant
        return _FakeResponse(self.payload)


def test_read_local_fragments_preserves_verbatim(tmp_path):
    db_path = tmp_path / ".klemma" / "data" / "library.db"
    _init_library_db(db_path, with_verbatim=True)

    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            """INSERT INTO fragments
               (fragment_id, paper_id, fragment_text, fragment_type, page_number, citation_intent, verbatim)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            ("frag-1", "paper-1", "Exact quote", "quote", 3, "result", 1),
        )
        conn.commit()
    finally:
        conn.close()

    fragments = read_local_fragments(tmp_path)

    assert len(fragments) == 1
    assert fragments[0].verbatim is True


def test_read_local_fragments_defaults_verbatim_false_for_legacy_db(tmp_path):
    db_path = tmp_path / ".klemma" / "data" / "library.db"
    _init_library_db(db_path, with_verbatim=False)

    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            """INSERT INTO fragments
               (fragment_id, paper_id, fragment_text, fragment_type, page_number, citation_intent)
               VALUES (?, ?, ?, ?, ?, ?)""",
            ("frag-legacy", "paper-1", "Legacy fragment", "key_idea", 1, "background"),
        )
        conn.commit()
    finally:
        conn.close()

    fragments = read_local_fragments(tmp_path)

    assert len(fragments) == 1
    assert fragments[0].verbatim is False


def test_pull_library_persists_verbatim_and_migrates_legacy_table(tmp_path):
    db_path = tmp_path / ".klemma" / "data" / "library.db"
    _init_library_db(db_path, with_verbatim=False)
    client = _FakeClient(
        {
            "sources": [
                {
                    "citekey": "smith2026",
                    "paper_id": "paper-1",
                    "title": "Test Paper",
                    "authors": "Smith",
                    "year": 2026,
                    "doi": None,
                    "abstract": "",
                    "sections": [],
                    "status": "completed",
                }
            ],
            "fragments": [
                {
                    "fragment_id": "frag-1",
                    "paper_id": "paper-1",
                    "text": "Exact quote",
                    "fragment_type": "quote",
                    "citation_intent": "result",
                    "page": 4,
                    "verbatim": True,
                }
            ],
        }
    )

    result = pull_library(client, tmp_path)

    assert result["sources"] == 1
    assert result["fragments"] == 1

    conn = sqlite3.connect(str(db_path))
    try:
        verbatim = conn.execute(
            "SELECT verbatim FROM fragments WHERE fragment_id = ?",
            ("frag-1",),
        ).fetchone()[0]
    finally:
        conn.close()

    assert verbatim == 1
