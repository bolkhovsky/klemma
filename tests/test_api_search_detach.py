"""Tests for three new endpoints added in #277:
  - GET  /library/sources?q=  (full-text source search)
  - GET  /library/fragments/search  (fragment text search)
  - DELETE /projects/sections/{section}/sources/{citekey}  (source detach)
"""

from __future__ import annotations

import importlib.util
import os
import sqlite3

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def data_dir(tmp_path):
    d = tmp_path / "klemma_data"
    d.mkdir()
    os.environ["KLEMMA_DATA_DIR"] = str(d)
    yield d
    os.environ.pop("KLEMMA_DATA_DIR", None)


@pytest.fixture
def mock_user():
    from klemma.models import UserRecord

    return UserRecord(
        user_id="test-user-123",
        email="test@example.com",
        password_hash="xxx",
        name="Test User",
        username="test-user",
    )


@pytest.fixture
def client(data_dir, mock_user):
    """TestClient with auth bypassed and a seeded library."""
    from klemma.api.app import create_app
    from klemma.api.auth.deps import get_current_user

    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: mock_user

    with TestClient(app) as c:
        # Seed users table so project creation works
        with sqlite3.connect(str(data_dir / "users.db")) as conn:
            conn.execute(
                "INSERT OR IGNORE INTO users (user_id, email, password_hash, name)"
                " VALUES (?, ?, ?, ?)",
                (mock_user.user_id, mock_user.email, mock_user.password_hash, mock_user.name),
            )
        yield c


@pytest.fixture
def seeded_client(client, data_dir, mock_user):
    """Client with two sources in the library (no PDFs needed)."""
    client.post(
        "/library/sources",
        json={
            "citekey": "smith2022arctic",
            "title": "Sea Ice Dynamics in the Arctic",
            "authors": "Smith J.",
            "year": 2022,
        },
    )
    client.post(
        "/library/sources",
        json={
            "citekey": "jones2021climate",
            "title": "Climate Change Impacts on Ocean Systems",
            "authors": "Jones M.",
            "year": 2021,
        },
    )
    return client


# ---------------------------------------------------------------------------
# GET /library/sources?q= — full-text source search
# ---------------------------------------------------------------------------


class TestSourceSearch:
    def test_no_q_returns_all(self, seeded_client):
        resp = seeded_client.get("/library/sources")
        assert resp.status_code == 200
        assert resp.json()["total"] == 2

    def test_q_matches_title_substring(self, seeded_client):
        resp = seeded_client.get("/library/sources?q=arctic")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert data["sources"][0]["citekey"] == "smith2022arctic"

    def test_q_matches_author(self, seeded_client):
        resp = seeded_client.get("/library/sources?q=Jones")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert data["sources"][0]["citekey"] == "jones2021climate"

    def test_q_matches_citekey(self, seeded_client):
        resp = seeded_client.get("/library/sources?q=smith2022")
        assert resp.status_code == 200
        assert resp.json()["total"] == 1

    def test_q_no_match_returns_empty(self, seeded_client):
        resp = seeded_client.get("/library/sources?q=zzznomatch")
        assert resp.status_code == 200
        assert resp.json()["total"] == 0

    def test_q_case_insensitive(self, seeded_client):
        resp = seeded_client.get("/library/sources?q=ARCTIC")
        assert resp.status_code == 200
        assert resp.json()["total"] == 1


# ---------------------------------------------------------------------------
# GET /library/fragments/search — fragment text search
# ---------------------------------------------------------------------------


