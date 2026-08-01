"""Cross-meeting analytics for the Bonum portal (metrics + LLM topic reports).

Same layering rule as ``meetings.py``: no CLI/FastAPI imports. ``compute_metrics``
is pure Python over ``meeting_meta`` + ``fragments``; ``generate_analytics`` adds
the LLM layer (summary/topics/kpis/patterns) with a per-(site, days, day) cache
in ``portal_analytics`` so the expensive call runs at most once per day per view.
"""

from __future__ import annotations

import json
import threading
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from .meetings import _loads, _meeting_meta_map, _prompts_dir
from .meetings_sites import ensure_portal_tables, site_display_names

# Serialize report generation: two concurrent requests for the same uncached
# view must not both pay for the LLM call — the second waits and hits the cache.
_GENERATE_LOCK = threading.Lock()

_CACHE_RETENTION_DAYS = 14

# Russian month abbreviations in genitive form for week labels ("1–7 июн").
_MONTHS_RU = ["янв", "фев", "мар", "апр", "мая", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"]

_TOPIC_STATUSES = {"developing", "stalled", "resolved", "recurring_problem"}

# Returned as ``detail`` when no report was ever generated for (site, days) —
# the frontend turns it into a "Сформировать отчёт" call-to-action. Page loads
# never trigger the LLM; only refresh=1 does.
DETAIL_NOT_GENERATED = "Отчёт ещё не сформирован — нажмите «Обновить»"

# Explicit refresh within this window returns the just-generated report instead
# of paying for a second identical LLM call (double-click / concurrent users).
_REFRESH_DEBOUNCE_SECONDS = 60
_KPI_TRENDS = {"improving", "degrading", "flat", "unclear"}
_SEVERITIES = {"high", "medium", "low"}

_FALLBACK_PROMPT = (
    "Ты — управленческий аналитик. По дайджестам совещаний площадки "
    "«{{ site_name }}» за период {{ period_label }} сформируй СТРОГО JSON с ключами "
    "summary, topics, kpis, patterns. Используй только факты из дайджестов, ничего "
    "не выдумывай.\n{{ metrics_summary }}\n{{ digest }}"
)


# ── Collection ────────────────────────────────────────────────────────────────


def _parse_date(value) -> Optional[date]:
    try:
        return date.fromisoformat(str(value)[:10])
    except Exception:
        return None


def _collect_meetings(state, sites: Optional[set[str]], days: int) -> list[dict]:
    """Meetings within the window (with their fragments), sorted date-ascending."""
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    out = []
    for sid, meta in _meeting_meta_map(state).items():
        if sites is not None and meta.get("site_slug", "") not in sites:
            continue
        d = _parse_date(meta.get("date"))
        if d is None or d.isoformat() < cutoff:
            continue
        out.append(
            {
                "sid": sid,
                "meta": meta,
                "date": d,
                "frags": state.get_fragments(source_id=sid, limit=100000),
            }
        )
    out.sort(key=lambda m: (m["date"], str(m["meta"].get("time") or "")))
    return out


def _task_entries(frags: list[dict]) -> list[dict]:
    entries = []
    for f in frags:
        if f.get("fragment_type") != "task":
            continue
        h = _loads(f.get("usage_hint"))
        entries.append(
            {
                "action": f.get("fragment_text", ""),
                "assignee": str(h.get("assignee") or "").strip(),
                "status": str(h.get("status") or "new"),
                "overdue": bool(h.get("overdue")),
                "escalation": f.get("citation_intent") == "escalation",
            }
        )
    return entries


# ── Metrics (pure Python, no LLM) ─────────────────────────────────────────────


def _iso_week_key(d: date) -> str:
    year, week, _ = d.isocalendar()
    return f"{year}-W{week:02d}"


def _week_label(monday: date) -> str:
    sunday = monday + timedelta(days=6)
    m1 = _MONTHS_RU[monday.month - 1]
    if monday.month == sunday.month:
        return f"{monday.day}–{sunday.day} {m1}"
    return f"{monday.day} {m1} – {sunday.day} {_MONTHS_RU[sunday.month - 1]}"


def _metrics_from_collected(collected: list[dict], days: int) -> dict:
    today = date.today()
    start = today - timedelta(days=days)
    # Full continuous week range (empty weeks kept — charts need continuity).
    monday = start - timedelta(days=start.weekday())
    weeks: dict[str, dict] = {}
    order: list[str] = []
    while monday <= today:
        key = _iso_week_key(monday)
        weeks[key] = {
            "week": key,
            "label": _week_label(monday),
            "meetings": 0,
            "tasks": 0,
            "escalations": 0,
            "overdue": 0,
        }
        order.append(key)
        monday += timedelta(days=7)

    totals = {"meetings": 0, "tasks": 0, "escalations": 0, "overdue": 0}
    assignees: dict[str, dict] = {}
    for m in collected:
        tasks = _task_entries(m["frags"])
        n_escalations = sum(1 for t in tasks if t["escalation"])
        n_overdue = sum(1 for t in tasks if t["overdue"])
        bucket = weeks.get(_iso_week_key(m["date"]))
        if bucket is not None:
            bucket["meetings"] += 1
            bucket["tasks"] += len(tasks)
            bucket["escalations"] += n_escalations
            bucket["overdue"] += n_overdue
        totals["meetings"] += 1
        totals["tasks"] += len(tasks)
        totals["escalations"] += n_escalations
        totals["overdue"] += n_overdue
        for t in tasks:
            if not t["assignee"]:
                continue
            a = assignees.setdefault(t["assignee"], {"name": t["assignee"], "tasks": 0, "overdue": 0})
            a["tasks"] += 1
            a["overdue"] += 1 if t["overdue"] else 0

    top = sorted(assignees.values(), key=lambda a: (-a["tasks"], -a["overdue"], a["name"]))[:8]
    return {"weeks": [weeks[k] for k in order], "totals": totals, "top_assignees": top}


def compute_metrics(state, *, sites: Optional[set[str]] = None, days: int = 90) -> dict:
    """Weekly activity buckets + totals + top assignees over the window."""
    return _metrics_from_collected(_collect_meetings(state, sites, days), days)


def _metrics_summary_text(metrics: dict) -> str:
    """Compact totals/weekly text embedded into the analytics prompt."""
    t = metrics["totals"]
    lines = [
        f"Всего за период: {t['meetings']} совещаний, {t['tasks']} задач, "
        f"{t['escalations']} эскалаций, {t['overdue']} просрочено.",
        "По неделям (совещания/задачи/эскалации/просрочено):",
    ]
    for w in metrics["weeks"]:
        lines.append(
            f"  {w['label']}: {w['meetings']}/{w['tasks']}/{w['escalations']}/{w['overdue']}"
        )
    if metrics["top_assignees"]:
        lines.append("Топ исполнителей (задач, из них просрочено):")
        for a in metrics["top_assignees"]:
            lines.append(f"  {a['name']}: {a['tasks']}, {a['overdue']}")
    return "\n".join(lines)


# ── Digest for the LLM ────────────────────────────────────────────────────────


def build_digest(
    meetings_meta_sorted: list[tuple[str, dict]],
    fragments_by_source: dict[str, list[dict]],
    *,
    max_chars: int = 90000,
) -> tuple[str, bool]:
    """Compact per-meeting digest for the LLM prompt.

    ``meetings_meta_sorted`` must be date-ascending: when the digest exceeds
    ``max_chars`` the OLDEST meetings are dropped first (recent context wins).
    Returns ``(digest, truncated)``.
    """
    blocks: list[str] = []
    for sid, meta in meetings_meta_sorted:
        # The meeting id rides along in the header so the LLM can cite it as
        # the `source` of every timeline entry (validated in _clean_topics).
        lines = [
            f"[{meta.get('date', '')} | id:{sid}] {meta.get('title', '')} ({meta.get('site', '')})".rstrip()
        ]
        summary = str(meta.get("summary") or "").strip()
        if summary:
            lines.append(f"  Сводка: {summary}")
        decisions = [str(d).strip() for d in (meta.get("decisions") or []) if str(d).strip()]
        if decisions:
            lines.append("  Решения: " + "; ".join(decisions))
        tasks: list[str] = []
        escalations: list[str] = []
        for t in _task_entries(fragments_by_source.get(sid, [])):
            who = t["assignee"] or "без ответственного"
            status = "просрочена" if t["overdue"] else t["status"]
            entry = f"{t['action']} ({who}, {status})"
            (escalations if t["escalation"] else tasks).append(entry)
        if tasks:
            lines.append("  Задачи: " + "; ".join(tasks))
        if escalations:
            lines.append("  Эскалации: " + "; ".join(escalations))
        blocks.append("\n".join(lines))

    truncated = False
    while blocks and sum(len(b) + 2 for b in blocks) > max_chars:
        blocks.pop(0)  # drop the oldest meeting
        truncated = True
    return "\n\n".join(blocks), truncated


# ── LLM output validation (tolerant — missing keys → empty, clamp enums) ──────


def _clean_topics(raw, valid_ids: Optional[set] = None) -> list[dict]:
    out = []
    for t in raw if isinstance(raw, list) else []:
        if not isinstance(t, dict):
            continue
        title = str(t.get("title") or "").strip()
        if not title:
            continue
        status = str(t.get("status") or "").strip()
        timeline = []
        raw_timeline = t.get("timeline")
        for p in raw_timeline if isinstance(raw_timeline, list) else []:
            if isinstance(p, dict) and (p.get("date") or p.get("note")):
                # Traceability with an anti-fabrication gate: keep the source
                # meeting id ONLY if it names a meeting that actually fed the
                # digest — a hallucinated id must not become a clickable link.
                source = str(p.get("source") or "").strip()
                if valid_ids is not None and source not in valid_ids:
                    source = ""
                timeline.append(
                    {
                        "date": str(p.get("date") or ""),
                        "note": str(p.get("note") or ""),
                        "source": source,
                    }
                )
        try:
            n_meetings = int(t.get("meetings") or 0)
        except (TypeError, ValueError):
            n_meetings = 0
        out.append(
            {
                "title": title,
                "status": status if status in _TOPIC_STATUSES else "developing",
                "first_seen": str(t.get("first_seen") or ""),
                "last_seen": str(t.get("last_seen") or ""),
                "meetings": n_meetings,
                "timeline": timeline,
                "insight": str(t.get("insight") or ""),
            }
        )
    return out


def _clean_kpis(raw) -> list[dict]:
    out = []
    for k in raw if isinstance(raw, list) else []:
        if not isinstance(k, dict):
            continue
        name = str(k.get("name") or "").strip()
        if not name:
            continue
        trend = str(k.get("trend") or "").strip()
        out.append(
            {
                "name": name,
                "trend": trend if trend in _KPI_TRENDS else "unclear",
                "evidence": str(k.get("evidence") or ""),
            }
        )
    return out


def _clean_patterns(raw) -> list[dict]:
    out = []
    for p in raw if isinstance(raw, list) else []:
        if not isinstance(p, dict):
            continue
        observation = str(p.get("observation") or "").strip()
        if not observation:
            continue
        severity = str(p.get("severity") or "").strip()
        out.append(
            {
                "observation": observation,
                "recommendation": str(p.get("recommendation") or ""),
                "severity": severity if severity in _SEVERITIES else "medium",
            }
        )
    return out


# ── Cache (portal_analytics) ──────────────────────────────────────────────────


def _cache_get(state, site_slug: str, days: int, date_to: str) -> Optional[dict]:
    with state._conn() as conn:
        row = conn.execute(
            "SELECT report FROM portal_analytics WHERE site_slug=? AND days=? AND date_to=?",
            (site_slug, days, date_to),
        ).fetchone()
    if row is None:
        return None
    try:
        report = json.loads(row[0])
        return report if isinstance(report, dict) else None
    except Exception:
        return None


def _cache_get_latest(state, site_slug: str, days: int) -> Optional[dict]:
    """Latest cached report for (site, days) regardless of generation date.

    Plain page loads serve this — possibly a previous day's snapshot — so a
    load never triggers an LLM call; only the explicit refresh button does.
    """
    with state._conn() as conn:
        row = conn.execute(
            "SELECT report FROM portal_analytics WHERE site_slug=? AND days=? "
            "ORDER BY date_to DESC LIMIT 1",
            (site_slug, days),
        ).fetchone()
    if row is None:
        return None
    try:
        report = json.loads(row[0])
        return report if isinstance(report, dict) else None
    except Exception:
        return None


def _is_fresh(report: dict, *, seconds: int) -> bool:
    """True when the report was generated within the last ``seconds``."""
    try:
        generated = datetime.fromisoformat(str(report.get("generated_at") or ""))
        return (datetime.now(timezone.utc) - generated).total_seconds() < seconds
    except (TypeError, ValueError):
        return False


def _cache_put(state, report: dict) -> None:
    with state._conn() as conn:
        conn.execute(
            """INSERT OR REPLACE INTO portal_analytics
               (site_slug, days, date_to, report, model, generated_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                report["site"],
                report["days"],
                report["window"]["to"],
                json.dumps(report, ensure_ascii=False),
                report.get("model", ""),
                report.get("generated_at", ""),
            ),
        )
        # Housekeeping: stale daily snapshots have no readers after two weeks.
        cutoff = (date.today() - timedelta(days=_CACHE_RETENTION_DAYS)).isoformat()
        conn.execute("DELETE FROM portal_analytics WHERE date_to < ?", (cutoff,))


# ── Report generation ─────────────────────────────────────────────────────────


def _load_analytics_prompt() -> str:
    path = _prompts_dir() / "meeting_analytics.md"
    try:
        return path.read_text(encoding="utf-8")
    except Exception:
        return _FALLBACK_PROMPT


def _build_base_report(state, site_slug: str, days: int, model: str, today: date) -> tuple[dict, list, dict]:
    """Compute the metrics-only report skeleton. Returns (report, collected, display)."""
    sites = None if site_slug == "" else {site_slug}
    collected = _collect_meetings(state, sites, days)
    metrics = _metrics_from_collected(collected, days)
    display = site_display_names(state)
    site_name = "Вся компания" if site_slug == "" else display.get(site_slug, site_slug)
    report: dict = {
        "site": site_slug,
        "site_name": site_name,
        "days": days,
        "window": {"from": (today - timedelta(days=days)).isoformat(), "to": today.isoformat()},
        "meetings_analyzed": metrics["totals"]["meetings"],
        "truncated": False,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "model": model or "",
        "cached": False,
        "metrics": metrics,
        "summary": "",
        "topics": [],
        "kpis": [],
        "patterns": [],
    }
    return report, collected, display


def generate_analytics(
    state,
    ai,
    model: str,
    *,
    site_slug: str,
    days: int,
    refresh: bool = False,
) -> dict:
    """Cross-meeting analytics report per the portal contract.

    ``site_slug=''`` means the whole company (no site filter — routes make sure
    leaders never reach it).

    The LLM runs ONLY on ``refresh=True`` (the explicit «Обновить» button).
    A plain page load (``refresh=False``) never spends AI resources: it returns
    the latest cached report for (site, days) regardless of its date — the
    frontend shows the report's own generated_at — or, when nothing was ever
    generated, a fast metrics-only preview with ``detail=DETAIL_NOT_GENERATED``
    (not cached). On refresh: <3 meetings degrades to metrics-only with a
    Russian ``detail`` and is cached; AI absence/failure degrades to
    metrics-only but is NOT cached, so the next refresh retries.
    """
    ensure_portal_tables(state)
    today = date.today()
    date_to = today.isoformat()

    if not refresh:
        cached = _cache_get_latest(state, site_slug, days)
        if cached is not None:
            cached["cached"] = True
            return cached
        report, _, _ = _build_base_report(state, site_slug, days, model, today)
        report["detail"] = DETAIL_NOT_GENERATED
        return report

    with _GENERATE_LOCK:
        # Double-click / concurrent-refresh guard: if a report for today was
        # generated moments ago (possibly by the request that held this lock),
        # serve it instead of paying for a second identical LLM call.
        cached = _cache_get(state, site_slug, days, date_to)
        if cached is not None and _is_fresh(cached, seconds=_REFRESH_DEBOUNCE_SECONDS):
            cached["cached"] = True
            return cached

        report, collected, display = _build_base_report(state, site_slug, days, model, today)

        if report["meetings_analyzed"] < 3:
            report["detail"] = "Недостаточно данных за период"
            _cache_put(state, report)
            return report

        meta_list: list[tuple[str, dict]] = []
        frags_by_sid: dict[str, list[dict]] = {}
        for entry in collected:
            meta = dict(entry["meta"])
            label = display.get(meta.get("site_slug", ""))
            if label:
                meta["site"] = label
            meta_list.append((entry["sid"], meta))
            frags_by_sid[entry["sid"]] = entry["frags"]
        digest, truncated = build_digest(meta_list, frags_by_sid)
        report["truncated"] = truncated
        valid_ids = {sid for sid, _ in meta_list}

        llm: Optional[dict] = None
        if ai is not None:
            from jinja2.sandbox import SandboxedEnvironment

            window = report["window"]
            system = SandboxedEnvironment().from_string(_load_analytics_prompt()).render(
                site_name=report["site_name"],
                period_label=f"{days} дней: {window['from']} — {window['to']}",
                meetings_count=report["meetings_analyzed"],
                digest=digest,
                metrics_summary=_metrics_summary_text(report["metrics"]),
            )
            try:
                # 16K output budget: an all-company digest yields long topic
                # timelines and a 6K cap was observed to cut the JSON mid-array
                # (finish_reason='length' → unparseable → silently empty report).
                llm = ai.call_json(
                    system=system,
                    user="Сформируй аналитический отчёт строго в формате JSON.",
                    max_tokens=16000,
                )
            except Exception:  # pragma: no cover - network/runtime
                llm = None

        if llm:
            report["summary"] = str(llm.get("summary") or "").strip()
            report["topics"] = _clean_topics(llm.get("topics"), valid_ids)
            report["kpis"] = _clean_kpis(llm.get("kpis"))
            report["patterns"] = _clean_patterns(llm.get("patterns"))

        # A "successful" call that produced no summary AND no topics is a failed
        # generation (truncated/unparseable output), not an empty period — flag
        # it and do NOT cache, so the next request retries instead of pinning an
        # empty report for the rest of the day.
        if not report["summary"] and not report["topics"]:
            report["detail"] = "AI недоступен — только метрики"
            return report

        _cache_put(state, report)
        return report
