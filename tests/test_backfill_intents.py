"""Tests for citation intent backfill — task + admin endpoint.

Covers:
- get_papers_for_user_backfill: pagination and cursor semantics
- update_citation_intents: only updates NULL/background, skips method etc.
- backfill_citation_intents task: seed → AI mock → verify updates, cursor, skipped
- Admin endpoint: non-admin → 403
"""

from __future__ import annotations

import hashlib
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from klemma.api.app import create_app
from klemma.api.auth.deps import set_user_store
from klemma.api.deps import set_file_store, set_paper_store, set_project_store, set_user_library
from klemma.api.rate_limit import reset_rate_limiter
from klemma.stores.file_store import LocalFileStore
from klemma.stores.paper_store import LocalPaperStore
from klemma.stores.project_store import LocalProjectStore
from klemma.stores.user_library import LocalUserLibrary
from klemma.stores.user_store import LocalUserStore

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def db(tmp_path):
    """Returns (paper_store, user_library, user_store) sharing library.db."""
    library_db = tmp_path / "library.db"
    return (
        LocalPaperStore(library_db),
        LocalUserLibrary(library_db),
        LocalUserStore(tmp_path / "users.db"),
    )


@pytest.fixture
def stores(tmp_path):
    library_db = tmp_path / "library.db"
    user_store = LocalUserStore(tmp_path / "users.db")
    paper_store = LocalPaperStore(library_db)
    user_library = LocalUserLibrary(library_db)
    project_store = LocalProjectStore(tmp_path / "project.db")
    file_store = LocalFileStore(tmp_path / "files")
    return user_store, paper_store, user_library, project_store, file_store


@pytest.fixture
def client(stores):
    user_store, paper_store, user_library, project_store, file_store = stores
    app = create_app()
    set_user_store(user_store)
    set_paper_store(paper_store)
    set_user_library(user_library)
    set_project_store(project_store)
    set_file_store(file_store)
    reset_rate_limiter()
    return TestClient(app)


def _register(client, email="admin@test.com", password="pass1234"):
    resp = client.post("/auth/register", json={"email": email, "password": password})
    return resp.json()["access_token"]


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def _title_hash(title: str) -> str:
    return hashlib.md5(title.lower().encode()).hexdigest()


def _get_intent(paper_store, paper_id, ref_title):
    with paper_store._conn() as conn:
        row = conn.execute(
            "SELECT citation_intent FROM citation_graph WHERE citing_paper_id=? AND cited_title_hash=?",
            (paper_id, _title_hash(ref_title)),
        ).fetchone()
    return row["citation_intent"] if row else None


# ---------------------------------------------------------------------------
# update_citation_intents — unit tests
# ---------------------------------------------------------------------------


def test_update_intents_updates_null(db):
    """update_citation_intents updates rows where intent IS NULL."""
    paper_store, _, _ = db
    pid = paper_store.register_paper(title="Test Paper", pdf_hash="hash1")
    paper_store.save_citation_links(pid, [
        {"title": "Gap Paper", "authors": "A", "year": 2020}  # intent=NULL
    ])
    updated = paper_store.update_citation_intents(pid, [
        {"title": "Gap Paper", "citation_intent": "method"}
    ])
    assert updated == 1
    assert _get_intent(paper_store, pid, "Gap Paper") == "method"


def test_update_intents_does_not_overwrite_background(db):
    """update_citation_intents does NOT overwrite existing 'background' intent.

    'background' is now a valid intent (Teufel 2006) — the backfill must not
    replace legitimately-classified background citations on re-runs.
    Only NULL entries are eligible for backfill.
    """
    paper_store, _, _ = db
    pid = paper_store.register_paper(title="Test Paper", pdf_hash="hash2")
    paper_store.save_citation_links(pid, [
        {"title": "Legacy Gap", "authors": "B", "year": 2019, "citation_intent": "background"}
    ])
    updated = paper_store.update_citation_intents(pid, [
        {"title": "Legacy Gap", "citation_intent": "extends"}
    ])
    assert updated == 0  # NOT overwritten — background is a valid intent
    assert _get_intent(paper_store, pid, "Legacy Gap") == "background"


def test_update_intents_does_not_overwrite_method(db):
    """update_citation_intents skips rows already set to a non-background intent."""
    paper_store, _, _ = db
    pid = paper_store.register_paper(title="Test Paper", pdf_hash="hash3")
    paper_store.save_citation_links(pid, [
        {"title": "Method Gap", "authors": "C", "year": 2021, "citation_intent": "method"}
    ])
    updated = paper_store.update_citation_intents(pid, [
        {"title": "Method Gap", "citation_intent": "background"}
    ])
    assert updated == 0  # NOT overwritten
    assert _get_intent(paper_store, pid, "Method Gap") == "method"


