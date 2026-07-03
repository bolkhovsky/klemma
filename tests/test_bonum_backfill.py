"""Tests for the pure helpers in scripts/bonum_backfill.py.

Regression coverage for a source_id collision found before the first
production backfill run: two different same-day meetings from a long-named
site folder collapsed into the same source_id (silent overwrite, not mere
duplication) because build_meeting_id's no-dash date wasn't recognized by
meetings.build_records' own date-dedup check, pushing the differentiating
time suffix past the (then 48-char) source_id length cap.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from bonum_backfill import (  # noqa: E402
    build_meeting_id,
    parse_protocol_filename,
    site_type_and_name,
    slugify,
)

from klemma.meetings import ParsedMeeting, build_records  # noqa: E402

LONG_SITE = "ОМС Заготовительное производство"


def test_parse_protocol_filename_matches_real_pattern():
    assert parse_protocol_filename(
        "Протокол_ОМС_Аксайский_филиал_11-02-2026_07-30.docx"
    ) == ("2026-02-11", "07:30")


def test_parse_protocol_filename_rejects_non_matching():
    assert parse_protocol_filename("2026-01-21_Протокол_Ежедневный_ОМС_ТРАСТ.md") is None
    assert parse_protocol_filename("Протокол_Ежедневный_ОМС_ТРАСТ_2026-01-21.pdf") is None


def test_site_type_and_name():
    assert site_type_and_name("ОМС Аксайский филиал") == ("ОМС", "Аксайский филиал")
    assert site_type_and_name("Скрам проекты") == ("Скрам", "Скрам проекты")


def test_build_meeting_id_keeps_dashed_date():
    mid = build_meeting_id(LONG_SITE, "2026-02-11", "07:30")
    # meetings.build_records dedups the date by substring match — it must
    # find it dashed, exactly as it appears in the payload's "date" field.
    assert "2026-02-11" in mid


def test_same_day_different_time_meetings_do_not_collide():
    """The actual bug: two real protocols from the same long-named site on
    the same day, different times, must not resolve to the same source_id."""
    ids = set()
    for time_str in ("07:30", "15:45"):
        mid = build_meeting_id(LONG_SITE, "2026-02-11", time_str)
        pm = ParsedMeeting(meta={"date": "2026-02-11", "site": LONG_SITE, "type": "ОМС"})
        source_id, _, _ = build_records(pm, mid)
        ids.add(source_id)
    assert len(ids) == 2, f"source_id collision: {ids}"


def test_different_days_do_not_collide():
    ids = set()
    for date_str in ("2026-02-11", "2026-02-12"):
        mid = build_meeting_id(LONG_SITE, date_str, "07:30")
        pm = ParsedMeeting(meta={"date": date_str, "site": LONG_SITE, "type": "ОМС"})
        source_id, _, _ = build_records(pm, mid)
        ids.add(source_id)
    assert len(ids) == 2, f"source_id collision: {ids}"


def test_slugify_length_cap():
    assert len(slugify("а" * 100)) == 40
