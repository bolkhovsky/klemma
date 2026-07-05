"""Tests for cross-meeting analytics: metrics, digest, report generation/cache.

Offline: meetings are written straight through ``import_meeting`` (no
embeddings), AI is a counting stub — no network anywhere.
"""

from datetime import date, timedelta

from klemma.meetings import ParsedMeeting, ParsedTask, import_meeting
from klemma.meetings_analytics import (
    build_digest,
    compute_metrics,
    generate_analytics,
)
from klemma.meetings_sites import upsert_sites
from klemma.state import StateManager

SITE = {"site_slug": "oms_alfa", "site_name": "ОМС Альфа",
        "site_keywords": ["омс альфа"], "enabled": True}


class StubAI:
    """Counting AI stub returning a fixed call_json payload (or raising)."""

    def __init__(self, payload=None, raise_error=False):
        self.payload = payload
        self.raise_error = raise_error
        self.calls = 0

    def call_json(self, system, user, max_tokens=8192, temperature=0.2, **kwargs):
        self.calls += 1
        if self.raise_error:
            raise RuntimeError("boom")
        return self.payload


LLM_PAYLOAD = {
    "summary": "Главный риск — брак литья.",
    "topics": [
        {"title": "Брак литья", "status": "recurring_problem",
         "first_seen": "2026-06-01", "last_seen": "2026-06-20", "meetings": 3,
         "timeline": [{"date": "2026-06-01", "note": "впервые поднята"}],
         "insight": "Тема без решения."},
        {"title": "Сроки ремонта", "status": "весьма странный",  # → clamped
         "timeline": "not-a-list", "meetings": "many"},
        {"status": "resolved"},  # no title → dropped
    ],
    "kpis": [
        {"name": "Дебиторка", "trend": "improving", "evidence": "12.4 → 9.8 млн"},
        {"name": "План", "trend": "вверх"},  # → unclear
        {"trend": "flat"},  # no name → dropped
    ],
    "patterns": [
        {"observation": "Просрочки у одного исполнителя",
         "recommendation": "Перераспределить задачи", "severity": "high"},
        {"observation": "Задачи без ответственных", "severity": "критично"},  # → medium
    ],
}


def _state(tmp_path) -> StateManager:
    state = StateManager(str(tmp_path / "data" / "klemma.db"))
    upsert_sites(state, [SITE])
    return state


def _mk_meeting(state, *, days_ago: int, title="ОМС Альфа", site="Альфа",
                tasks=(), decisions=(), summary="Обсудили статус производства."):
    date_str = (date.today() - timedelta(days=days_ago)).isoformat()
    pm = ParsedMeeting(
        title=title,
        summary=summary,
        decisions=list(decisions),
        tasks=[
            ParsedTask(action=action, assignee=who, deadline=deadline, overdue=overdue)
            for (action, who, deadline, overdue) in tasks
        ],
        meta={"date": date_str, "site": site, "type": "ОМС", "time": "09:00",
              "speakers": []},
    )
    return import_meeting(state, None, pm, f"{date_str}-{days_ago}-{title}")


# ── compute_metrics ───────────────────────────────────────────────────────────


def test_compute_metrics_weekly_buckets_and_totals(tmp_path):
    state = _state(tmp_path)
    _mk_meeting(state, days_ago=3, tasks=[
        ("Задача 1", "Иванов", "просроч. вчера", True),
        ("Задача 2", "Петров", "завтра", False),
    ])
    _mk_meeting(state, days_ago=10, tasks=[("Задача 3", "Иванов", "завтра", False)])
    _mk_meeting(state, days_ago=40, tasks=[("Старая задача", "Сидоров", "", False)])
    _mk_meeting(state, days_ago=3, site="Марс", title="Планёрка")  # unresolved

    m = compute_metrics(state, sites={"oms_alfa"}, days=30)
    assert m["totals"] == {"meetings": 2, "tasks": 3, "escalations": 1, "overdue": 1}
    # Full continuous window of weeks (30 days ≈ 5-6 ISO weeks), zeros included
    assert len(m["weeks"]) >= 5
    assert sum(w["meetings"] for w in m["weeks"]) == 2
    assert any(w["meetings"] == 0 for w in m["weeks"])
    for w in m["weeks"]:
        assert set(w) == {"week", "label", "meetings", "tasks", "escalations", "overdue"}
        assert w["week"].count("-W") == 1
        # Russian month abbreviation present in the label
        assert any(mon in w["label"] for mon in
                   ["янв", "фев", "мар", "апр", "мая", "июн",
                    "июл", "авг", "сен", "окт", "ноя", "дек"])
    # top_assignees ranked by task count with overdue counts
    assert m["top_assignees"][0] == {"name": "Иванов", "tasks": 2, "overdue": 1}


def test_compute_metrics_no_sites_filter_counts_all(tmp_path):
    state = _state(tmp_path)
    _mk_meeting(state, days_ago=1)
    _mk_meeting(state, days_ago=2, site="Марс", title="Планёрка")
    m = compute_metrics(state, sites=None, days=30)
    assert m["totals"]["meetings"] == 2


