"""Meeting-analytics portal endpoints (Bonum B2B MVP).

Backed by Layer A (``StateManager`` at ``KLEMMA_BONUM_PROJECT_ROOT``) via the
bridge in ``klemma.meetings`` — NOT the SaaS stores. Single-tenant demo: every
authenticated user sees the one configured meeting project, so routes are flat
(``/meetings``) and ignore ``project_id`` (which scopes only the portal chrome).

Per-user scoping happens via ``portal_access`` (see ``klemma.meetings_sites``):
no row → director (full view), leaders see only their ``site_slugs``.
"""

from __future__ import annotations

import os

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from pydantic import BaseModel

from klemma.meetings import (
    BONUM_ROOT_ENV,
    aggregate_tasks,
    answer_question,
    build_ai,
    build_state_and_embeddings,
    count_meetings_by_site,
    ingest_meeting,
    list_meetings,
    search_meetings,
)
from klemma.meetings_analytics import generate_analytics
from klemma.meetings_sites import (
    get_access,
    get_sites,
    parse_sites_webhook,
    remap_meeting_sites,
    upsert_sites,
)
from klemma.models import UserRecord

from ..auth.deps import get_current_user

router = APIRouter()

_AI_CACHE: dict = {}

# Allowed day windows: out-of-range values are clamped to the nearest one so a
# hand-crafted query can never widen the window beyond what the UI offers.
_LIST_DAYS = (7, 14, 30, 90, 180)
_ANALYTICS_DAYS = (30, 90, 180)


def _root() -> str:
    root = os.getenv(BONUM_ROOT_ENV)
    if not root:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"{BONUM_ROOT_ENV} is not set — meeting portal not configured",
        )
    return root


def _state_emb():
    return build_state_and_embeddings(_root(), use_cache=True)


def _ai():
    root = _root()
    if root not in _AI_CACHE:
        _AI_CACHE[root] = build_ai(root)
    return _AI_CACHE[root]


def _clamp_days(days: int | None, allowed: tuple[int, ...]) -> int | None:
    if days is None:
        return None
    return min(allowed, key=lambda a: (abs(a - days), a))


def _scope(state, user, site: str | None) -> set[str] | None:
    """Resolve the sites filter for the current user.

    Director: no site → None (everything, incl. unresolved); site → {site}.
    Leader: no site → their slugs; site in slugs → {site}; otherwise 403.
    """
    access = get_access(state, user.user_id)
    if access["role"] == "director":
        return {site} if site else None
    slugs = set(access["site_slugs"])
    if not site:
        return slugs
    if site in slugs:
        return {site}
    raise HTTPException(status_code=403, detail="site not allowed for this account")


def _check_ingest_token(x_ingest_token: str | None) -> None:
    expected = os.getenv("KLEMMA_BONUM_INGEST_TOKEN", "")
    if not expected or x_ingest_token != expected:
        raise HTTPException(status_code=401, detail="invalid ingest token")


def _fetch_json(url: str) -> object:
    """GET the sites webhook and parse JSON (10s budget; 502 on any failure).

    Uses ``requests`` — the repo's pinned core HTTP dependency (same client the
    Bitrix backfill script uses), available in both the container and bare CLI.
    """
    import requests

    try:
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        raise HTTPException(
            status_code=502, detail=f"sites webhook fetch failed: {e}"
        ) from e


class AskRequest(BaseModel):
    query: str
    site: str | None = None


class SitesSyncRequest(BaseModel):
    url: str | None = None


class IngestRequest(BaseModel):
    meeting_id: str
    date: str = ""
    type: str = ""
    site: str = ""
    time: str = ""
    duration: int | None = None
    speakers: list[str] = []
    protocol_md: str = ""
    tasks: list[dict] = []
    title: str = ""


# NOTE: every fixed-path route below must stay declared BEFORE the catch-all
# ``GET /{meeting_id}`` at the bottom — FastAPI matches in declaration order.


@router.get("")
async def get_meetings(
    site: str | None = Query(default=None),
    days: int | None = Query(default=None),
    user: UserRecord = Depends(get_current_user),
) -> dict:
    """List meeting protocols + headline stats (Совещания screen)."""
    state, _ = _state_emb()
    sites = _scope(state, user, site)
    return list_meetings(state, sites=sites, days=_clamp_days(days, _LIST_DAYS))


