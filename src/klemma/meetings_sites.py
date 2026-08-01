"""Portal sites registry, access control, and site-slug resolution (Bonum portal).

Same layering rule as ``meetings.py``: imported by the API routes, the CLI
scripts, and tests, so it must NOT import FastAPI symbols. It must also not
import ``klemma.meetings`` (that module imports this one — keep the dependency
one-directional).

Storage: three portal tables on the *meeting DB only*, created by guarded
``CREATE TABLE IF NOT EXISTS`` — intentionally NOT part of
``StateManager._migrate_schema`` (same policy as the ``meeting_meta`` ALTER in
``meetings.py``), so the global schema chain stays untouched.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Optional

# Tokens that carry no site identity on their own — organisational prefixes,
# report-title boilerplate, and generic org-unit words. Used by the layer-3
# significant-token match in ``resolve_site_slug``.
_SITE_STOPWORDS = {
    "омс", "oms", "ос", "отчет", "отчёт", "стендап", "ежедневный", "ежедневная",
    "директора", "филиал", "команда", "цеха", "цех", "производство", "по",
}

_ROLES = ("director", "leader")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_portal_tables(state) -> None:
    """Create the portal tables if absent (this DB only — guarded, idempotent)."""
    with state._conn() as conn:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS portal_sites (
                slug TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                site_type TEXT DEFAULT 'oms',
                leader TEXT DEFAULT '',
                keywords TEXT DEFAULT '[]',
                enabled INTEGER DEFAULT 1,
                updated_at TEXT)"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS portal_access (
                user_id TEXT PRIMARY KEY,
                role TEXT NOT NULL CHECK(role IN ('director','leader')),
                site_slugs TEXT DEFAULT '[]',
                updated_at TEXT)"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS portal_analytics (
                site_slug TEXT NOT NULL,
                days INTEGER NOT NULL,
                date_to TEXT NOT NULL,
                report TEXT NOT NULL,
                model TEXT DEFAULT '',
                generated_at TEXT,
                PRIMARY KEY (site_slug, days, date_to))"""
        )


# ── Sites registry ────────────────────────────────────────────────────────────


def parse_sites_webhook(payload) -> list[dict]:
    """Extract site ``value`` dicts from a Nodul sites-webhook payload.

    Accepts ``{"result": [...]}`` or a bare list; each entry is either a
    wrapper ``{"collection_name": "sites", "value": {...}}`` (collection_name
    may be missing) or a bare value dict. Returns ``[]`` on garbage.
    """
    if isinstance(payload, dict):
        items = payload.get("result")
    elif isinstance(payload, list):
        items = payload
    else:
        return []
    if not isinstance(items, list):
        return []

    values: list[dict] = []
    for entry in items:
        if not isinstance(entry, dict):
            continue
        coll = entry.get("collection_name")
        if coll is not None and coll != "sites":
            continue
        value = entry.get("value")
        if isinstance(value, dict) and value.get("site_slug"):
            values.append(value)
        elif value is None and entry.get("site_slug"):
            values.append(entry)
    return values