# ── build_digest ──────────────────────────────────────────────────────────────


def _digest_inputs(n: int):
    metas = []
    frags = {}
    for i in range(n):
        sid = f"mtg-{i}"
        metas.append((sid, {
            "date": f"2026-06-{i + 1:02d}", "title": f"Совещание {i}",
            "site": "ОМС Альфа", "summary": "Сводка " + "х" * 50,
            "decisions": [f"Решение {i}"],
        }))
        frags[sid] = [
            {"fragment_type": "task", "fragment_text": f"Задача {i}",
             "usage_hint": '{"assignee": "Иванов", "overdue": false, "status": "new"}',
             "citation_intent": "new"},
            {"fragment_type": "task", "fragment_text": f"Эскалация {i}",
             "usage_hint": '{"assignee": "", "overdue": true, "status": "new"}',
             "citation_intent": "escalation"},
            {"fragment_type": "summary", "fragment_text": "не задача",
             "usage_hint": "{}", "citation_intent": "summary"},
        ]
    return metas, frags


def test_build_digest_format():
    metas, frags = _digest_inputs(2)
    digest, truncated = build_digest(metas, frags)
    assert not truncated
    # Meeting id rides in the header — the LLM cites it as timeline `source`.
    assert "[2026-06-01 | id:mtg-0] Совещание 0 (ОМС Альфа)" in digest
    assert "Сводка:" in digest and "Решения: Решение 0" in digest
    assert "Задачи: Задача 0 (Иванов, new)" in digest
    assert "Эскалации: Эскалация 0 (без ответственного, просрочена)" in digest


def test_build_digest_skips_empty_parts():
    digest, _ = build_digest(
        [("m1", {"date": "2026-06-01", "title": "Пустое", "site": "", "summary": "",
                 "decisions": []})],
        {"m1": []},
    )
    assert "Сводка:" not in digest
    assert "Решения:" not in digest
    assert "Задачи:" not in digest


