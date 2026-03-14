"""Protocol interfaces for the three-tier library split (ADR-014).

Defines the boundary between Global Corpus (PaperStore),
User Library (UserLibrary), and Project data (ProjectStore).

These Protocols are the seam between tiers — each has a local SQLite
implementation (PR B/C) and will have a PostgreSQL implementation (SaaS).
Skills do NOT import this module — they receive data via arguments.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from .models import FragmentRecord, PaperRecord, UserRecord, UserSource


@runtime_checkable
class PaperStore(Protocol):
    """Content-addressable paper storage (Global Corpus).

    Stores papers, fragments, and embeddings that are shared across
    all projects. Content is immutable and deduplicated by hash.
    """

    def find_paper(
        self, *, pdf_hash: str | None = None, doi: str | None = None
    ) -> PaperRecord | None: ...

    def register_paper(
        self,
        *,
        title: str,
        authors: str,
        year: int | None = None,
        doi: str | None = None,
        abstract: str = "",
        pdf_hash: str,
    ) -> str:
        """Register a paper, return paper_id."""
        ...

    def get_fragments(self, paper_id: str) -> list[FragmentRecord]: ...

    def save_fragments(
        self,
        paper_id: str,
        fragments: list[FragmentRecord],
        prompt_hash: str,
        ai_model: str,
    ) -> int:
        """Save fragments for a paper. Returns count saved."""
        ...

    def get_paper_embedding(
        self, paper_id: str, model: str
    ) -> list[float] | None: ...

    def save_paper_embedding(
        self, paper_id: str, vector: list[float], model: str
    ) -> None: ...

    def get_fragment_embeddings(
        self, paper_id: str, model: str
    ) -> dict[str, list[float]]: ...

    def save_fragment_embedding(
        self, fragment_id: str, vector: list[float], model: str
    ) -> None: ...


@runtime_checkable
class UserLibrary(Protocol):
    """User's personal paper collection.

    Maps user-specific citekeys to global paper_ids.
    Stores per-user metadata: status, pdf_path, quality score.
    """

    def add_source(
        self, paper_id: str, citekey: str, **metadata: object
    ) -> None: ...

    def get_source_by_citekey(self, citekey: str) -> UserSource | None: ...

    def resolve_paper_id(self, citekey: str) -> str | None: ...

    def get_existing_citekeys(self) -> set[str]: ...


@runtime_checkable
class ProjectStore(Protocol):
    """Per-project data: section assignments, gaps, plans, benchmarks.

    Each project has its own DB. Same paper may appear in multiple
    projects with different section assignments and relevance scores.
    """

    def set_source_sections(
        self,
        citekey: str,
        paper_id: str,
        sections: list[str],
        chapters: list[int],
    ) -> None: ...

    def get_coverage_stats(self) -> dict: ...

    def get_reference_gaps(self, **kwargs: object) -> list[dict]: ...


@runtime_checkable
class FileStore(Protocol):
    """File storage abstraction (ADR-009).

    Stores uploaded PDFs and other files. Local implementation uses
    filesystem; SaaS can swap to S3-compatible storage.
    Files are content-addressed by paper_id.
    """

    def save(self, paper_id: str, data: bytes, filename: str) -> str:
        """Save file data, return storage path/key."""
        ...

    def read(self, paper_id: str, filename: str) -> bytes | None:
        """Read file data. Returns None if not found."""
        ...

    def exists(self, paper_id: str, filename: str) -> bool:
        """Check if a file exists."""
        ...

    def delete(self, paper_id: str, filename: str) -> bool:
        """Delete a file. Returns True if deleted, False if not found."""
        ...

    def get_path(self, paper_id: str, filename: str) -> str | None:
        """Get a resolvable path/URL for the file. Returns None if not found."""
        ...


@runtime_checkable
class UserStore(Protocol):
    """User account storage (ADR-009).

    Manages user registration, lookup, and credential storage.
    Local implementation uses SQLite; SaaS uses PostgreSQL.
    """

    def create_user(
        self,
        *,
        email: str,
        password_hash: str,
        name: str = "",
    ) -> UserRecord:
        """Create a new user. Raises ValueError if email already exists."""
        ...

    def get_user_by_email(self, email: str) -> UserRecord | None:
        """Look up a user by email. Returns None if not found."""
        ...

    def get_user_by_id(self, user_id: str) -> UserRecord | None:
        """Look up a user by ID. Returns None if not found."""
        ...

    def update_user(
        self,
        user_id: str,
        *,
        name: str | None = None,
        email_verified: bool | None = None,
    ) -> bool:
        """Update user fields. Returns True if user existed and was updated."""
        ...

    def store_refresh_token(
        self, user_id: str, token_hash: str, expires_at: str
    ) -> None:
        """Store a hashed refresh token for a user."""
        ...

    def verify_refresh_token(self, user_id: str, token_hash: str) -> bool:
        """Check if a refresh token hash is valid (exists and not expired)."""
        ...

    def revoke_refresh_tokens(self, user_id: str) -> int:
        """Revoke all refresh tokens for a user. Returns count revoked."""
        ...
