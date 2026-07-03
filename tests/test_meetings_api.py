"""API tests for the meeting-analytics portal routes.

Offline: the Layer-A bridge is monkeypatched to a temp StateManager and a None
embeddings/AI provider, so no network/Ollama is needed. Auth is bypassed via a
dependency override.
"""

from types import SimpleNamespace

import pytest

from klemma.meetings import import_meeting, parse_protocol
from klemma.state import StateManager

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

M1 = """\
---
date: 2026-06-24
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

M2 = """\
---
date: 2026-06-22
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
    # Seed a temp meeting DB (no embeddings → no network)
    state = StateManager(str(tmp_path / "data" / "klemma.db"))
    import_meeting(state, None, parse_protocol(M1), "m1")
    import_meeting(state, None, parse_protocol(M2), "m2")

    # Boot the app fully offline
    monkeypatch.setenv("KLEMMA_EMBEDDINGS_ALLOW_REMOTE", "1")
    monkeypatch.setenv("KLEMMA_DATA_DIR", str(tmp_path / "saas"))
    monkeypatch.setenv("KLEMMA_BONUM_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setenv("KLEMMA_JWT_SECRET", "test-secret")
    monkeypatch.setenv("KLEMMA_BONUM_INGEST_TOKEN", "test-ingest")

    import klemma.api.routes.meetings as mroutes

    monkeypatch.setattr(mroutes, "build_state_and_embeddings", lambda *a, **k: (state, None))
    monkeypatch.setattr(mroutes, "build_ai", lambda *a, **k: (None, "test-model"))

    from klemma.api.app import create_app
    from klemma.api.auth.deps import get_current_user

    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(user_id="u1")

    with TestClient(app) as c:
        yield c


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


def test_get_meeting_404(client):
    assert client.get("/meetings/nope").status_code == 404


def test_requires_query_min_length(client):
    assert client.get("/meetings/search", params={"q": "a"}).status_code == 422