def test_build_digest_truncates_oldest_first():
    metas, frags = _digest_inputs(6)
    full, _ = build_digest(metas, frags)
    digest, truncated = build_digest(metas, frags, max_chars=len(full) // 2)
    assert truncated
    assert "Совещание 5" in digest  # newest kept
    assert "Совещание 0" not in digest  # oldest dropped


# ── generate_analytics ────────────────────────────────────────────────────────


def _seed_three(state):
    _mk_meeting(state, days_ago=20, tasks=[("Задача А", "Иванов", "просроч. вчера", True)])
    _mk_meeting(state, days_ago=10, tasks=[("Задача Б", "Петров", "завтра", False)])
    _mk_meeting(state, days_ago=2, decisions=["Закрыть тему брака"])


def test_generate_analytics_report_shape(tmp_path):
    state = _state(tmp_path)
    _seed_three(state)
    ai = StubAI(LLM_PAYLOAD)
    r = generate_analytics(state, ai, "stub-model", site_slug="oms_alfa", days=90,
                           refresh=True)
    assert ai.calls == 1
    assert r["site"] == "oms_alfa"
    assert r["site_name"] == "ОМС Альфа"
    assert r["days"] == 90
    assert r["meetings_analyzed"] == 3
    assert r["cached"] is False
    assert r["model"] == "stub-model"
    assert r["window"]["to"] == date.today().isoformat()
    assert "detail" not in r
    assert r["summary"] == "Главный риск — брак литья."
    # Tolerant validation: clamped enums, dropped title-less entries
    assert [t["status"] for t in r["topics"]] == ["recurring_problem", "developing"]
    assert r["topics"][1]["timeline"] == []
    assert r["topics"][1]["meetings"] == 0
    assert [k["trend"] for k in r["kpis"]] == ["improving", "unclear"]
    assert [p["severity"] for p in r["patterns"]] == ["high", "medium"]
    assert r["metrics"]["totals"]["meetings"] == 3


def test_generate_analytics_caches(tmp_path, monkeypatch):
    import klemma.meetings_analytics as ma

    state = _state(tmp_path)
    _seed_three(state)
    ai = StubAI(LLM_PAYLOAD)
    r1 = generate_analytics(state, ai, "stub-model", site_slug="oms_alfa", days=90,
                            refresh=True)
    r2 = generate_analytics(state, ai, "stub-model", site_slug="oms_alfa", days=90)
    assert ai.calls == 1  # plain load served from cache — no AI call
    assert r1["cached"] is False and r2["cached"] is True
    assert r2["summary"] == r1["summary"]
    # refresh=True regenerates (debounce disabled for the test)
    monkeypatch.setattr(ma, "_REFRESH_DEBOUNCE_SECONDS", 0)
    r3 = generate_analytics(state, ai, "stub-model", site_slug="oms_alfa", days=90,
                            refresh=True)
    assert ai.calls == 2
    assert r3["cached"] is False


def test_generate_analytics_refresh_debounced(tmp_path):
    # A second explicit refresh right after generation returns the fresh cache
    # instead of paying for an identical LLM call (double-click guard).
    state = _state(tmp_path)
    _seed_three(state)
    ai = StubAI(LLM_PAYLOAD)
    generate_analytics(state, ai, "stub-model", site_slug="oms_alfa", days=90,
                       refresh=True)
    r2 = generate_analytics(state, ai, "stub-model", site_slug="oms_alfa", days=90,
                            refresh=True)
    assert ai.calls == 1
    assert r2["cached"] is True


def test_generate_analytics_plain_load_never_calls_llm(tmp_path):
    from klemma.meetings_analytics import DETAIL_NOT_GENERATED

    state = _state(tmp_path)
    _seed_three(state)
    ai = StubAI(LLM_PAYLOAD)
    r = generate_analytics(state, ai, "stub-model", site_slug="oms_alfa", days=90)
    assert ai.calls == 0
    assert r["detail"] == DETAIL_NOT_GENERATED
    assert r["metrics"]["totals"]["meetings"] == 3  # metrics preview still there
    # The preview is NOT cached — the next load recomputes it, still without AI
    r2 = generate_analytics(state, ai, "stub-model", site_slug="oms_alfa", days=90)
    assert ai.calls == 0 and r2["cached"] is False


def test_generate_analytics_serves_stale_snapshot_on_load(tmp_path):
    # Yesterday's report keeps serving page loads today — no daily auto-regen.
    state = _state(tmp_path)
    _seed_three(state)
    ai = StubAI(LLM_PAYLOAD)
    generate_analytics(state, ai, "stub-model", site_slug="oms_alfa", days=90,
                       refresh=True)
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    with state._conn() as conn:
        conn.execute("UPDATE portal_analytics SET date_to=?", (yesterday,))
    r = generate_analytics(state, ai, "stub-model", site_slug="oms_alfa", days=90)
    assert ai.calls == 1  # served from the stale snapshot
    assert r["cached"] is True
    assert r["summary"] == "Главный риск — брак литья."


def test_generate_analytics_timeline_source_validated(tmp_path):
    state = _state(tmp_path)
    real_sid = _mk_meeting(state, days_ago=20)["source_id"]
    _mk_meeting(state, days_ago=10)
    _mk_meeting(state, days_ago=2)
    payload = {
        "summary": "Ок.",
        "topics": [{
            "title": "Брак литья", "status": "developing",
            "timeline": [
                {"date": "2026-06-01", "note": "реальный источник", "source": real_sid},
                {"date": "2026-06-05", "note": "выдуманный источник", "source": "mtg-fake-42"},
                {"date": "2026-06-07", "note": "без источника"},
            ],
        }],
        "kpis": [], "patterns": [],
    }
    r = generate_analytics(state, StubAI(payload), "stub-model",
                           site_slug="oms_alfa", days=90, refresh=True)
    sources = [p["source"] for p in r["topics"][0]["timeline"]]
    assert sources == [real_sid, "", ""]  # hallucinated id stripped, absent → ''


def test_generate_analytics_ai_failure_falls_back_to_metrics(tmp_path):
    state = _state(tmp_path)
    _seed_three(state)
    ai = StubAI(raise_error=True)
    r = generate_analytics(state, ai, "stub-model", site_slug="oms_alfa", days=90,
                           refresh=True)
    assert r["detail"] == "AI недоступен — только метрики"
    assert r["summary"] == "" and r["topics"] == [] and r["kpis"] == []
    assert r["metrics"]["totals"]["meetings"] == 3


def test_generate_analytics_ai_none(tmp_path):
    state = _state(tmp_path)
    _seed_three(state)
    r = generate_analytics(state, None, "", site_slug="oms_alfa", days=90,
                           refresh=True)
    assert r["detail"] == "AI недоступен — только метрики"


def test_generate_analytics_too_few_meetings_skips_llm(tmp_path):
    state = _state(tmp_path)
    _mk_meeting(state, days_ago=5)
    _mk_meeting(state, days_ago=3)
    ai = StubAI(LLM_PAYLOAD)
    r = generate_analytics(state, ai, "stub-model", site_slug="oms_alfa", days=90,
                           refresh=True)
    assert ai.calls == 0
    assert r["detail"] == "Недостаточно данных за период"
    assert r["meetings_analyzed"] == 2
    # ...and the metrics-only report is cached too
    r2 = generate_analytics(state, ai, "stub-model", site_slug="oms_alfa", days=90)
    assert r2["cached"] is True and ai.calls == 0


def test_generate_analytics_whole_company(tmp_path):
    state = _state(tmp_path)
    _seed_three(state)
    _mk_meeting(state, days_ago=4, site="Марс", title="Планёрка")  # unresolved slug
    r = generate_analytics(state, None, "", site_slug="", days=90, refresh=True)
    assert r["site_name"] == "Вся компания"
    # No site filter → unresolved meetings included
    assert r["meetings_analyzed"] == 4
