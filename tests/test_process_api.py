"""Tests for process API endpoints (ADR-009, #186)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from klemma.api.app import create_app
from klemma.api.auth.deps import set_user_store
from klemma.api.deps import set_paper_store, set_project_store, set_user_library
from klemma.api.rate_limit import reset_rate_limiter
from klemma.stores.paper_store import LocalPaperStore
from klemma.stores.project_store import LocalProjectStore
from klemma.stores.user_library import LocalUserLibrary
from klemma.stores.user_store import LocalUserStore


@pytest.fixture
def stores(tmp_path):
    user_store = LocalUserStore(tmp_path / "users.db")
    library_db = tmp_path / "library.db"
    paper_store = LocalPaperStore(library_db)
    user_library = LocalUserLibrary(library_db)
    project_store = LocalProjectStore(tmp_path / "project.db")
    return user_store, paper_store, user_library, project_store


@pytest.fixture
def client(stores) -> TestClient:
    user_store, paper_store, user_library, project_store = stores
    app = create_app()
    set_user_store(user_store)
    set_paper_store(paper_store)
    set_user_library(user_library)
    set_project_store(project_store)
    reset_rate_limiter()
    return TestClient(app)


def _auth_token(client: TestClient) -> str:
    resp = client.post(
        "/auth/register",
        json={"email": "proc@example.com", "password": "secret123"},
    )
    return resp.json()["access_token"]


def _headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _add_source(client, token, citekey="smithML2020"):
    client.post(
        "/library/sources",
        json={"citekey": citekey, "title": f"Paper {citekey}"},
        headers=_headers(token),
    )


# ---------------------------------------------------------------------------
# Submit process job
# ---------------------------------------------------------------------------


def test_submit_process_source_not_found(client):
    token = _auth_token(client)
    resp = client.post("/process/sources/nonexistent", headers=_headers(token))
    assert resp.status_code == 404


def test_submit_process_enqueues_job(client, monkeypatch):
    token = _auth_token(client)
    _add_source(client, token)

    mock_job = MagicMock()
    mock_job.id = "test-job-123"
    mock_queue_instance = MagicMock()
    mock_queue_instance.enqueue.return_value = mock_job

    # Patch the module-level imports in process.py
    import klemma.api.routes.process as proc_mod

    monkeypatch.setattr(proc_mod, "_RQ_AVAILABLE", True)
    monkeypatch.setattr(proc_mod, "Redis", MagicMock())
    monkeypatch.setattr(proc_mod, "Queue", MagicMock(return_value=mock_queue_instance))

    resp = client.post("/process/sources/smithML2020", headers=_headers(token))

    assert resp.status_code == 202
    data = resp.json()
    assert data["job_id"] == "test-job-123"
    assert data["status"] == "queued"
    assert data["citekey"] == "smithML2020"


# ---------------------------------------------------------------------------
# Local-fallback gate (#369): KLEMMA_ALLOW_LOCAL_JOBS must be exactly "1"
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("env_value", "expected"),
    [
        (None, False),       # unset → fallback disabled (production default)
        ("", False),         # empty string → disabled
        ("0", False),         # explicit-off, MUST NOT enable fallback
        ("false", False),    # word-form-off, MUST NOT enable fallback
        ("no", False),       # word-form-off, MUST NOT enable fallback
        ("true", False),     # only "1" enables; word-form-on is rejected
        ("1", True),         # documented opt-in
    ],
)
def test_local_jobs_allowed_strict_equality(monkeypatch, env_value, expected):
    """`bool(os.environ.get(...))` would treat "0"/"false"/"no" as truthy.

    Regression for codex-rescue P1 on PR #377: ensure `_local_jobs_allowed()`
    only honors the literal string "1" so an operator typing
    `KLEMMA_ALLOW_LOCAL_JOBS=0` cannot accidentally re-enable the unsafe
    per-process fallback in production.
    """
    from klemma.api.routes.process import _local_jobs_allowed

    monkeypatch.delenv("KLEMMA_ALLOW_LOCAL_JOBS", raising=False)
    if env_value is not None:
        monkeypatch.setenv("KLEMMA_ALLOW_LOCAL_JOBS", env_value)
    assert _local_jobs_allowed() is expected


def test_submit_process_returns_503_when_redis_unavailable_and_fallback_off(
    client, monkeypatch
):
    """No KLEMMA_ALLOW_LOCAL_JOBS=1 + Redis unavailable → 503, not silent local fallback."""
    token = _auth_token(client)
    _add_source(client, token)

    monkeypatch.delenv("KLEMMA_ALLOW_LOCAL_JOBS", raising=False)

    import klemma.api.routes.process as proc_mod

    # Redis path disabled — would otherwise enqueue successfully
    monkeypatch.setattr(proc_mod, "_RQ_AVAILABLE", False)

    resp = client.post("/process/sources/smithML2020", headers=_headers(token))
    assert resp.status_code == 503
    assert "KLEMMA_ALLOW_LOCAL_JOBS" in resp.json()["detail"]


def test_submit_process_returns_503_when_fallback_var_is_zero(client, monkeypatch):
    """Regression: KLEMMA_ALLOW_LOCAL_JOBS=0 must NOT enable the fallback.

    Prior to the PR #377 strict-equality fix, `bool("0")` evaluated to True
    and silently allowed the unsafe per-process fallback in production.
    """
    token = _auth_token(client)
    _add_source(client, token)

    monkeypatch.setenv("KLEMMA_ALLOW_LOCAL_JOBS", "0")

    import klemma.api.routes.process as proc_mod

    monkeypatch.setattr(proc_mod, "_RQ_AVAILABLE", False)

    resp = client.post("/process/sources/smithML2020", headers=_headers(token))
    assert resp.status_code == 503


# ---------------------------------------------------------------------------
# Job status
# ---------------------------------------------------------------------------


def test_job_status_finished(client, monkeypatch):
    token = _auth_token(client)

    mock_job = MagicMock()
    mock_job.get_status.return_value = "finished"
    mock_job.is_finished = True
    mock_job.result = {"status": "ok", "fragment_count": 5}

    import klemma.api.routes.process as proc_mod

    monkeypatch.setattr(proc_mod, "_RQ_AVAILABLE", True)
    monkeypatch.setattr(proc_mod, "Redis", MagicMock())
    mock_job_cls = MagicMock()
    mock_job_cls.fetch.return_value = mock_job
    monkeypatch.setattr(proc_mod, "Job", mock_job_cls)

    resp = client.get("/process/jobs/test-123", headers=_headers(token))

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "finished"
    assert data["result"]["fragment_count"] == 5


def test_job_status_business_error_promoted_to_failed(client, monkeypatch):
    """Tasks that return {status: error, ...} surface as outer status='failed'.

    Without this promotion, callers see status='finished' for token-limit
    exhaustion and other recoverable errors, then must defensively unwrap
    result.status. Regression guard for the golden E2E find.
    """
    token = _auth_token(client)

    mock_job = MagicMock()
    mock_job.get_status.return_value = "finished"
    mock_job.is_finished = True
    mock_job.result = {"status": "error", "detail": "Token limit exhausted"}

    import klemma.api.routes.process as proc_mod

    monkeypatch.setattr(proc_mod, "_RQ_AVAILABLE", True)
    monkeypatch.setattr(proc_mod, "Redis", MagicMock())
    mock_job_cls = MagicMock()
    mock_job_cls.fetch.return_value = mock_job
    monkeypatch.setattr(proc_mod, "Job", mock_job_cls)

    resp = client.get("/process/jobs/test-456", headers=_headers(token))

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "failed"
    assert data["result"]["detail"] == "Token limit exhausted"


def test_job_status_local_business_error_promoted_to_failed(client):
    """Same promotion applies to the in-memory local job store."""
    import klemma.api.routes.process as proc_mod

    proc_mod._local_jobs["local-err-1"] = {
        "status": "finished",
        "result": {"status": "error", "detail": "PDF text too short"},
    }
    token = _auth_token(client)
    try:
        resp = client.get("/process/jobs/local-err-1", headers=_headers(token))
    finally:
        proc_mod._local_jobs.pop("local-err-1", None)

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "failed"
    assert data["result"]["detail"] == "PDF text too short"


def test_job_status_not_found(client, monkeypatch):
    token = _auth_token(client)

    import klemma.api.routes.process as proc_mod

    monkeypatch.setattr(proc_mod, "_RQ_AVAILABLE", True)
    monkeypatch.setattr(proc_mod, "Redis", MagicMock())
    mock_job_cls = MagicMock()
    mock_job_cls.fetch.side_effect = Exception("No such job")
    monkeypatch.setattr(proc_mod, "Job", mock_job_cls)

    resp = client.get("/process/jobs/nonexistent", headers=_headers(token))
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Auth required
# ---------------------------------------------------------------------------


def test_process_requires_auth(client):
    resp = client.post("/process/sources/anything")
    assert resp.status_code == 401


def test_job_status_requires_auth(client):
    resp = client.get("/process/jobs/anything")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Chunked extraction task (unit-level, all external deps mocked)
# ---------------------------------------------------------------------------


def test_chunked_extraction_accumulates_fragments_from_all_chunks(tmp_path):
    """process_source() calls AI once per chunk and merges fragment lists.

    build_chunks_from_pages is patched to return exactly 2 chunks so the test
    is independent of PDF size/layout.  Each mock AI call returns 5 distinct
    fragments → 10 fragments total (5 per chunk × 2 chunks).
    """
    import json
    from unittest.mock import MagicMock, patch

    from klemma.api.tasks import process_source
    from klemma.literature.pdf import ChunkRecord
    from klemma.stores.file_store import LocalFileStore
    from klemma.stores.paper_store import LocalPaperStore
    from klemma.stores.user_library import LocalUserLibrary

    data_dir = str(tmp_path)
    library_db = tmp_path / "library.db"
    paper_store = LocalPaperStore(library_db)
    user_library = LocalUserLibrary(library_db)
    file_store = LocalFileStore(tmp_path / "files")

    # Register a paper
    paper_id = paper_store.register_paper(title="Test Paper", pdf_hash="abc123")
    user_library.add_source(paper_id, "testkey2025", status="pending")

    # Write a minimal PDF — only needs to pass the 500-char extraction guard.
    import fitz
    paper_dir = file_store.get_paper_dir(paper_id)
    paper_dir.mkdir(parents=True, exist_ok=True)
    pdf_path = paper_dir / "test.pdf"
    doc = fitz.open()
    for i in range(3):
        page = doc.new_page()
        rect = fitz.Rect(50, 50, 550, 750)
        body = "\n".join(f"Page {i + 1} sentence {j}." for j in range(40))
        page.insert_textbox(rect, body, fontsize=10)
    doc.save(str(pdf_path))
    doc.close()

    # Patch chunking to return exactly 2 chunks regardless of PDF size
    two_chunks = [
        ChunkRecord(index=0, text="[Page 1]\n" + "A" * 1000, page_start=1, page_end=1, char_start=0, char_end=1009),
        ChunkRecord(index=1, text="[Page 2]\n" + "B" * 1000, page_start=2, page_end=2, char_start=1009, char_end=2018),
    ]

    # Build 5 distinct synthetic fragments for each chunk call
    def _make_ai_result(chunk_idx: int) -> MagicMock:
        frags = [
            {
                "text": f"Chunk {chunk_idx} fragment {j} with unique verbatim text.",
                "verbatim": False,
                "type": "key_idea",
                "page": chunk_idx + 1,
                "citation_intent": "background",
            }
            for j in range(5)
        ]
        mock_result = MagicMock()
        # Non-empty key_references prevents the bibliography fallback AI call
        mock_result.text = json.dumps({
            "fragments": frags,
            "key_references": [{"title": "Test ref", "authors": "Author A", "year": 2020}],
        })
        mock_result.input_tokens = 100
        mock_result.output_tokens = 200
        return mock_result

    call_count = [0]

    def _mock_call_with_meta(*args, **kwargs):
        idx = call_count[0]
        call_count[0] += 1
        return _make_ai_result(idx)

    mock_ai = MagicMock()
    mock_ai.call_with_meta.side_effect = _mock_call_with_meta
    mock_ai.render_prompt.return_value = "mock prompt"
    mock_ai_config = MagicMock()
    mock_ai_config.model = "test-model"

    with (
        patch("klemma.api.tasks._create_ai_provider", return_value=(mock_ai, mock_ai_config)),
        patch("klemma.api.tasks._create_embeddings_provider", return_value=None),
        patch("klemma.literature.pdf.build_chunks_from_pages", return_value=two_chunks),
        patch.dict("os.environ", {
            "KLEMMA_DATA_DIR": data_dir,
            "ANTHROPIC_API_KEY": "test-key",
            "KLEMMA_EMBEDDINGS_ALLOW_REMOTE": "1",
        }),
    ):
        result = process_source(paper_id, "testkey2025", data_dir)

    assert result["status"] == "completed", result
    # Exactly 2 chunk AI calls (bibliography fallback suppressed via key_references)
    assert call_count[0] == 2
    # Total fragments = 2 chunks × 5 (no overlap dedup — all texts differ)
    assert result["fragment_count"] == 10


def test_verbatim_validation_uses_full_text_not_per_chunk(tmp_path):
    """Regression for #379: fragment quoting text outside its own chunk must
    not be falsely downgraded.

    Setup forces the bug condition: AI for chunk 0 returns a verbatim=True
    fragment whose text exists in the real PDF (page 2) but NOT in chunk 0's
    mocked slice. Per-chunk validation (the old bug) would downgrade the
    flag because chunk[0].text doesn't contain the marker. Full-text
    validation (the fix) keeps verbatim=True because full_text — built by
    process_source from extract_pages() — contains it.
    """
    import json
    from unittest.mock import MagicMock, patch

    import fitz

    from klemma.api.tasks import process_source
    from klemma.literature.pdf import ChunkRecord
    from klemma.stores.file_store import LocalFileStore
    from klemma.stores.paper_store import LocalPaperStore
    from klemma.stores.user_library import LocalUserLibrary

    marker = "ZETAmarker42 was observed in the experimental results."

    data_dir = str(tmp_path)
    library_db = tmp_path / "library.db"
    paper_store = LocalPaperStore(library_db)
    user_library = LocalUserLibrary(library_db)
    file_store = LocalFileStore(tmp_path / "files")

    paper_id = paper_store.register_paper(title="Test Paper", pdf_hash="zetahash")
    user_library.add_source(paper_id, "zeta2026", status="pending")

    # Real PDF: page 2 contains marker, others are filler.
    paper_dir = file_store.get_paper_dir(paper_id)
    paper_dir.mkdir(parents=True, exist_ok=True)
    pdf_path = paper_dir / "test.pdf"
    doc = fitz.open()
    for i in range(3):
        page = doc.new_page()
        rect = fitz.Rect(50, 50, 550, 750)
        if i == 1:
            body = marker + "\n" + "\n".join(f"Filler {j}." for j in range(40))
        else:
            body = "\n".join(f"Page {i + 1} sentence {j}." for j in range(40))
        page.insert_textbox(rect, body, fontsize=10)
    doc.save(str(pdf_path))
    doc.close()

    # Mocked chunks: NEITHER contains marker. Per-chunk validation against
    # chunk[0].text would not find the marker text and would downgrade.
    two_chunks = [
        ChunkRecord(index=0, text="[Page 1]\n" + "A" * 1000,
                    page_start=1, page_end=1, char_start=0, char_end=1009),
        ChunkRecord(index=1, text="[Page 3]\n" + "C" * 1000,
                    page_start=3, page_end=3, char_start=1009, char_end=2018),
    ]

    # AI mock: chunk 0 returns a verbatim=True fragment quoting marker.
    def _make_ai_result(chunk_idx: int) -> MagicMock:
        if chunk_idx == 0:
            frags = [{
                "text": marker,
                "verbatim": True,
                "type": "result",
                "page": 2,
                "citation_intent": "result_comparison",
            }]
        else:
            frags = [{
                "text": "Filler claim from chunk 1.",
                "verbatim": False,
                "type": "background",
                "page": 3,
                "citation_intent": "background",
            }]
        m = MagicMock()
        m.text = json.dumps({
            "fragments": frags,
            # Non-empty key_references suppresses bibliography fallback AI call.
            "key_references": [{"title": "Test ref", "authors": "A", "year": 2020}],
        })
        m.input_tokens = 100
        m.output_tokens = 50
        return m

    call_count = [0]

    def _mock_call_with_meta(*args, **kwargs):
        idx = call_count[0]
        call_count[0] += 1
        return _make_ai_result(idx)

    mock_ai = MagicMock()
    mock_ai.call_with_meta.side_effect = _mock_call_with_meta
    mock_ai.render_prompt.return_value = "mock prompt"
    mock_ai_config = MagicMock()
    mock_ai_config.model = "test-model"

    with (
        patch("klemma.api.tasks._create_ai_provider", return_value=(mock_ai, mock_ai_config)),
        patch("klemma.api.tasks._create_embeddings_provider", return_value=None),
        patch("klemma.literature.pdf.build_chunks_from_pages", return_value=two_chunks),
        patch.dict("os.environ", {
            "KLEMMA_DATA_DIR": data_dir,
            "ANTHROPIC_API_KEY": "test-key",
            "KLEMMA_EMBEDDINGS_ALLOW_REMOTE": "1",
        }),
    ):
        result = process_source(paper_id, "zeta2026", data_dir)

    assert result["status"] == "completed", result
    saved = paper_store.get_fragments(paper_id)
    zeta = next((f for f in saved if marker in f.fragment_text), None)
    assert zeta is not None, "Marker fragment was not saved"
    assert zeta.verbatim is True, (
        "Fragment quoting full-document text was downgraded — validator is still "
        "using per-chunk slice instead of full text (#379 regression)."
    )


def test_run_chunked_extraction_falls_back_to_joined_chunk_text():
    """Direct helper test: when full_text="" the validator must still run
    (against the joined chunk text), not silently skip with stats-only seeding.

    This protects helper-level tests and any future direct caller from
    accidentally bypassing verbatim validation. Cross-chunk quotes are still
    visible to the validator because the fallback joins ALL chunk texts.
    """
    import json
    from unittest.mock import MagicMock, patch

    from klemma.api.tasks import _run_chunked_extraction
    from klemma.literature.models import ZoteroEntry
    from klemma.literature.pdf import ChunkRecord

    chunks = [
        ChunkRecord(index=0, text="[Page 1]\nIntro from chunk zero.",
                    page_start=1, page_end=1, char_start=0, char_end=30),
        ChunkRecord(index=1, text="[Page 2]\nResults from chunk one.",
                    page_start=2, page_end=2, char_start=30, char_end=62),
    ]

    # AI returns one fragment per chunk: chunk 0 quotes its own text (true
    # verbatim), chunk 1 fabricates text that's nowhere in the joined source
    # (must downgrade).
    def _ai_call(chunk_idx: int) -> MagicMock:
        if chunk_idx == 0:
            text = "Intro from chunk zero."
            verbatim = True
        else:
            text = "Fabricated claim never appearing anywhere in the document."
            verbatim = True
        m = MagicMock()
        m.text = json.dumps({
            "fragments": [{
                "text": text, "verbatim": verbatim, "type": "key_idea",
                "page": chunk_idx + 1, "citation_intent": "background",
            }],
            "key_references": [{"title": "Ref", "authors": "A", "year": 2020}],
        })
        m.input_tokens = 50
        m.output_tokens = 25
        return m

    counter = [0]

    def _side_effect(*a, **kw):
        idx = counter[0]
        counter[0] += 1
        return _ai_call(idx)

    mock_ai = MagicMock()
    mock_ai.call_with_meta.side_effect = _side_effect
    mock_ai.render_prompt.return_value = "prompt"
    mock_ai_config = MagicMock()
    mock_ai_config.model = "test-model"

    entry = ZoteroEntry(id="p1", title="Test")
    with patch("klemma.api.tasks.dedup_fragments_by_prefix", side_effect=lambda x, **_: x):
        result = _run_chunked_extraction(
            mock_ai, mock_ai_config, entry, chunks, "p1", "testkey", "/dev/null",
            # full_text="" intentionally — exercise the fallback path
        )

    assert result is not None
    saved = result["fragments"]
    intro = next(f for f in saved if "Intro" in f.fragment_text)
    fab = next(f for f in saved if "Fabricated" in f.fragment_text)
    # Real quote stays True because the joined chunk text contains it
    assert intro.verbatim is True, "Real cross-chunk-visible quote should keep verbatim=True"
    # Fabricated text is in NO chunk — must be downgraded
    assert fab.verbatim is False, "Fabricated text not in any chunk should be downgraded"
    assert result["downgrade_stats"].downgraded == 1


def test_force_reprocess_partial_failure_preserves_completed_source(tmp_path):
    """force=True must not downgrade an intact old corpus when new chunks fail."""
    import json
    from unittest.mock import MagicMock, patch

    from klemma.api.tasks import process_source
    from klemma.literature.pdf import ChunkRecord
    from klemma.models import FragmentRecord
    from klemma.stores.file_store import LocalFileStore
    from klemma.stores.paper_store import LocalPaperStore
    from klemma.stores.user_library import LocalUserLibrary

    data_dir = str(tmp_path)
    library_db = tmp_path / "library.db"
    paper_store = LocalPaperStore(library_db)
    user_library = LocalUserLibrary(library_db)
    file_store = LocalFileStore(tmp_path / "files")

    paper_id = paper_store.register_paper(title="Existing Paper", pdf_hash="force-hash")
    user_library.add_source(paper_id, "forcekey2026", status="completed")
    old_fragment = FragmentRecord(
        fragment_id="old-frag",
        paper_id=paper_id,
        fragment_text="Existing complete fragment.",
        fragment_type="key_idea",
        page_number=1,
        content_hash="old-frag",
    )
    paper_store.save_fragments(paper_id, [old_fragment], "", "old-model")

    paper_dir = file_store.get_paper_dir(paper_id)
    paper_dir.mkdir(parents=True, exist_ok=True)
    (paper_dir / "paper.pdf").write_bytes(b"dummy")

    chunks = [
        ChunkRecord(
            index=0,
            text="[Page 1]\nSuccessful chunk fragment.",
            page_start=1,
            page_end=1,
            char_start=0,
            char_end=32,
        ),
        ChunkRecord(
            index=1,
            text="[Page 2]\nBroken chunk.",
            page_start=2,
            page_end=2,
            char_start=32,
            char_end=54,
        ),
    ]

    good_result = MagicMock()
    good_result.text = json.dumps({
        "fragments": [
            {
                "text": "Successful chunk fragment.",
                "verbatim": False,
                "type": "key_idea",
                "page": 1,
                "citation_intent": "background",
            }
        ],
        "key_references": [{"title": "Ref", "authors": "Author", "year": 2024}],
    })
    good_result.input_tokens = 10
    good_result.output_tokens = 20

    bad_result = MagicMock()
    bad_result.text = "not json"
    bad_result.input_tokens = 10
    bad_result.output_tokens = 20

    mock_ai = MagicMock()
    mock_ai.render_prompt.return_value = "mock prompt"
    mock_ai.call_with_meta.side_effect = [good_result, bad_result]
    mock_ai_config = MagicMock()
    mock_ai_config.model = "test-model"

    with (
        patch("klemma.api.tasks._create_ai_provider", return_value=(mock_ai, mock_ai_config)),
        patch("klemma.api.tasks._create_embeddings_provider", return_value=None),
        patch("klemma.literature.pdf.PDFExtractor.extract_pages", return_value=["x" * 600]),
        patch("klemma.literature.pdf.build_chunks_from_pages", return_value=chunks),
        patch.dict("os.environ", {
            "KLEMMA_DATA_DIR": data_dir,
            "ANTHROPIC_API_KEY": "test-key",
            "KLEMMA_EMBEDDINGS_ALLOW_REMOTE": "1",
        }),
    ):
        result = process_source(paper_id, "forcekey2026", data_dir, force=True)

    assert result["status"] == "error"
    assert result["failed_chunks"] == 1
    assert result["chunks_processed"] == 1
    assert user_library.get_source_by_citekey("forcekey2026").status == "completed"
    assert [f.fragment_id for f in paper_store.get_fragments(paper_id)] == ["old-frag"]
