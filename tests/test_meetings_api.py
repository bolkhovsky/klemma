"""API tests for the meeting-analytics portal routes.

Offline: the Layer-A bridge is monkeypatched to a temp StateManager and a None
embeddings/AI provider, so no network/Ollama is needed. Auth is bypassed via a
dependency override.
"""

from datetime import date, timedelta
from types import SimpleNamespace

import pytest

from klemma.meetings import import_meeting, parse_protocol
from klemma.meetings_sites import set_access, upsert_sites
from klemma.state import StateManager

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

# Dynamic dates keep meetings inside the rolling 90-day windows used by
# /meetings/sites counts and /meetings/analytics regardless of when tests run.
DATE_RECENT = (date.today() - timedelta(days=1)).isoformat()
DATE_OLDER = (date.today() - timedelta(days=3)).isoformat()

# Synthetic portal sites: resolver maps M1 (site "Челябинск") / M2 ("Тольятти")
# via significant-token overlap with the site names.
PORTAL_SITES = [
    {"site_slug": "oms_chel", "site_name": "ОМС Челябинск",
     "site_keywords": ["омс челябинск"], "enabled": True},
    {"site_slug": "oms_tlt", "site_name": "ОМС Тольятти",
     "site_keywords": ["омс тольятти"], "enabled": True},
]

M1 = f"""\
---
date: {DATE_RECENT}
type: ОМС
site: Челябинск
speakers: [Анна Иванова, Пётр Сидоров]
---
**Оперативка по трубе**
**Саммари по темам**:
## Дефицит трубы 1420
- Анна Иванова сообщила о дефиците трубы 1420. [2:01]
**Принятые решения**:
- Найти альтернативного поставщика.
**Задачи:**
- Подготовить список поставщиков (**Assignee:** Пётр Сидоров, **deadline:** просроч. 21 июн) [12:40]
"""

M2 = f"""\
---
date: {DATE_OLDER}
type: ОМС
site: Тольятти
speakers: [Анна Иванова]
---
**Снабжение Тольятти**
**Саммари по темам**:
## Дефицит трубы 1420
- Анна Иванова уточнила сроки трубы 1420. [4:31]
**Задачи:**
- Свести остатки (**Assignee:** Анна Иванова, **deadline:** 26 июн) [8:00]
"""


