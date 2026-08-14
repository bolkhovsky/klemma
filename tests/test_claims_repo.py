"""Tests for ClaimsRepository — the manuscript claims ledger (claim-provenance PR-4)."""
from __future__ import annotations

import pytest

from klemma.skills.citation_checker import (
    ClaimAnchor,
    compute_anchor_key,
    compute_claim_hash,
)
from klemma.state import StateManager


@pytest.fixture
def state(tmp_path):
    return StateManager(tmp_path / "test.db")


def _entry(
    claim_hash: str,
    anchor_key: str = "",
    sentence: str = "Утверждение со ссылкой на источник.",
    verdict=None,
    **overrides,
):
    entry = {
        "claim_hash": claim_hash,
        "anchor_key": anchor_key,
        "sentence": sentence,
        "citekey": "smith2020",
        "ref_number": None,
        "location": "",
        "char_start": 0,
        "char_end": len(sentence),
        "anchor_kind": "numeric" if anchor_key else None,
        "anchor_raw": "42" if anchor_key else None,
        "verdict": verdict,
        "reason": f"reason for {verdict}" if verdict else None,
        "ai_used": False,
        "evidence_start": None,
        "evidence_end": None,
        "evidence_locator": None,
    }
    entry.update(overrides)
    return entry


# ---------------------------------------------------------------------------
# compute_claim_hash / compute_anchor_key
# ---------------------------------------------------------------------------


def test_hash_stable_under_whitespace_reformatting():
    a = compute_claim_hash("Точность метода составила  85 %\nна тестовой выборке.", "smith2020")
    b = compute_claim_hash("Точность метода составила 85 % на тестовой выборке.", "smith2020")
    assert a == b


def test_hash_stable_under_case_and_dashes():
    a = compute_claim_hash("Метод — стандартный подход.", "smith2020")
    b = compute_claim_hash("метод − стандартный подход.", "smith2020")
    assert a == b


def test_hash_changes_on_sentence_edit():
    a = compute_claim_hash("Точность составила 85 %.", "smith2020")
    b = compute_claim_hash("Точность составила 87 %.", "smith2020")
    assert a != b


def test_hash_distinguishes_citekey_and_ref_number():
    sentence = "Утверждение из статьи."
    assert compute_claim_hash(sentence, "a2020") != compute_claim_hash(sentence, "b2020")
    assert compute_claim_hash(sentence, "a2020", 5) != compute_claim_hash(sentence, "a2020", 12)


def test_anchor_key_format_and_stability():
    anchor = ClaimAnchor(
        kind="numeric", raw="85 %", trigger="numeric_value",
        start_offset=10, end_offset=14, anchor_id="10:14",
    )
    moved = ClaimAnchor(
        kind="numeric", raw="85  %", trigger="numeric_value",
        start_offset=99, end_offset=103, anchor_id="99:103",
    )
    key = compute_anchor_key(anchor)
    assert key.startswith("numeric:")
    # Position and inner whitespace do not change the identity
    assert key == compute_anchor_key(moved)


# ---------------------------------------------------------------------------
# record_check — UPSERT semantics
# ---------------------------------------------------------------------------


def test_record_check_upsert_no_duplicates(state):
    h = compute_claim_hash("Утверждение со ссылкой на источник.", "smith2020")
    entries = [_entry(h, "numeric:abcdef123456", verdict="ok")]

    assert state.record_claim_check("paper.md", entries) == 1
    assert state.record_claim_check("paper.md", entries) == 1

    rows = state.get_claims("paper.md")
    assert len(rows) == 1
    assert rows[0]["verdict"] == "ok"


def test_record_check_updates_verdict_and_range(state):
    h = compute_claim_hash("Утверждение со ссылкой на источник.", "smith2020")
    state.record_claim_check("paper.md", [_entry(h, "numeric:aaa", verdict="ok")])
    state.record_claim_check(
        "paper.md",
        [_entry(h, "numeric:aaa", verdict="hard_warn", char_start=100, char_end=140)],
    )

    rows = state.get_claims("paper.md")
    assert len(rows) == 1
    assert rows[0]["verdict"] == "hard_warn"
    assert rows[0]["char_start"] == 100


def test_verified_at_set_only_with_verdict(state):
    h1 = compute_claim_hash("Первое утверждение из статьи.", "smith2020")
    h2 = compute_claim_hash("Второе утверждение из статьи.", "smith2020")
    state.record_claim_check("paper.md", [
        _entry(h1, "numeric:aaa", verdict="ok"),
        _entry(h2),  # anchorless, unchecked
    ])

    by_hash = {r["claim_hash"]: r for r in state.get_claims("paper.md")}
    assert by_hash[h1]["verified_at"] is not None
    assert by_hash[h2]["verified_at"] is None
    assert by_hash[h2]["verdict"] is None


def test_judge_model_provenance_preserved_on_replay(state):
    """A replayed AI verdict (judge_model=None) must not erase the model name."""
    h = compute_claim_hash("Метод является стандартным подходом.", "smith2020")
    entry = _entry(h, "definitional:abc", verdict="ok", ai_used=True)

    state.record_claim_check("paper.md", [entry], judge_model="anthropic/claude-x")
    assert state.get_claims("paper.md")[0]["judge_model"] == "anthropic/claude-x"

    # Incremental replay: no fresh judge calls → judge_model arrives as None
    state.record_claim_check("paper.md", [entry], judge_model=None)
    assert state.get_claims("paper.md")[0]["judge_model"] == "anthropic/claude-x"

    # Fresh deterministic verdict → AI provenance honestly cleared
    state.record_claim_check("paper.md", [_entry(h, "definitional:abc", verdict="unverifiable")])
    assert state.get_claims("paper.md")[0]["judge_model"] is None


