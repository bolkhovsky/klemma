"""Pydantic schemas for sync payloads."""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


class SourcePayload(BaseModel):
    citekey: str
    paper_id: str = ""
    title: str = ""
    authors: str = ""
    year: Optional[int] = None
    doi: Optional[str] = None
    abstract: str = ""
    sections: list[str] = []
    status: str = "pending"


class FragmentPayload(BaseModel):
    fragment_id: str
    paper_id: str
    text: str
    fragment_type: str = "key_idea"
    citation_intent: Optional[str] = None
    page: Optional[int] = None


class EmbeddingPayload(BaseModel):
    id: str
    vector_b64: str
    model: str = "specter2"


class DecisionPayload(BaseModel):
    decision_id: str = ""
    trigger_type: str = ""
    trigger_source: str = ""
    context_json: str = "{}"
    options_json: str = "{}"
    chosen_option: Optional[str] = None
    rationale: str = ""
    note: str = ""
    feedback: str = ""


class SyncConfig(BaseModel):
    """Persisted in .klemma/sync_config.json."""

    project_id: str
    api_url: str
    git_url: str
    access_token: str
    last_push: str = ""
    last_pull: str = ""