@router.get("/sites")
async def get_sites_registry(user: UserRecord = Depends(get_current_user)) -> dict:
    """Sites the current user may see, with 90-day meeting counts."""
    state, _ = _state_emb()
    access = get_access(state, user.user_id)
    role = access["role"]
    allowed = None if role == "director" else set(access["site_slugs"])
    counts = count_meetings_by_site(state, days=90)
    items = []
    for s in get_sites(state):
        if allowed is not None and s["slug"] not in allowed:
            continue
        items.append(
            {
                "slug": s["slug"],
                "name": s["name"],
                "type": s["site_type"],
                "leader": s["leader"],
                "meetings": counts.get(s["slug"], 0),
            }
        )
    items.sort(key=lambda x: (-x["meetings"], x["name"]))
    return {"role": role, "can_view_all": role == "director", "sites": items}


@router.post("/sites/sync")
async def post_sites_sync(
    body: SitesSyncRequest | None = None,
    x_ingest_token: str | None = Header(default=None, alias="X-Ingest-Token"),
) -> dict:
    """Refresh the sites registry from the Nodul webhook + remap all meetings.

    Authed by the shared ingest token (webhook-to-webhook, no user session)."""
    _check_ingest_token(x_ingest_token)
    url = (body.url if body else None) or os.getenv("KLEMMA_BONUM_SITES_WEBHOOK", "")
    if not url:
        raise HTTPException(status_code=400, detail="no sites webhook url configured")
    items = parse_sites_webhook(_fetch_json(url))
    state, _ = _state_emb()
    n = upsert_sites(state, items)
    remap = remap_meeting_sites(state)
    return {"sites": n, **remap}


@router.get("/search")
async def get_search(
    q: str = Query(..., min_length=2),
    site: str | None = Query(default=None),
    user: UserRecord = Depends(get_current_user),
) -> dict:
    """Semantic search across meeting fragments (Поиск screen)."""
    state, emb = _state_emb()
    sites = _scope(state, user, site)
    return search_meetings(state, emb, q, sites=sites)


@router.get("/tasks")
async def get_tasks(
    site: str | None = Query(default=None),
    days: int | None = Query(default=None),
    user: UserRecord = Depends(get_current_user),
) -> dict:
    """Aggregate task board: themes, overdue, escalations (Задачи screen)."""
    state, _ = _state_emb()
    sites = _scope(state, user, site)
    return aggregate_tasks(state, sites=sites, days=_clamp_days(days, _LIST_DAYS))


@router.get("/analytics")
async def get_analytics(
    site: str = Query(default=""),
    days: int = Query(default=90),
    refresh: int = Query(default=0),
    user: UserRecord = Depends(get_current_user),
) -> dict:
    """Cross-meeting analytics report (Аналитика screen). Synchronous — an
    uncached report costs one LLM call (may take 30–60s)."""
    state, _ = _state_emb()
    access = get_access(state, user.user_id)
    if access["role"] == "director":
        site_slug = site  # '' = вся компания
    else:
        slugs = access["site_slugs"]
        if site:
            if site not in slugs:
                raise HTTPException(status_code=403, detail="site not allowed for this account")
            site_slug = site
        elif len(slugs) == 1:
            site_slug = slugs[0]
        else:
            raise HTTPException(
                status_code=403, detail="site is required for leader accounts"
            )
    ai, model = _ai()
    return generate_analytics(
        state,
        ai,
        model,
        site_slug=site_slug,
        days=_clamp_days(days, _ANALYTICS_DAYS),
        refresh=bool(refresh),
    )


@router.post("/ask")
async def post_ask(
    body: AskRequest,
    user: UserRecord = Depends(get_current_user),
) -> dict:
    """RAG Q&A over the meeting history with cited sources (Вопрос screen)."""
    state, emb = _state_emb()
    sites = _scope(state, user, body.site)
    ai, model = _ai()
    return answer_question(state, emb, ai, model, body.query, sites=sites)


@router.post("/ingest")
async def post_ingest(
    body: IngestRequest,
    x_ingest_token: str | None = Header(default=None, alias="X-Ingest-Token"),
) -> dict:
    """Continuous ingest from the Nodul webhook. Authed by a shared token
    (not JWT — the webhook has no user session). Idempotent by meeting_id."""
    _check_ingest_token(x_ingest_token)
    state, emb = _state_emb()
    return ingest_meeting(state, emb, body.model_dump())


@router.get("/{meeting_id}")
async def get_meeting(
    meeting_id: str,
    user: UserRecord = Depends(get_current_user),
) -> dict:
    """Single meeting detail (scoped to the user's allowed sites)."""
    state, _ = _state_emb()
    sites = _scope(state, user, None)
    payload = list_meetings(state, sites=sites)
    for m in payload["meetings"]:
        if m["id"] == meeting_id:
            return m
    raise HTTPException(status_code=404, detail="Meeting not found")
