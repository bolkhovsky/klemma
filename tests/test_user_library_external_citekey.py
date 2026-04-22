"""Tests for external_citekey support (BBT parity plan, part 2)."""

from __future__ import annotations

import sqlite3

import pytest

from klemma.stores.user_library import LocalUserLibrary


@pytest.fixture
def lib(tmp_path):
    return LocalUserLibrary(tmp_path / "library.db")


# ---------------------------------------------------------------------------
# Schema v6 migration
# ---------------------------------------------------------------------------


def test_schema_version_is_6(lib, tmp_path):
    conn = sqlite3.connect(str(tmp_path / "library.db"))
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    conn.close()
    assert version == 6


def test_external_citekey_column_exists(lib, tmp_path):
    conn = sqlite3.connect(str(tmp_path / "library.db"))
    cols = {r[1] for r in conn.execute("PRAGMA table_info(user_sources)").fetchall()}
    conn.close()
    assert "external_citekey" in cols


def test_v6_migration_idempotent(tmp_path):
    db = tmp_path / "library.db"
    LocalUserLibrary(db)
    # Second init must not raise or duplicate the column
    LocalUserLibrary(db)
    conn = sqlite3.connect(str(db))
    count = sum(
        1 for r in conn.execute("PRAGMA table_info(user_sources)").fetchall()
        if r[1] == "external_citekey"
    )
    conn.close()
    assert count == 1


# ---------------------------------------------------------------------------
# set_external_citekey + roundtrip through get_source_by_citekey
# ---------------------------------------------------------------------------


def test_set_and_read_external_citekey(lib):
    lib.add_source("paper-1", "воронина2023_ugly", user_id="user-a")
    assert lib.set_external_citekey(
        "воронина2023_ugly", "voronina2023", user_id="user-a"
    ) is True

    src = lib.get_source_by_citekey("воронина2023_ugly", user_id="user-a")
    assert src is not None
    assert src.citekey == "воронина2023_ugly"
    assert src.external_citekey == "voronina2023"


def test_set_external_citekey_missing_row_returns_false(lib):
    assert lib.set_external_citekey("nonexistent", "foo", user_id="user-a") is False


def test_clear_external_citekey(lib):
    lib.add_source("paper-1", "ck1", user_id="u")
    lib.set_external_citekey("ck1", "external", user_id="u")
    lib.set_external_citekey("ck1", None, user_id="u")
    src = lib.get_source_by_citekey("ck1", user_id="u")
    assert src.external_citekey is None


def test_external_citekey_scoped_to_user(lib):
    """user_b's import must not leak into user_a's view."""
    lib.add_source("paper-1", "ck", user_id="user-a")
    lib.add_source("paper-1", "ck", user_id="user-b")
    lib.set_external_citekey("ck", "a_external", user_id="user-a")

    a = lib.get_source_by_citekey("ck", user_id="user-a")
    b = lib.get_source_by_citekey("ck", user_id="user-b")
    assert a.external_citekey == "a_external"
    assert b.external_citekey is None


# ---------------------------------------------------------------------------
# get_source_by_any_key (dual-key resolver)
# ---------------------------------------------------------------------------


def test_get_source_by_any_key_matches_citekey(lib):
    lib.add_source("paper-1", "ugly_key", user_id="u")
    src = lib.get_source_by_any_key("ugly_key", user_id="u")
    assert src is not None
    assert src.citekey == "ugly_key"


def test_get_source_by_any_key_matches_external(lib):
    lib.add_source("paper-1", "ugly_key", user_id="u")
    lib.set_external_citekey("ugly_key", "voronina2023", user_id="u")

    src = lib.get_source_by_any_key("voronina2023", user_id="u")
    assert src is not None
    assert src.citekey == "ugly_key"  # internal returned, not external
    assert src.external_citekey == "voronina2023"


def test_get_source_by_any_key_not_found(lib):
    assert lib.get_source_by_any_key("nope", user_id="u") is None


def test_citekey_preferred_over_external(lib):
    """If a submitted key matches both citekey (of one source) and
    external_citekey (of another), citekey wins — citekeys are the stable
    reference anchor.
    """
    lib.add_source("paper-1", "conflict", user_id="u")
    lib.add_source("paper-2", "other", user_id="u")
    lib.set_external_citekey("other", "conflict", user_id="u")

    src = lib.get_source_by_any_key("conflict", user_id="u")
    assert src.paper_id == "paper-1"  # citekey match wins


# ---------------------------------------------------------------------------
# get_display_citekeys (batch mapping)
# ---------------------------------------------------------------------------


def test_get_display_citekeys_mixed(lib):
    lib.add_source("p1", "voronina_ugly", user_id="u")
    lib.add_source("p2", "smith2020", user_id="u")
    lib.set_external_citekey("voronina_ugly", "voronina2023", user_id="u")

    display = lib.get_display_citekeys(
        ["voronina_ugly", "smith2020", "missing"], user_id="u"
    )
    # external overrides, otherwise citekey itself, missing absent
    assert display == {"voronina_ugly": "voronina2023", "smith2020": "smith2020"}


def test_get_display_citekeys_empty_input(lib):
    assert lib.get_display_citekeys([], user_id="u") == {}


def test_get_display_citekeys_user_scoped(lib):
    lib.add_source("p1", "ck", user_id="user-a")
    lib.add_source("p1", "ck", user_id="user-b")
    lib.set_external_citekey("ck", "a_external", user_id="user-a")

    assert lib.get_display_citekeys(["ck"], user_id="user-a") == {"ck": "a_external"}
    assert lib.get_display_citekeys(["ck"], user_id="user-b") == {"ck": "ck"}
