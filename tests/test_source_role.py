"""Tests for source_role classification (#85)."""

import sqlite3

from klemma.source_role import SourceRole, format_gost_phrase
from klemma.state import StateManager


def _make_db(tmp_path):
    db_path = str(tmp_path / "test.db")
    sm = StateManager(db_path)
    return sm


def test_source_role_enum_values():
    """SourceRole has 8 values."""
    assert len(SourceRole) == 8
    assert SourceRole.EXTERNAL.value == "external"
    assert SourceRole.AUTHOR_VAK.value == "author_vak"


def test_source_role_author_roles():
    """author_roles() excludes external."""
    roles = SourceRole.author_roles()
    assert SourceRole.EXTERNAL not in roles
    assert len(roles) == 7


def test_migration_v8_adds_source_role(tmp_path):
    """DB migration v8 adds source_role column with default 'external'."""
    _make_db(tmp_path)
    db_path = str(tmp_path / "test.db")
    conn = sqlite3.connect(db_path)
    cols = {row[1] for row in conn.execute("PRAGMA table_info(sources)")}
    assert "source_role" in cols
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    assert version == 15
    conn.close()


def test_set_source_role(tmp_path):
    """set_source_role updates the column."""
    sm = _make_db(tmp_path)
    sm.register_sources(["@testkey"])
    sm.set_source_role("@testkey", "author_vak")
    src = sm.get_source("@testkey")
    assert src["source_role"] == "author_vak"


def test_set_source_role_default_external(tmp_path):
    """New sources default to 'external'."""
    sm = _make_db(tmp_path)
    sm.register_sources(["@newkey"])
    src = sm.get_source("@newkey")
    assert src["source_role"] == "external"


def test_get_author_publication_counts(tmp_path):
    """get_author_publication_counts groups by role, excludes external."""
    sm = _make_db(tmp_path)
    sm.register_sources(["@a", "@b", "@c", "@d"])
    sm.set_source_role("@a", "author_vak")
    sm.set_source_role("@b", "author_vak")
    sm.set_source_role("@c", "author_conf")
    # @d stays external — should not appear
    counts = sm.get_author_publication_counts()
    assert counts == {"author_vak": 2, "author_conf": 1}


def test_format_gost_phrase_full():
    """ГОСТ phrase includes all role counts."""
    counts = {"author_vak": 3, "author_scopus": 2, "author_conf": 1}
    phrase = format_gost_phrase(counts)
    assert "6 печатных изданиях" in phrase
    assert "3 из которых в журналах ВАК" in phrase
    assert "2 — в Scopus" in phrase
    assert "1 — в тезисах докладов" in phrase
    assert phrase.endswith(".")


def test_format_gost_phrase_empty():
    """Empty counts → empty string."""
    assert format_gost_phrase({}) == ""


def test_format_gost_phrase_single_role():
    """Single role still formats correctly."""
    phrase = format_gost_phrase({"author_vak": 5})
    assert "5 печатных изданиях" in phrase
    assert "5 из которых в журналах ВАК" in phrase