@pytest.fixture()
def client(tmp_path, monkeypatch):
    # Seed a temp meeting DB (no embeddings → no network). Sites registered
    # BEFORE import so site_slug resolves at write time.
    state = StateManager(str(tmp_path / "data" / "klemma.db"))
    upsert_sites(state, PORTAL_SITES)
    import_meeting(state, None, parse_protocol(M1), "m1")
    import_meeting(state, None, parse_protocol(M2), "m2")

    # Boot the app fully offline
    monkeypatch.setenv("KLEMMA_EMBEDDINGS_ALLOW_REMOTE", "1")
    monkeypatch.setenv("KLEMMA_DATA_DIR", str(tmp_path / "saas"))
    monkeypatch.setenv("KLEMMA_BONUM_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setenv("KLEMMA_JWT_SECRET", "test-secret")
    monkeypatch.setenv("KLEMMA_BONUM_INGEST_TOKEN", "test-ingest")
    monkeypatch.delenv("KLEMMA_BONUM_SITES_WEBHOOK", raising=False)

    import klemma.api.routes.meetings as mroutes

    monkeypatch.setattr(mroutes, "build_state_and_embeddings", lambda *a, **k: (state, None))
    monkeypatch.setattr(mroutes, "build_ai", lambda *a, **k: (None, "test-model"))
    mroutes._AI_CACHE.clear()

    from klemma.api.app import create_app
    from klemma.api.auth.deps import get_current_user

    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(user_id="u1")

    with TestClient(app) as c:
        c.meeting_state = state  # handed to tests that write access rows
        yield c


def _set_leader(client, slugs):
    set_access(client.meeting_state, "u1", "leader", slugs)


def test_list_meetings(client):
    r = client.get("/meetings")
    assert r.status_code == 200
    data = r.json()
    assert data["stats"]["meetings"] == 2
    assert data["stats"]["tasks"] == 2
    assert data["stats"]["escalations"] == 1  # the overdue task is tagged escalation
    titles = {m["title"] for m in data["meetings"]}
    assert "Оперативка по трубе" in titles
    m = next(m for m in data["meetings"] if m["title"] == "Оперативка по трубе")
    assert m["type"] == "ОМС"
    assert m["task_list"][0]["who"] == "Пётр Сидоров"
    assert any(c["tone"] == "err" for c in m["chips"])  # escalation chip


def test_tasks_aggregate(client):
    r = client.get("/meetings/tasks")
    assert r.status_code == 200
    data = r.json()
    # "Дефицит трубы 1420" appears in both meetings → recurring theme
    themes = {t["title"]: t["count"] for t in data["themes"]}
    assert themes.get("Дефицит трубы 1420") == 2
    assert any(s["label"] == "просрочено" for s in data["stats"])
    assert data["escalations"]  # one escalation present
    # Overdue-by-site ranking uses the registry display name
    assert data["overdue_sites"][0]["name"] == "ОМС Челябинск"


def test_search_offline_is_safe(client):
    # No embeddings backend → empty results, but 200 and correct shape
    r = client.get("/meetings/search", params={"q": "труба"})
    assert r.status_code == 200
    data = r.json()
    assert data["query"] == "труба"
    assert data["results"] == []
    assert data["semantic_count"] == 0


def test_ask_offline_is_safe(client):
    r = client.post("/meetings/ask", json={"query": "что с трубой?"})
    assert r.status_code == 200
    data = r.json()
    assert data["model"] == "test-model"
    assert "sources" in data and "followups" in data


def test_ask_with_site_offline(client):
    r = client.post("/meetings/ask", json={"query": "что с трубой?", "site": "oms_chel"})
    assert r.status_code == 200


INGEST_PAYLOAD = {
    "meeting_id": "abc-123",
    "date": "2026-06-25",
    "type": "Scrum",
    "site": "ИТ",
    "speakers": ["Дмитрий Соколов"],
    "protocol_md": "**Спринт 99**\n\n**Саммари по темам**:\n## Релиз\n- Дмитрий Соколов доложил о релизе. [1:00]\n",
    "tasks": [{"assignee": "Дмитрий Соколов", "action": "Выкатить релиз", "deadline": "27 июн", "timecode": "1:00"}],
}


def test_ingest_requires_token(client):
    # No token → 401
    assert client.post("/meetings/ingest", json=INGEST_PAYLOAD).status_code == 401
    # Wrong token → 401
    r = client.post("/meetings/ingest", json=INGEST_PAYLOAD, headers={"X-Ingest-Token": "nope"})
    assert r.status_code == 401


def test_ingest_adds_meeting(client):
    before = client.get("/meetings").json()["stats"]["meetings"]
    r = client.post("/meetings/ingest", json=INGEST_PAYLOAD, headers={"X-Ingest-Token": "test-ingest"})
    assert r.status_code == 200
    assert r.json()["fragments"] >= 1
    after = client.get("/meetings").json()
    assert after["stats"]["meetings"] == before + 1
    assert any(m["title"] == "Спринт 99" for m in after["meetings"])


def test_ingest_accepts_explicit_site_slug(client):
    """Отправитель, который знает слаг (мобильный воркер читает его из того же
    GET /meetings/sites), не должен зависеть от нечёткого резолвера: 'ИТ' не
    матчится ни на одну площадку реестра, и без явного слага встреча осталась бы
    неразрешённой — невидимой для руководителя площадки."""
    unresolved = dict(INGEST_PAYLOAD, meeting_id="no-slug", date=DATE_RECENT)
    assert client.post("/meetings/ingest", json=unresolved,
                       headers={"X-Ingest-Token": "test-ingest"}).status_code == 200
    assert not any(
        m["title"] == "Спринт 99"
        for m in client.get("/meetings", params={"site": "oms_tlt"}).json()["meetings"]
    )

    explicit = dict(INGEST_PAYLOAD, meeting_id="with-slug", date=DATE_RECENT,
                    site_slug="oms_tlt")
    assert client.post("/meetings/ingest", json=explicit,
                       headers={"X-Ingest-Token": "test-ingest"}).status_code == 200
    scoped = client.get("/meetings", params={"site": "oms_tlt"}).json()["meetings"]
    assert any(m["title"] == "Спринт 99" for m in scoped)


def test_ingest_ignores_unknown_site_slug(client):
    """Опечатка в слаге не создаёт встречу, до которой не дойдёт ни один аккаунт:
    падаем обратно на резолвер по имени площадки и заголовку."""
    payload = dict(INGEST_PAYLOAD, meeting_id="bad-slug", date=DATE_RECENT,
                   site="Челябинск", title="ОМС Челябинск", site_slug="oms_chelyabinks")
    assert client.post("/meetings/ingest", json=payload,
                       headers={"X-Ingest-Token": "test-ingest"}).status_code == 200
    scoped = client.get("/meetings", params={"site": "oms_chel"}).json()["meetings"]
    assert any(m["title"] == "ОМС Челябинск" for m in scoped)


def test_get_meeting_404(client):
    assert client.get("/meetings/nope").status_code == 404


def test_requires_query_min_length(client):
    assert client.get("/meetings/search", params={"q": "a"}).status_code == 422


# ── Sites registry + access scoping ───────────────────────────────────────────


def test_sites_endpoint_director(client):
    r = client.get("/meetings/sites")
    assert r.status_code == 200
    data = r.json()
    assert data["role"] == "director"
    assert data["can_view_all"] is True
    sites = {s["slug"]: s for s in data["sites"]}
    assert set(sites) == {"oms_chel", "oms_tlt"}
    assert sites["oms_chel"]["name"] == "ОМС Челябинск"
    assert sites["oms_chel"]["meetings"] == 1
    assert sites["oms_tlt"]["meetings"] == 1


def test_sites_endpoint_leader(client):
    _set_leader(client, ["oms_tlt"])
    data = client.get("/meetings/sites").json()
    assert data["role"] == "leader"
    assert data["can_view_all"] is False
    assert [s["slug"] for s in data["sites"]] == ["oms_tlt"]


def test_meetings_site_filter(client):
    data = client.get("/meetings", params={"site": "oms_chel"}).json()
    assert data["stats"]["meetings"] == 1
    assert data["meetings"][0]["title"] == "Оперативка по трубе"
    # `site` field carries the registry display name for resolved meetings
    assert data["meetings"][0]["site"] == "ОМС Челябинск"


def test_meetings_days_filter(client):
    data = client.get("/meetings", params={"days": 7}).json()
    assert data["stats"]["meetings"] == 2  # both meetings are 1-3 days old


def test_leader_scope_defaults_to_their_sites(client):
    _set_leader(client, ["oms_tlt"])
    data = client.get("/meetings").json()
    assert data["stats"]["meetings"] == 1
    assert data["meetings"][0]["site"] == "ОМС Тольятти"


def test_leader_foreign_site_403(client):
    _set_leader(client, ["oms_tlt"])
    assert client.get("/meetings", params={"site": "oms_chel"}).status_code == 403
    assert client.get("/meetings/tasks", params={"site": "oms_chel"}).status_code == 403
    r = client.post("/meetings/ask", json={"query": "что с трубой?", "site": "oms_chel"})
    assert r.status_code == 403


def test_meeting_detail_scoped_for_leader(client):
    mid = client.get("/meetings", params={"site": "oms_chel"}).json()["meetings"][0]["id"]
    _set_leader(client, ["oms_tlt"])
    assert client.get(f"/meetings/{mid}").status_code == 404


# ── Analytics ─────────────────────────────────────────────────────────────────


class _StubAI:
    def __init__(self):
        self.calls = 0

    def call_json(self, system, user, max_tokens=8192, temperature=0.2, **kwargs):
        self.calls += 1
        return {"summary": "Стаб-сводка.", "topics": [], "kpis": [], "patterns": []}


def test_analytics_endpoint_with_stub_ai(client, monkeypatch):
    import klemma.api.routes.meetings as mroutes

    stub = _StubAI()
    monkeypatch.setattr(mroutes, "build_ai", lambda *a, **k: (stub, "stub-model"))
    mroutes._AI_CACHE.clear()

    # Third meeting so the ≥3 LLM threshold is met (dynamic date, in-window).
    payload = dict(INGEST_PAYLOAD, meeting_id="third", date=DATE_RECENT,
                   site="Челябинск", title="ОМС Челябинск")
    r = client.post("/meetings/ingest", json=payload, headers={"X-Ingest-Token": "test-ingest"})
    assert r.status_code == 200

    # Plain page load before any generation: fast preview, NO LLM call
    data0 = client.get("/meetings/analytics", params={"days": 90}).json()
    assert stub.calls == 0
    assert data0["detail"].startswith("Отчёт ещё не сформирован")
    assert data0["metrics"]["totals"]["meetings"] == 3

    # Explicit refresh (the «Обновить» button) triggers the LLM
    r = client.get("/meetings/analytics", params={"days": 90, "refresh": 1})
    assert r.status_code == 200
    data = r.json()
    assert data["site"] == ""
    assert data["site_name"] == "Вся компания"
    assert data["days"] == 90
    assert data["meetings_analyzed"] == 3
    assert data["summary"] == "Стаб-сводка."
    assert data["model"] == "stub-model"
    assert data["metrics"]["totals"]["meetings"] == 3
    assert stub.calls == 1

    # Subsequent plain loads are served from cache — no extra LLM call
    data2 = client.get("/meetings/analytics", params={"days": 90}).json()
    assert data2["cached"] is True
    assert stub.calls == 1


def test_analytics_days_clamped_metrics_only(client):
    # Plain load, nothing generated yet → preview with clamped window, no LLM
    data = client.get("/meetings/analytics", params={"days": 45}).json()
    assert data["days"] == 30  # clamped to the nearest allowed window
    assert data["detail"].startswith("Отчёт ещё не сформирован")
    # Refresh with <3 meetings → metrics-only degradation, cached
    data = client.get("/meetings/analytics", params={"days": 45, "refresh": 1}).json()
    assert data["days"] == 30
    assert data["detail"] == "Недостаточно данных за период"  # 2 meetings < 3
    assert data["metrics"]["totals"]["meetings"] == 2


def test_analytics_leader_rules(client):
    _set_leader(client, ["oms_tlt"])
    # Omitted site with a single slug → defaults to it
    data = client.get("/meetings/analytics", params={"days": 90}).json()
    assert data["site"] == "oms_tlt"
    # Foreign site → 403
    r = client.get("/meetings/analytics", params={"site": "oms_chel", "days": 90})
    assert r.status_code == 403
    # Multiple slugs + omitted site → 403 (frontend passes explicit slugs)
    _set_leader(client, ["oms_tlt", "oms_chel"])
    assert client.get("/meetings/analytics", params={"days": 90}).status_code == 403


# ── Sites sync ────────────────────────────────────────────────────────────────

# The rename adds a significant token ("обновл") that breaks the layer-3
# name-token match, so the sync payload ships a keyword that still resolves —
# exactly how prod keywords compensate for display-name churn.
SYNC_PAYLOAD = {
    "result": [
        {"collection_name": "sites",
         "value": {"site_slug": "oms_chel", "site_name": "ОМС Челябинск (обновл.)",
                   "site_keywords": ["челябинск"], "enabled": True}},
    ]
}


def test_sites_sync_requires_token(client):
    assert client.post("/meetings/sites/sync", json={}).status_code == 401


def test_sites_sync_no_url_configured(client):
    r = client.post("/meetings/sites/sync", json={},
                    headers={"X-Ingest-Token": "test-ingest"})
    assert r.status_code == 400


def test_sites_sync_upserts_and_remaps(client, monkeypatch):
    import klemma.api.routes.meetings as mroutes

    monkeypatch.setattr(mroutes, "_fetch_json", lambda url: SYNC_PAYLOAD)
    r = client.post("/meetings/sites/sync", json={"url": "https://example.test/hook"},
                    headers={"X-Ingest-Token": "test-ingest"})
    assert r.status_code == 200
    data = r.json()
    assert data["sites"] == 1
    # Both fixture meetings stay mapped (oms_tlt row is untouched by the sync)
    assert data["mapped"] == 2
    assert data["unmapped"] == 0
    assert data["distribution"] == {"oms_chel": 1, "oms_tlt": 1}
    # The updated display name is now served on /meetings
    m = client.get("/meetings", params={"site": "oms_chel"}).json()["meetings"][0]
    assert m["site"] == "ОМС Челябинск (обновл.)"


def test_sites_sync_fetch_failure_502(client, monkeypatch):
    import requests

    def boom(*args, **kwargs):
        raise requests.ConnectionError("no route to host")

    monkeypatch.setattr(requests, "get", boom)
    r = client.post("/meetings/sites/sync", json={"url": "https://example.test/hook"},
                    headers={"X-Ingest-Token": "test-ingest"})
    assert r.status_code == 502