class TestFragmentSearch:
    def test_no_fragments_returns_empty(self, seeded_client):
        resp = seeded_client.get("/library/fragments/search?q=ice")
        assert resp.status_code == 200
        data = resp.json()
        assert data["results"] == []
        assert data["total"] == 0
        assert data["query"] == "ice"

    def test_q_too_short_rejected(self, seeded_client):
        resp = seeded_client.get("/library/fragments/search?q=a")
        assert resp.status_code == 422

    def test_with_seeded_fragments(self, client, data_dir, mock_user):
        """Seed a fragment directly into library.db and verify it's found."""
        from klemma.stores.paper_store import LocalPaperStore
        from klemma.stores.user_library import LocalUserLibrary

        lib_db = data_dir / "library.db"
        ps = LocalPaperStore(lib_db)
        ul = LocalUserLibrary(lib_db)

        paper_id = ps.register_paper(title="Arctic Ice Study", pdf_hash="aabbcc")
        ul.add_source(paper_id, "arctic2023", status="completed", user_id=mock_user.user_id)

        from klemma.models import FragmentRecord

        frags = [
            FragmentRecord(
                fragment_id="frag-001",
                paper_id=paper_id,
                fragment_text="Sea ice concentration in the Arctic has decreased significantly.",
                fragment_type="key_idea",
                page_number=1,
                citation_intent="background",
                content_hash="frag-001",
            )
        ]
        ps.save_fragments(paper_id, frags, prompt_hash="test", ai_model="test")

        resp = client.get("/library/fragments/search?q=Arctic")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] >= 1
        result = data["results"][0]
        assert result["citekey"] == "arctic2023"
        assert "Arctic" in result["text"] or "arctic" in result["text"].lower()

    def test_limit_param_respected(self, client, data_dir, mock_user):
        """limit=1 should return at most 1 result."""
        from klemma.models import FragmentRecord
        from klemma.stores.paper_store import LocalPaperStore
        from klemma.stores.user_library import LocalUserLibrary

        lib_db = data_dir / "library.db"
        ps = LocalPaperStore(lib_db)
        ul = LocalUserLibrary(lib_db)
        paper_id = ps.register_paper(title="Test Paper", pdf_hash="test123")
        ul.add_source(paper_id, "testkey", status="completed", user_id=mock_user.user_id)

        frags = [
            FragmentRecord(
                fragment_id=f"frag-{i}",
                paper_id=paper_id,
                fragment_text=f"Relevant text number {i} about methodology.",
                fragment_type="key_idea",
                page_number=i,
                citation_intent="background",
                content_hash=f"frag-{i}",
            )
            for i in range(5)
        ]
        ps.save_fragments(paper_id, frags, prompt_hash="test", ai_model="test")

        resp = client.get("/library/fragments/search?q=Relevant&limit=1")
        assert resp.status_code == 200
        assert len(resp.json()["results"]) <= 1


# ---------------------------------------------------------------------------
# DELETE /projects/sections/{section}/sources/{citekey} — detach
# ---------------------------------------------------------------------------


class TestDetachSourceFromSection:
    @pytest.fixture
    def setup_assignment(self, client, data_dir, mock_user):
        """Create a source in library and assign it to section 1.1."""
        client.post(
            "/library/sources",
            json={"citekey": "smith2022", "title": "Smith 2022", "authors": "Smith"},
        )
        client.post(
            "/projects/sections/assign",
            json={"citekey": "smith2022", "sections": ["1.1"], "chapters": [1]},
        )
        return client

    def test_detach_removes_assignment(self, setup_assignment):
        c = setup_assignment
        # Confirm it's assigned
        resp = c.get("/projects/sources/smith2022/sections")
        assert "1.1" in resp.json()["sections"]

        # Detach
        resp = c.delete("/projects/sections/1.1/sources/smith2022")
        assert resp.status_code == 204

        # Confirm removed
        resp = c.get("/projects/sources/smith2022/sections")
        assert "1.1" not in resp.json()["sections"]

    def test_detach_not_found_returns_404(self, client):
        """Detaching a non-existent assignment returns 404."""
        client.post(
            "/library/sources",
            json={"citekey": "ghost2020", "title": "Ghost"},
        )
        resp = client.delete("/projects/sections/9.9/sources/ghost2020")
        assert resp.status_code == 404

    def test_detach_does_not_remove_other_sections(self, setup_assignment):
        """Detaching from 1.1 leaves other section assignments intact."""
        c = setup_assignment
        # Assign to second section
        c.post(
            "/projects/sections/assign",
            json={"citekey": "smith2022", "sections": ["1.1", "2.1"], "chapters": [1, 2]},
        )
        # Detach from 1.1 only
        c.delete("/projects/sections/1.1/sources/smith2022")

        resp = c.get("/projects/sources/smith2022/sections")
        sections = resp.json()["sections"]
        assert "1.1" not in sections
        assert "2.1" in sections


# ---------------------------------------------------------------------------
# POST /library/fragments/semantic-search
# ---------------------------------------------------------------------------

_VEC_AVAILABLE = importlib.util.find_spec("sqlite_vec") is not None
_skip_no_vec = pytest.mark.skipif(not _VEC_AVAILABLE, reason="sqlite-vec not installed")


