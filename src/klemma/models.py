"""Data classes for the three-tier library storage layer (ADR-014).

These are storage-layer records — distinct from the Pydantic models in
literature/models.py which represent AI extraction output. The naming
convention is *Record/*Source to avoid collision with existing types:

- PaperRecord   — global corpus paper (vs ZoteroEntry for Zotero data)
- FragmentRecord — stored fragment (vs Fragment for extraction-time model)
- UserSource    — user's citekey mapping to a global paper
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class PaperRecord:
    """A paper in the global corpus (library.db)."""

    paper_id: str
    pdf_hash: str | None = None
    doi: str | None = None
    title: str = ""
    authors: str = ""
    year: int | None = None
    abstract: str = ""


@dataclass
class FragmentRecord:
    """A stored fragment in the global corpus (library.db).

    fragment_id is content-addressable: SHA256(paper_id + text + page).
    See hashing.compute_content_hash().
    """

    fragment_id: str  # content_hash
    paper_id: str
    fragment_text: str
    fragment_type: str = "key_idea"
    page_number: int | None = None
    citation_intent: str | None = None
    verbatim: bool = False
    content_hash: str = ""  # same as fragment_id, explicit for clarity


@dataclass
class UserRecord:
    """A registered user account (ADR-009)."""

    user_id: str
    email: str
    password_hash: str
    name: str = ""
    username: str = ""
    email_verified: bool = False
    created_at: str = ""  # ISO 8601


@dataclass
class UserSource:
    """A user's source entry mapping citekey → global paper.

    `citekey` is the internal, immutable label used as PK and as the
    reference key in drafts/curation/section-assignments (citekey-stability
    invariant, issue #268). `external_citekey` is an optional display
    override imported from a user's BetterBibTeX JSON (part 2 of BBT parity
    plan): when set, it's what the cloud emits into generated text and
    echoes in API responses, so `[@ck]` references match the user's local
    `.bib` file. Dual-key lookups accept either on read paths; all writes
    still use `citekey`.
    """

    citekey: str
    paper_id: str
    status: str = "pending"
    pdf_path: str | None = None
    note_path: str | None = None
    quality_score: int | None = None
    chapters: list[int] = field(default_factory=list)
    sections: list[str] = field(default_factory=list)
    external_citekey: str | None = None
    project_id: str | None = None
