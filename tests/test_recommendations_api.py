"""API-level tests for GET /library/recommendations (#332).

Uses mocked `_create_ai_provider` to deterministically stub the LLM call,
and exercises caching, ownership, three-source threshold, and fallback paths.
Also covers regression of GET /library/gaps after refactor to
`compute_scored_gaps`.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from klemma.api.app import create_app
from klemma.api.auth.deps import set_user_store
from klemma.api.deps import (
    set_file_store,
    set_paper_store,
    set_project_store,
    set_user_library,
)
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
def stores(tmp_path):
    user_store = LocalUserStore(tmp_path / "users.db")
    library_db = tmp_path / "library.db"
    paper_store = LocalPaperStore(library_db)
    user_library = LocalUserLibrary(library_db)
    project_store = LocalProjectStore(tmp_path / "project.db")
    file_store = LocalFileStore(tmp_path / "files")
    return user_store, paper_store, user_library, project_store, file_store


@pytest.fixture
def client(stores) -> TestClient:
    user_store, paper_store, user_library, project_store, file_store = stores
    app = create_app()
    set_user_store(user_store)
    set_paper_store(paper_store)
    set_user_library(user_library)
    set_project_store(project_store)
    set_file_store(file_store)
    reset_rate_limiter()
    return TestClient(app)


def _register(client: TestClient, email: str = "rec@example.com") -> str:
    resp = client.post("/auth/register", json={"email": email, "password": "secret123"})
    return resp.json()["access_token"]


def _headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _create_project(client, token, name="Sea-ice ML") -> str:
    r = client.post(
        "/projects",
        json={"name": name, "type": "dissertation"},
        headers=_headers(token),
    )
    assert r.status_code == 201
    return r.json()["project_id"]


def _seed_three_sources(client, token, stores):
    """Add 3 completed sources with bibliographies for gap analysis."""
    _, paper_store, user_library, _, _ = stores

    # Get user_id from token
    me = client.get("/auth/me", headers=_headers(token))
    user_id = me.json()["user_id"]

    for i in range(1, 4):
        citekey = f"src{i}"
        paper_id = paper_store.register_paper(
            title=f"Paper {i}", authors=f"Author {i}", year=2023,
            abstract=f"Abstract for paper {i} on sea-ice ML forecasting.",
            pdf_hash=f"hash{i}",
        )
        user_library.add_source(paper_id, citekey, status="completed", user_id=user_id)

        # Seed citation_graph with cross-cited gaps
        paper_store.save_citation_links(paper_id, [
            {"title": "U-Net: convolutional networks", "authors": "Ronneberger",
             "year": 2015, "citation_intent": "method"},
            {"title": "ERA5 reanalysis", "authors": "Hersbach", "year": 2020,
             "citation_intent": "uses_data"},
            {"title": f"Unique ref for paper {i}", "authors": "X", "year": 2022,
             "citation_intent": "background"},
        ])

    return user_id


# ---------------------------------------------------------------------------
# AI provider stub
# ---------------------------------------------------------------------------


class _StubAI:
    def __init__(self, return_value):
        self._rv = return_value
        self.calls = []

    def call_json(self, system, user, **kwargs):
        self.calls.append({"system": system, "user": user, **kwargs})
        return self._rv


def _stub_provider(payload):
    ai = _StubAI(payload)
    cfg = SimpleNamespace(model="stub-model-1")
    return ai, cfg


# ---------------------------------------------------------------------------
# Basic shape / auth / ownership
# ---------------------------------------------------------------------------


def test_recommendations_requires_project_id(client):
    token = _register(client)
    resp = client.get("/library/recommendations", headers=_headers(token))
    assert resp.status_code == 422  # FastAPI validation


def test_recommendations_requires_three_sources(client, stores):
    token = _register(client)
    pid = _create_project(client, token)
    resp = client.get(
        f"/library/recommendations?project_id={pid}", headers=_headers(token)
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["recommendations"] == []
    assert "минимум 3" in data["detail"]


def test_recommendations_requires_project_ownership(client, stores):
    # User A creates a project
    token_a = _register(client, "a@example.com")
    pid = _create_project(client, token_a)

    # User B tries to read recommendations for A's project
    token_b = _register(client, "b@example.com")
    resp = client.get(
        f"/library/recommendations?project_id={pid}", headers=_headers(token_b)
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Happy path + cache
# ---------------------------------------------------------------------------


def test_recommendations_happy_path(client, stores):
    token = _register(client)
    pid = _create_project(client, token)
    _seed_three_sources(client, token, stores)

    payload = {"recommendations": [
        {"title": "U-Net: convolutional networks", "authors": "Ronneberger",
         "year": 2015, "rationale": "Ключевой baseline для сегментации", "score": 9},
        {"title": "ERA5 reanalysis", "authors": "Hersbach", "year": 2020,
         "rationale": "Референсные данные для моделей", "score": 8},
    ]}

    with patch("klemma.api.tasks._create_ai_provider", return_value=_stub_provider(payload)):
        resp = client.get(
            f"/library/recommendations?project_id={pid}", headers=_headers(token)
        )

    assert resp.status_code == 200
    data = resp.json()
    assert len(data["recommendations"]) == 2
    assert data["recommendations"][0]["title"] == "U-Net: convolutional networks"
    assert data["recommendations"][0]["rationale"].startswith("Ключевой")
    assert data["model"] == "stub-model-1"
    assert data["generated_at"]
    assert data["cached"] is False


def test_recommendations_cache_hit_on_second_call(client, stores):
    token = _register(client)
    pid = _create_project(client, token)
    _seed_three_sources(client, token, stores)

    payload = {"recommendations": [{"title": "X", "rationale": "R", "score": 8}]}
    ai, cfg = _stub_provider(payload)

    with patch("klemma.api.tasks._create_ai_provider", return_value=(ai, cfg)):
        r1 = client.get(
            f"/library/recommendations?project_id={pid}", headers=_headers(token)
        )
        assert r1.status_code == 200
        assert r1.json()["cached"] is False

        r2 = client.get(
            f"/library/recommendations?project_id={pid}", headers=_headers(token)
        )
        assert r2.status_code == 200
        assert r2.json()["cached"] is True

    # LLM was called exactly once
    assert len(ai.calls) == 1


def test_recommendations_cache_miss_on_outline_change(client, stores):
    token = _register(client)
    pid = _create_project(client, token)
    _seed_three_sources(client, token, stores)

    payload = {"recommendations": [{"title": "X", "rationale": "R", "score": 8}]}
    ai, cfg = _stub_provider(payload)

    with patch("klemma.api.tasks._create_ai_provider", return_value=(ai, cfg)):
        client.get(f"/library/recommendations?project_id={pid}", headers=_headers(token))
        assert len(ai.calls) == 1

        # Change outline
        client.patch(
            f"/projects/{pid}/outline",
            json={"sections": [{"id": "1", "name": "Introduction"}]},
            headers=_headers(token),
        )

        client.get(f"/library/recommendations?project_id={pid}", headers=_headers(token))
        # Cache invalidated → LLM called again
        assert len(ai.calls) == 2


def test_recommendations_cache_miss_on_source_add(client, stores):
    token = _register(client)
    pid = _create_project(client, token)
    _seed_three_sources(client, token, stores)

    payload = {"recommendations": [{"title": "X", "rationale": "R", "score": 8}]}
    ai, cfg = _stub_provider(payload)

    with patch("klemma.api.tasks._create_ai_provider", return_value=(ai, cfg)):
        client.get(f"/library/recommendations?project_id={pid}", headers=_headers(token))
        assert len(ai.calls) == 1

        # Add a new source → invalidates cache
        client.post(
            "/library/sources",
            json={"citekey": "new_paper", "title": "New Paper", "year": 2024},
            headers=_headers(token),
        )

        client.get(f"/library/recommendations?project_id={pid}", headers=_headers(token))
        assert len(ai.calls) == 2


# ---------------------------------------------------------------------------
# Fallback
# ---------------------------------------------------------------------------


def test_recommendations_fallback_on_ai_error(client, stores):
    token = _register(client)
    pid = _create_project(client, token)
    _seed_three_sources(client, token, stores)

    class _BrokenAI:
        def call_json(self, **kwargs):
            raise RuntimeError("LLM down")

    with patch("klemma.api.tasks._create_ai_provider",
               return_value=(_BrokenAI(), SimpleNamespace(model="stub-model"))):
        resp = client.get(
            f"/library/recommendations?project_id={pid}", headers=_headers(token)
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["detail"] and "AI" in data["detail"]
    # fallback still returns items — empty rationale
    for item in data["recommendations"]:
        assert item["rationale"] == ""


def test_recommendations_fallback_on_invalid_json(client, stores):
    token = _register(client)
    pid = _create_project(client, token)
    _seed_three_sources(client, token, stores)

    ai = MagicMock()
    ai.call_json.return_value = "not-a-json"
    cfg = SimpleNamespace(model="stub")

    with patch("klemma.api.tasks._create_ai_provider", return_value=(ai, cfg)):
        resp = client.get(
            f"/library/recommendations?project_id={pid}", headers=_headers(token)
        )

    assert resp.status_code == 200
    assert resp.json()["detail"]


# ---------------------------------------------------------------------------
# Regression: /library/gaps after refactor
# ---------------------------------------------------------------------------


def test_gaps_endpoint_still_works_after_refactor(client, stores):
    """Regression: refactor of list_reference_gaps into compute_scored_gaps
    must preserve response shape + recency filter behaviour."""
    token = _register(client)
    _create_project(client, token)
    _seed_three_sources(client, token, stores)

    resp = client.get("/library/gaps", headers=_headers(token))
    assert resp.status_code == 200
    data = resp.json()
    assert "gaps" in data
    assert "total" in data
    # At least U-Net + ERA5 are cross-cited ≥2 times → survive recency filter
    titles = {g["title"] for g in data["gaps"]}
    assert any("U-Net" in t for t in titles) or any("ERA5" in t for t in titles)
    # Shape sanity
    for g in data["gaps"]:
        assert "score" in g
        assert "intent_weight" in g
        assert "semantic_factor" in g


def test_gaps_endpoint_three_sources_threshold(client, stores):
    token = _register(client)
    resp = client.get("/library/gaps", headers=_headers(token))
    assert resp.status_code == 200
    assert resp.json()["detail"]