def upsert_sites(state, items: list[dict]) -> int:
    """Upsert webhook ``value`` dicts into ``portal_sites``. Returns row count.

    Only slug/name/type/leader/keywords/enabled are stored — bitrix/gsheet
    fields are dropped (they never belong in this DB).
    """
    ensure_portal_tables(state)
    now = _now_iso()
    n = 0
    with state._conn() as conn:
        for item in items:
            if not isinstance(item, dict):
                continue
            slug = str(item.get("site_slug") or "").strip()
            name = str(item.get("site_name") or "").strip()
            if not slug or not name:
                continue
            keywords = item.get("site_keywords") or []
            if not isinstance(keywords, list):
                keywords = []
            conn.execute(
                """INSERT OR REPLACE INTO portal_sites
                   (slug, name, site_type, leader, keywords, enabled, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    slug,
                    name,
                    str(item.get("site_type") or "oms"),
                    str(item.get("leader") or ""),
                    json.dumps([str(k) for k in keywords], ensure_ascii=False),
                    1 if item.get("enabled", True) else 0,
                    now,
                ),
            )
            n += 1
    return n


def get_sites(state, *, enabled_only: bool = True) -> list[dict]:
    """Return registered sites with ``keywords`` parsed back to a list."""
    ensure_portal_tables(state)
    query = "SELECT slug, name, site_type, leader, keywords, enabled FROM portal_sites"
    if enabled_only:
        query += " WHERE enabled=1"
    query += " ORDER BY name"
    with state._conn() as conn:
        rows = conn.execute(query).fetchall()
    sites = []
    for row in rows:
        try:
            keywords = json.loads(row[4]) if row[4] else []
        except Exception:
            keywords = []
        if not isinstance(keywords, list):
            keywords = []
        sites.append(
            {
                "slug": row[0],
                "name": row[1],
                "site_type": row[2] or "oms",
                "leader": row[3] or "",
                "keywords": [str(k) for k in keywords],
                "enabled": bool(row[5]),
            }
        )
    return sites


def site_display_names(state) -> dict[str, str]:
    """Return ``{slug: display name}`` for all registered sites (incl. disabled)."""
    ensure_portal_tables(state)
    with state._conn() as conn:
        rows = conn.execute("SELECT slug, name FROM portal_sites").fetchall()
    return {row[0]: row[1] for row in rows}


# ── Resolver ──────────────────────────────────────────────────────────────────


def _site_fields(site: dict) -> tuple[str, str, list[str], bool]:
    """Normalize a site dict from either the stored form (slug/name/keywords)
    or the raw webhook form (site_slug/site_name/site_keywords)."""
    slug = str(site.get("slug") or site.get("site_slug") or "")
    name = str(site.get("name") or site.get("site_name") or "")
    keywords = site.get("keywords") or site.get("site_keywords") or []
    if not isinstance(keywords, list):
        keywords = []
    enabled = bool(site.get("enabled", True))
    return slug, name, [str(k) for k in keywords], enabled


def _token_stem(token: str) -> str:
    """Trim a Russian case ending (up to 3 chars) keeping a ≥5-char stem.

    Registry names carry genitive forms ("Аксайского филиала") while meeting
    folders use nominative ("Аксайский филиал") — exact token matching missed
    those. Short tokens (≤5 chars, "траст"/"аксай") are returned unchanged so
    they never loosen into false positives.
    """
    return token[: max(5, len(token) - 3)]


def resolve_site_slug(site: str, title: str, sites: list[dict]) -> str:
    """Resolve a meeting to a site slug over ``f"{site} {title}"`` (lowered).

    Layered, deterministic scoring per site (best layer wins per site):
      * full site name (lowered) is a substring of the text → score 4
      * keyword phrase: every whitespace-split word of the phrase present in
        the text as a substring (keywords are prefixes like "омс ремонтн")
        → score 3, longer phrase breaks ties
      * significant-token overlap: site-name tokens minus stopwords; ≥1
        significant token and ALL of them present, compared by ``_token_stem``
        → score 2
    Highest (score, matched-name length) across sites wins; no match → ``""``.
    Only enabled sites participate.
    """
    text = f"{site or ''} {title or ''}".lower().strip()
    if not text or not sites:
        return ""

    best: tuple[int, int] = (0, 0)
    best_slug = ""
    for entry in sites:
        if not isinstance(entry, dict):
            continue
        slug, name, keywords, enabled = _site_fields(entry)
        if not slug or not enabled:
            continue
        name_low = name.lower().strip()
        candidates: list[tuple[int, int]] = []

        if name_low and name_low in text:
            candidates.append((4, len(name_low)))

        for phrase in keywords:
            words = phrase.lower().split()
            if words and all(w in text for w in words):
                candidates.append((3, len(phrase)))

        if name_low:
            tokens = [t for t in re.split(r"\W+", name_low, flags=re.UNICODE) if t]
            significant = [t for t in tokens if t not in _SITE_STOPWORDS]
            if significant and all(_token_stem(t) in text for t in significant):
                candidates.append((2, len(name_low)))

        if candidates:
            site_best = max(candidates)
            if site_best > best:
                best = site_best
                best_slug = slug
    return best_slug


EXPLICIT = "explicit"
RESOLVED = "resolved"


def _enabled_slugs(sites: list[dict]) -> set[str]:
    """Live slugs from a registry listing (defensive about the disabled flag:
    ``get_sites`` filters by default, but callers may pass ``enabled_only=False``)."""
    known = set()
    for entry in sites:
        if not isinstance(entry, dict):
            continue
        slug, _name, _keywords, enabled = _site_fields(entry)
        if slug and enabled:
            known.add(slug)
    return known


def pick_site_slug(
    explicit: str, site: str, title: str, sites: list[dict]
) -> tuple[str, str]:
    """Choose a meeting's site slug — ``(slug, source)``, source ∈ explicit|resolved.

    Senders that already know the registry id must not have it thrown away and
    re-guessed: the mobile client reads the slug from the same
    ``GET /meetings/sites`` this portal serves, so its value is authoritative in
    a way ``resolve_site_slug`` (substring / keyword / stem scoring over the site
    string plus the title) can never be. A miss there yields ``''``, and an
    unresolved meeting is invisible to every site leader.

    An explicit slug wins ONLY when it names a live enabled site. A typo or a
    retired slug falls back to the resolver rather than creating a meeting that
    no account can reach — silently trusting it would trade one failure mode
    for a worse one.
    """
    if explicit and explicit in _enabled_slugs(sites):
        return explicit, EXPLICIT
    return resolve_site_slug(site, title, sites), RESOLVED


def remap_meeting_sites(state) -> dict:
    """Re-resolve ``site_slug`` for meeting sources against the registry.

    Returns ``{"mapped": n, "unmapped": n, "preserved": n, "distribution":
    {slug_or_"": count}}`` so callers can print the result — silent mis-mapping
    is not acceptable.

    Meetings whose slug came in explicitly (``site_slug_source == "explicit"``,
    see ``pick_site_slug``) are LEFT ALONE while that slug still names a live
    site. Without this a single ``POST /meetings/sites/sync`` would overwrite
    every authoritative slug with a fuzzy guess — the exact loss the explicit
    field exists to prevent. They still count as ``mapped`` (so
    ``mapped + unmapped`` stays the total) and are reported separately as
    ``preserved``: silently kept is as unacceptable as silently re-mapped.
    """
    ensure_portal_tables(state)
    sites = get_sites(state)
    known = _enabled_slugs(sites)
    distribution: dict[str, int] = {}
    mapped = unmapped = preserved = 0
    with state._conn() as conn:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(sources)")}
        if "meeting_meta" not in cols:
            return {"mapped": 0, "unmapped": 0, "distribution": {}}
        rows = conn.execute(
            "SELECT id, meeting_meta FROM sources WHERE source_type='meeting'"
        ).fetchall()
        for sid, blob in rows:
            try:
                meta = json.loads(blob) if blob else {}
            except Exception:
                meta = {}
            if not isinstance(meta, dict):
                meta = {}
            current = str(meta.get("site_slug") or "")
            if meta.get("site_slug_source") == EXPLICIT and current in known:
                # Авторитетный слаг живой площадки — не трогаем и не переписываем строку.
                distribution[current] = distribution.get(current, 0) + 1
                mapped += 1
                preserved += 1
                continue
            slug = resolve_site_slug(
                str(meta.get("site") or ""), str(meta.get("title") or ""), sites
            )
            meta["site_slug"] = slug
            meta["site_slug_source"] = RESOLVED
            conn.execute(
                "UPDATE sources SET meeting_meta=? WHERE id=?",
                (json.dumps(meta, ensure_ascii=False), sid),
            )
            distribution[slug] = distribution.get(slug, 0) + 1
            if slug:
                mapped += 1
            else:
                unmapped += 1
    return {
        "mapped": mapped,
        "unmapped": unmapped,
        "preserved": preserved,
        "distribution": distribution,
    }


# ── Access control ────────────────────────────────────────────────────────────


def get_access(state, user_id: str) -> dict:
    """Return ``{"role": ..., "site_slugs": [...]}`` for a portal user.

    No row → director with full view (backward compat: users existing before
    the access table keep seeing everything).
    """
    ensure_portal_tables(state)
    with state._conn() as conn:
        row = conn.execute(
            "SELECT role, site_slugs FROM portal_access WHERE user_id=?", (user_id,)
        ).fetchone()
    if row is None:
        return {"role": "director", "site_slugs": []}
    try:
        slugs = json.loads(row[1]) if row[1] else []
    except Exception:
        slugs = []
    if not isinstance(slugs, list):
        slugs = []
    role = row[0] if row[0] in _ROLES else "director"
    return {"role": role, "site_slugs": [str(s) for s in slugs]}


def set_access(state, user_id: str, role: str, site_slugs: list[str]) -> None:
    """Upsert a portal access row. ``site_slugs`` is ignored for directors."""
    if role not in _ROLES:
        raise ValueError(f"role must be one of {_ROLES}, got {role!r}")
    ensure_portal_tables(state)
    with state._conn() as conn:
        conn.execute(
            """INSERT OR REPLACE INTO portal_access (user_id, role, site_slugs, updated_at)
               VALUES (?, ?, ?, ?)""",
            (
                user_id,
                role,
                json.dumps([str(s) for s in site_slugs or []], ensure_ascii=False),
                _now_iso(),
            ),
        )


def allowed_slugs(state, user_id: str) -> Optional[set[str]]:
    """Site filter for a user: ``None`` = all (director), else the leader's set."""
    access = get_access(state, user_id)
    if access["role"] == "director":
        return None
    return set(access["site_slugs"])