# ---------------------------------------------------------------------------
# mark_stale — staleness by design
# ---------------------------------------------------------------------------


def test_edited_sentence_goes_stale_new_row_unchecked(state):
    old_hash = compute_claim_hash("Точность составила 85 %.", "smith2020")
    new_hash = compute_claim_hash("Точность составила 87 %.", "smith2020")

    state.record_claim_check("paper.md", [_entry(old_hash, "numeric:aaa", verdict="ok")])

    # The sentence was edited: fresh parse yields only the new hash
    state.record_claim_check("paper.md", [_entry(new_hash, "numeric:aaa")])
    marked = state.mark_claims_stale("paper.md", {new_hash})
    assert marked == 1

    rows = {r["claim_hash"]: r for r in state.get_claims("paper.md")}
    assert rows[old_hash]["stale"] == 1
    assert rows[new_hash]["stale"] == 0
    assert rows[new_hash]["verdict"] is None  # unchecked until re-audited


def test_mark_stale_empty_parse_marks_all(state):
    h = compute_claim_hash("Утверждение со ссылкой на источник.", "smith2020")
    state.record_claim_check("paper.md", [_entry(h, "numeric:aaa", verdict="ok")])
    assert state.mark_claims_stale("paper.md", set()) == 1
    assert state.get_claims("paper.md")[0]["stale"] == 1


def test_reappeared_claim_revives_stale_row(state):
    h = compute_claim_hash("Утверждение со ссылкой на источник.", "smith2020")
    state.record_claim_check("paper.md", [_entry(h, "numeric:aaa", verdict="ok")])
    state.mark_claims_stale("paper.md", set())

    # The sentence came back (e.g. the edit was reverted)
    state.record_claim_check("paper.md", [_entry(h, "numeric:aaa", verdict="ok")])
    rows = state.get_claims("paper.md")
    assert len(rows) == 1
    assert rows[0]["stale"] == 0


def test_mark_stale_scoped_to_manuscript(state):
    h = compute_claim_hash("Утверждение со ссылкой на источник.", "smith2020")
    state.record_claim_check("a.md", [_entry(h, "numeric:aaa", verdict="ok")])
    state.record_claim_check("b.md", [_entry(h, "numeric:aaa", verdict="ok")])

    assert state.mark_claims_stale("a.md", set()) == 1
    assert state.get_claims("b.md")[0]["stale"] == 0


def test_get_claims_include_stale_filter(state):
    h1 = compute_claim_hash("Первое утверждение из статьи.", "smith2020")
    h2 = compute_claim_hash("Второе утверждение из статьи.", "smith2020")
    state.record_claim_check("paper.md", [
        _entry(h1, "numeric:aaa", verdict="ok"),
        _entry(h2, "numeric:bbb", verdict="ok"),
    ])
    state.mark_claims_stale("paper.md", {h1})

    assert len(state.get_claims("paper.md")) == 2
    live = state.get_claims("paper.md", include_stale=False)
    assert [r["claim_hash"] for r in live] == [h1]


# ---------------------------------------------------------------------------
# get_status_summary — gate counters
# ---------------------------------------------------------------------------


def test_status_summary_counts(state):
    sentences = [f"Утверждение номер {i} из статьи." for i in range(6)]
    hashes = [compute_claim_hash(s, "smith2020") for s in sentences]
    state.record_claim_check("paper.md", [
        _entry(hashes[0], "numeric:a", verdict="ok"),
        _entry(hashes[1], "numeric:b", verdict="soft_warn"),
        _entry(hashes[2], "numeric:c", verdict="hard_warn"),
        _entry(hashes[3], "numeric:d", verdict="unverifiable"),
        _entry(hashes[4]),  # unchecked (anchorless)
        _entry(hashes[5], "numeric:e", verdict="ok"),
    ])
    state.mark_claims_stale("paper.md", set(hashes[:5]))  # hashes[5] goes stale

    summary = state.get_claims_status_summary("paper.md")
    assert len(summary) == 1
    row = summary[0]
    assert row["total"] == 6
    assert row["ok"] == 1  # the stale ok row counts only under stale
    assert row["soft_warn"] == 1
    assert row["hard_warn"] == 1
    assert row["unverifiable"] == 1
    assert row["unchecked"] == 1
    assert row["stale"] == 1
    assert row["last_verified"] is not None


def test_status_summary_all_manuscripts(state):
    h = compute_claim_hash("Утверждение со ссылкой на источник.", "smith2020")
    state.record_claim_check("b.md", [_entry(h, "numeric:aaa", verdict="ok")])
    state.record_claim_check("a.md", [_entry(h, "numeric:aaa")])

    summary = state.get_claims_status_summary()
    assert [r["manuscript_path"] for r in summary] == ["a.md", "b.md"]
    assert summary[0]["unchecked"] == 1
    assert summary[1]["ok"] == 1