def test_update_intents_skips_invalid(db):
    """update_citation_intents skips refs with invalid intent (returns 0)."""
    paper_store, _, _ = db
    pid = paper_store.register_paper(title="Test Paper", pdf_hash="hash4")
    paper_store.save_citation_links(pid, [{"title": "Gap", "authors": "D", "year": 2020}])
    updated = paper_store.update_citation_intents(pid, [
        {"title": "Gap", "citation_intent": "garbage_intent"}
    ])
    assert updated == 0


def test_update_intents_idempotent(db):
    """Calling update_citation_intents twice with same data doesn't double-update."""
    paper_store, _, _ = db
    pid = paper_store.register_paper(title="Test Paper", pdf_hash="hash5")
    paper_store.save_citation_links(pid, [{"title": "Idem Gap", "authors": "E", "year": 2020}])
    paper_store.update_citation_intents(pid, [{"title": "Idem Gap", "citation_intent": "extends"}])
    # Second call — already set to extends → not overwritten again (condition fails)
    updated2 = paper_store.update_citation_intents(pid, [{"title": "Idem Gap", "citation_intent": "contrasts"}])
    assert updated2 == 0
    assert _get_intent(paper_store, pid, "Idem Gap") == "extends"


# ---------------------------------------------------------------------------
# get_papers_for_user_backfill — cursor pagination
# ---------------------------------------------------------------------------


def test_backfill_pagination_cursor(db):
    """Cursor advances the batch window; remaining count is cursor-independent (total).

    remaining always reflects ALL papers still needing backfill, not just those
    after the cursor — so failed/stuck papers behind the cursor still appear.
    """
    paper_store, user_library, user_store = db
    user = user_store.create_user(email="cursor@test.com", password_hash="hashed")
    user_id = user.user_id

    pids = []
    for i in range(4):
        pid = paper_store.register_paper(title=f"Paper {i}", pdf_hash=f"chash{i}")
        citekey = f"cursor_paper_{i}"
        user_library.add_source(pid, citekey, status="completed", user_id=user_id)
        paper_store.save_citation_links(pid, [{"title": f"Gap {i}", "authors": "X", "year": 2020}])
        paper_store.update_paper_raw_text(pid, f"Body text for paper {i}.")
        pids.append(pid)

    # First batch of 2
    batch1, remaining1 = paper_store.get_papers_for_user_backfill(user_id, batch_size=2, cursor=None)
    assert len(batch1) == 2
    assert remaining1 == 4  # total — cursor-independent

    cursor = batch1[-1]["paper_id"]

    # Second batch starting after cursor
    batch2, remaining2 = paper_store.get_papers_for_user_backfill(user_id, batch_size=2, cursor=cursor)
    assert len(batch2) == 2
    # remaining2 is still 4 — cursor doesn't subtract from the total count
    assert remaining2 == 4
    # IDs in batch2 should be distinct from batch1
    ids1 = {p["paper_id"] for p in batch1}
    ids2 = {p["paper_id"] for p in batch2}
    assert ids1.isdisjoint(ids2)


def test_backfill_skip_already_updated(db):
    """Papers with all-method intents are NOT returned by get_papers_for_user_backfill."""
    paper_store, user_library, user_store = db
    user = user_store.create_user(email="skip@test.com", password_hash="hashed")
    user_id = user.user_id

    # One paper with method intent (should not be backfilled)
    pid_method = paper_store.register_paper(title="MethodOnly", pdf_hash="mhash1")
    user_library.add_source(pid_method, "method_paper", status="completed", user_id=user_id)
    paper_store.save_citation_links(pid_method, [
        {"title": "Method Gap", "authors": "M", "year": 2020, "citation_intent": "method"}
    ])

    # One paper with NULL intent (should be backfilled) — must have raw_text
    pid_null = paper_store.register_paper(title="NullIntents", pdf_hash="nhash1")
    user_library.add_source(pid_null, "null_paper", status="completed", user_id=user_id)
    paper_store.save_citation_links(pid_null, [
        {"title": "Null Gap", "authors": "N", "year": 2020}
    ])
    paper_store.update_paper_raw_text(pid_null, "Body text referencing Null Gap.")

    papers, _ = paper_store.get_papers_for_user_backfill(user_id, batch_size=10, cursor=None)
    paper_ids = {p["paper_id"] for p in papers}
    assert pid_null in paper_ids
    assert pid_method not in paper_ids


