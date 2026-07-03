"""Meeting-analytics portal endpoints (Bonum B2B MVP).

Backed by Layer A (``StateManager`` at ``KLEMMA_BONUM_PROJECT_ROOT``) via the
bridge in ``klemma.meetings`` — NOT the SaaS stores. Single-tenant demo: every
authenticated user sees the one configured meeting project, so routes are flat
(``/meetings``) and ignore ``project_id`` (which scopes only the portal chrome).
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
    ingest_meeting,
    list_meetings,
    search_meetings,
)
from klemma.models import UserRecord

from ..auth.deps import get_current_user

router = APIRouter()

_AI_CACHE: dict = {}


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


class AskRequest(BaseModel):
    query: str


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


@router.get("")
async def get_meetings(user: UserRecord = Depends(get_current_user)) -> dict:
    """List meeting protocols + headline stats (Совещания screen)."""
    state, _ = _state_emb()
    return list_meetings(state)


@router.get("/search")
async def get_search(
    q: str = Query(..., min_length=2),
    user: UserRecord = Depends(get_current_user),
) -> dict:
    """Semantic search across meeting fragments (Поиск screen)."""
    state, emb = _state_emb()
    return search_meetings(state, emb, q)


@router.get("/tasks")
async def get_tasks(user: UserRecord = Depends(get_current_user)) -> dict:
    """Aggregate task board: themes, overdue, escalations (Задачи screen)."""
    state, _ = _state_emb()
    return aggregate_tasks(state)


@router.post("/ask")
async def post_ask(
    body: AskRequest,
    user: UserRecord = Depends(get_current_user),
) -> dict:
    """RAG Q&A over the whole meeting history with cited sources (Вопрос screen)."""
    state, emb = _state_emb()
    ai, model = _ai()
    return answer_question(state, emb, ai, model, body.query)


@router.post("/ingest")
async def post_ingest(
    body: IngestRequest,
    x_ingest_token: str | None = Header(default=None, alias="X-Ingest-Token"),
) -> dict:
    """Continuous ingest from the Nodul webhook. Authed by a shared token
    (not JWT — the webhook has no user session). Idempotent by meeting_id."""
    expected = os.getenv("KLEMMA_BONUM_INGEST_TOKEN", "")
    if not expected or x_ingest_token != expected:
        raise HTTPException(status_code=401, detail="invalid ingest token")
    state, emb = _state_emb()
    return ingest_meeting(state, emb, body.model_dump())


@router.get("/{meeting_id}")
async def get_meeting(
    meeting_id: str,
    user: UserRecord = Depends(get_current_user),
) -> dict:
    """Single meeting detail."""
    state, _ = _state_emb()
    payload = list_meetings(state)
    for m in payload["meetings"]:
        if m["id"] == meeting_id:
            return m
    raise HTTPException(status_code=404, detail="Meeting not found")
