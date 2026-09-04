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

    # Verbatim integrity (PR #308): cache of the PDF text the AI saw,
    # plus per-fragment flag flips for the backfill command.
    def update_paper_raw_text(self, paper_id: str, raw_text: str) -> bool:
        """Persist the PDF's extracted text on the paper record."""
        ...

    def get_raw_text(self, paper_id: str) -> str | None:
        """Return the cached raw PDF text, or None if not yet populated."""
        ...

    def get_paper_ids_with_raw_text(self) -> list[str]:
        """Return paper_ids whose raw_text cache is populated."""
        ...

    def update_fragment_verbatim(self, fragment_id: str, verbatim: bool) -> bool:
        """Flip a fragment's verbatim flag; True if a row was updated."""
        ...

    # Extraction attempts (ADR-020): repeated attempts with per-attempt
    # fragment links (span, locator, verbatim status). Canonical fragments
    # are never deleted by this path.
    def start_attempt(self, attempt_id: str, paper_id: str, **fields: object) -> None: ...
    def finish_attempt(
        self, attempt_id: str, *, status: str, coverage_json: str = "",
        validation_incomplete: bool = False,
    ) -> bool: ...
    def save_attempt_fragments(
        self, attempt_id: str, paper_id: str, fragments: list[FragmentRecord], links: list[dict],
    ) -> int: ...
    def get_attempt(self, attempt_id: str) -> dict | None: ...
    def get_attempts(self, paper_id: str) -> list[dict]: ...
    def get_attempt_fragments(self, attempt_id: str) -> list[dict]: ...

    def get_latest_embedding_dim(self, paper_ids: list[str]) -> int | None:
        """Return the vector dimension of the most recently inserted embedding
        for any of the given papers, or None if no embeddings exist.

        Used to pin scoring to the current model's dimension after a migration.
        """
        ...

    def find_similar_fragments(
        self,
        query_vector: list[float],
        user_id: str,
        limit: int = 20,
        citekey_filter: str | None = None,
    ) -> list[dict]:
        """Return top-K fragments semantically closest to query_vector for the user.

        Implementations without KNN support must return []. Never raises.
        Each dict contains: fragment_id, fragment_text, paper_id, citekey, similarity.
        """
        ...


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

    def get_sources_by_section(self, section: str) -> list[str]:
        """Return citekeys assigned to a section."""
        ...

    def get_source_sections(self, citekey: str) -> list[str]:
        """Return section list for a citekey."""
        ...

    def get_reference_gaps(self, **kwargs: object) -> list[dict]: ...

    # Extraction runs + active set (ADR-020). A run is project- and
    # user-scoped; the active set is the run referenced by
    # project_sources.active_run_id, or the run-less legacy rows.
    def start_run(self, citekey: str, *, user_id: str | None = None, **fields: object) -> int: ...
    def publish_run(
        self, run_id: int, fragments: list[dict], *, is_partial: bool,
        validation_incomplete: bool, counters: dict | None = None,
        verify_fragment: object = None, replace_legacy: bool = False,
    ) -> str: ...
    def fail_run(self, run_id: int, error: str, **counters: object) -> None: ...
    def get_run(self, run_id: int) -> dict | None: ...
    def get_runs(self, citekey: str, user_id: str | None = None) -> list[dict]: ...
    def get_active_run_id(self, citekey: str, user_id: str | None = None) -> int | None: ...
    def get_project_fragments(
        self, citekey: str, *, user_id: str | None = None, run_id: int | None = None,
        all_runs: bool = False,
    ) -> list[dict]: ...


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

    def revoke_refresh_token(self, user_id: str, token_hash: str) -> int:
        """Revoke ONE refresh token (plus that user's expired ones).

        This is what normal rotation must use: revoking every token of the
        user would log out all their other devices on each refresh.
        Returns count revoked.
        """
        ...

    def revoke_refresh_tokens(self, user_id: str) -> int:
        """Revoke all refresh tokens for a user. Returns count revoked.

        Reserved for token reuse detection and explicit "log out everywhere":
        for rotation use [revoke_refresh_token].
        """
        ...