def test_backfill_excludes_papers_without_raw_text(db, tmp_path):
    """Papers without raw_text are excluded from the backfill query entirely.

    The query now has AND p.raw_text IS NOT NULL — unprocessable papers never
    enter the batch, so processed=0 and the loop terminates immediately.
    """
    paper_store, user_library, user_store = db
    user = user_store.create_user(email="skip2@test.com", password_hash="hashed")
    user_id = user.user_id

    pid = paper_store.register_paper(title="NoText Paper", pdf_hash="nohash")
    user_library.add_source(pid, "notext_paper", status="completed", user_id=user_id)
    paper_store.save_citation_links(pid, [{"title": "Gap", "authors": "X", "year": 2020}])
    # raw_text intentionally NOT set

    from unittest.mock import patch

    mock_ai = MagicMock()
    mock_ai_config = MagicMock()
    mock_ai_config.model = "test-model"

    with patch("klemma.api.tasks._create_ai_provider", return_value=(mock_ai, mock_ai_config)):
        from klemma.api.tasks import backfill_citation_intents

        result = backfill_citation_intents(
            user_id=user_id,
            data_dir=str(tmp_path),
            batch_size=10,
            cursor=None,
        )

    # Paper with no raw_text is excluded from the query — no AI call, nothing processed
    assert result["processed"] == 0
    assert result["skipped_no_raw_text"] == 0
    assert result["remaining"] == 0
    mock_ai.call_with_meta.assert_not_called()


def test_backfill_with_raw_text_updates_intents(db, tmp_path):
    """Full backfill flow: seed + raw_text → mock AI → intents updated."""
    paper_store, user_library, user_store = db
    user = user_store.create_user(email="backfill@test.com", password_hash="hashed")
    user_id = user.user_id

    pid = paper_store.register_paper(title="Raw Text Paper", pdf_hash="rthash")
    user_library.add_source(pid, "raw_text_paper", status="completed", user_id=user_id)
    paper_store.save_citation_links(pid, [
        {"title": "Gap To Update", "authors": "X", "year": 2020}  # intent=NULL
    ])
    paper_store.update_paper_raw_text(pid, "This paper uses the method of Gap To Update etc.")

    from unittest.mock import patch

    from klemma.ai import AICallResult

    mock_ai_result = MagicMock(spec=AICallResult)
    mock_ai_result.text = '{"key_references": [{"title": "Gap To Update", "citation_intent": "method"}]}'
    mock_ai_result.input_tokens = 100
    mock_ai_result.output_tokens = 50

    mock_ai = MagicMock()
    mock_ai.call_with_meta.return_value = mock_ai_result
    mock_ai_config = MagicMock()
    mock_ai_config.model = "test"

    with patch("klemma.api.tasks._create_ai_provider", return_value=(mock_ai, mock_ai_config)):
        from klemma.api.tasks import backfill_citation_intents

        result = backfill_citation_intents(
            user_id=user_id,
            data_dir=str(tmp_path),
            batch_size=10,
            cursor=None,
        )

    assert result["processed"] == 1
    assert result["skipped_no_raw_text"] == 0
    assert result["failed"] == 0
    assert _get_intent(paper_store, pid, "Gap To Update") == "method"


# ---------------------------------------------------------------------------
# Admin endpoint — auth tests
# ---------------------------------------------------------------------------


def test_backfill_endpoint_requires_auth(client):
    """Unauthenticated request → 403."""
    resp = client.post("/admin/backfill/citation-intents?target_user_id=someuser")
    assert resp.status_code == 403


def test_backfill_endpoint_non_admin_403(client):
    """Second registered user is not admin → 403."""
    # Register admin (first user)
    _register(client, "admin@test.com")
    # Register non-admin second user
    token2 = _register(client, "nonadmin@test.com")

    user_id = client.get("/auth/me", headers=_auth(token2)).json()["user_id"]
    resp = client.post(
        f"/admin/backfill/citation-intents?target_user_id={user_id}",
        headers=_auth(token2),
    )
    assert resp.status_code == 403


def test_backfill_endpoint_admin_succeeds(client, stores, tmp_path, monkeypatch):
    """First registered user (admin) can call the backfill endpoint."""
    user_store, paper_store, user_library, _, _ = stores
    token = _register(client)
    user_id = client.get("/auth/me", headers=_auth(token)).json()["user_id"]

    # Monkeypatch the task to avoid AI call

    def _fake_backfill(user_id, data_dir, batch_size=20, cursor=None, dry_run=False):
        return {"processed": 0, "skipped_no_raw_text": 0, "failed": 0,
                "next_cursor": None, "remaining": 0}

    monkeypatch.setattr("klemma.api.tasks.backfill_citation_intents", _fake_backfill)

    resp = client.post(
        f"/admin/backfill/citation-intents?target_user_id={user_id}",
        headers=_auth(token),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["processed"] == 0
    assert data["remaining"] == 0