class TestSemanticSearch:
    """Tests for POST /library/fragments/semantic-search (M2)."""

    _DIM = 1024

    def _seed_library(self, data_dir, mock_user):
        """Seed library.db with two papers, each with one fragment + embedding."""
        from klemma.models import FragmentRecord
        from klemma.stores.paper_store import LocalPaperStore
        from klemma.stores.user_library import LocalUserLibrary

        os.environ["KLEMMA_EMBEDDINGS_MODEL"] = "test-model"
        lib_db = data_dir / "library.db"
        ps = LocalPaperStore(lib_db)
        ul = LocalUserLibrary(lib_db)

        def _vec(hot: int) -> list[float]:
            v = [0.0] * self._DIM
            v[hot] = 1.0
            return v

        papers = [
            ("paper-A", "hash-A", "Ice Edge Prediction", "Author A", 2021),
            ("paper-B", "hash-B", "Sea Ice Forecasting", "Author B", 2022),
        ]
        frags = [
            ("frag-A", "paper-A", "IIEE metric measures ice edge location error", _vec(0)),
            ("frag-B", "paper-B", "SPS index for Southern Ocean ice forecasting", _vec(1)),
        ]

        for paper_id, pdf_hash, title, authors, year in papers:
            ps.register_paper(title=title, authors=authors, year=year, pdf_hash=pdf_hash)
            # Insert with explicit paper_id
            raw_conn = sqlite3.connect(str(lib_db))
            raw_conn.execute(
                "UPDATE papers SET paper_id=? WHERE pdf_hash=?", (paper_id, pdf_hash)
            )
            raw_conn.commit()
            raw_conn.close()
            ul.add_source(paper_id, paper_id.lower().replace("-", "_"),
                          status="completed", user_id=mock_user.user_id)

        for frag_id, paper_id, text, vec in frags:
            frag = FragmentRecord(
                fragment_id=frag_id,
                paper_id=paper_id,
                fragment_text=text,
                fragment_type="key_idea",
                page_number=1,
                citation_intent="background",
                content_hash=frag_id,
            )
            ps.save_fragments(paper_id, [frag], prompt_hash="p", ai_model="m")
            ps.save_fragment_embedding(frag_id, vec, "test-model")

        return ps

    @_skip_no_vec
    def test_semantic_search_returns_ranked_results(self, client, data_dir, mock_user):
        """Query similar to frag-A should return frag-A first."""
        from unittest.mock import MagicMock, patch

        self._seed_library(data_dir, mock_user)

        # Mock embedding provider to return vec close to frag-A (hot=0)
        mock_emb = MagicMock()
        mock_emb.embed.return_value = [1.0] + [0.0] * (self._DIM - 1)

        with patch("klemma.api.tasks._create_embeddings_provider", return_value=mock_emb):
            resp = client.post(
                "/library/fragments/semantic-search",
                json={"query": "ice edge error metric", "limit": 5},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] >= 1
        # frag-A must appear (closest to query vec)
        fragment_ids = [r["fragment_id"] for r in data["results"]]
        assert "frag-A" in fragment_ids
        assert data["results"][0]["fragment_id"] == "frag-A"
        assert data["results"][0]["similarity"] > 0.9

    @_skip_no_vec
    def test_semantic_search_citekey_filter(self, client, data_dir, mock_user):
        """Restricting to paper_b citekey must exclude frag-A."""
        from unittest.mock import MagicMock, patch

        self._seed_library(data_dir, mock_user)

        mock_emb = MagicMock()
        mock_emb.embed.return_value = [1.0] + [0.0] * (self._DIM - 1)

        with patch("klemma.api.tasks._create_embeddings_provider", return_value=mock_emb):
            resp = client.post(
                "/library/fragments/semantic-search",
                json={"query": "ice forecasting", "limit": 5, "citekey": "paper_b"},
            )

        assert resp.status_code == 200
        ids = [r["fragment_id"] for r in resp.json()["results"]]
        assert "frag-A" not in ids

    def test_semantic_search_returns_empty_when_provider_unavailable(self, client, data_dir):
        """When embedding provider raises, endpoint returns empty results (not 500)."""
        from unittest.mock import patch

        with patch(
            "klemma.api.tasks._create_embeddings_provider",
            side_effect=RuntimeError("no embeddings"),
        ):
            resp = client.post(
                "/library/fragments/semantic-search",
                json={"query": "any query"},
            )

        assert resp.status_code == 200
        assert resp.json()["total"] == 0

    def test_semantic_search_citekey_not_found_returns_404(self, client):
        """Unknown citekey filter returns 404."""
        from unittest.mock import MagicMock, patch

        mock_emb = MagicMock()
        mock_emb.embed.return_value = [1.0] + [0.0] * (self._DIM - 1)

        with patch("klemma.api.tasks._create_embeddings_provider", return_value=mock_emb):
            resp = client.post(
                "/library/fragments/semantic-search",
                json={"query": "query", "citekey": "nonexistent_key"},
            )

        assert resp.status_code == 404
