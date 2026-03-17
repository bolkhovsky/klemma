"""Adapter classes for headless SaaS mode (no Obsidian vault, no monolithic StateManager).

_NullVault: stub VaultAdapter for SaaS — returns safe defaults for all reads.
_SaaSStateAdapter: wraps three-tier stores to satisfy StateManager interface for skills.

These adapters live in api/ (not core) because they are SaaS-specific glue code.
Core skills remain unchanged — they see the same interface they expect from CLI.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from klemma.stores.paper_store import LocalPaperStore
    from klemma.stores.project_store import LocalProjectStore
    from klemma.stores.user_library import LocalUserLibrary

logger = logging.getLogger(__name__)


class _NullVault:
    """Stub VaultAdapter for SaaS — no Obsidian vault available.

    Returns empty/safe defaults for all reads. Satisfies the VaultAdapter
    interface expected by research_section() and context_loader functions.
    """

    vault_path = ""

    def read_note(self, name: str) -> str:
        return ""

    def get_properties(self, name: str) -> dict:
        return {}

    def list_notes(self, folder: str = "", pattern: str = "*.md") -> list[str]:
        return []

    def write_note(self, name: str, content: str, folder: str = "") -> None:
        pass

    def update_section(self, name: str, heading: str, content: str) -> None:
        pass

    def check_folder(self, folder: str) -> bool:
        return False

    def search(self, query: str, limit: int = 20) -> list[dict]:
        return []


class _SaaSStateAdapter:
    """Wraps three-tier stores to satisfy StateManager interface for skills.

    research_section() calls ~10 methods on its ``state`` parameter.
    This adapter translates those calls to paper_store + project_store +
    user_library queries.  Only methods actually used by researcher.py
    are implemented — anything else will raise AttributeError loudly.

    NOT a full StateManager replacement — intentionally minimal.
    """

    def __init__(
        self,
        paper_store: LocalPaperStore,
        project_store: LocalProjectStore,
        user_library: LocalUserLibrary,
    ) -> None:
        self._paper = paper_store
        self._project = project_store
        self._library = user_library

    # ── source queries ────────────────────────────────────────────────

    def get_by_section(self, section: str, section_type: str | None = None) -> list[dict]:
        citekeys = self._project.get_sources_by_section(section)
        return self._enrich_sources(citekeys)

    def get_by_chapter(self, chapter: int) -> list[dict]:
        stats = self._project.get_coverage_stats()
        chapter_prefix = f"{chapter}."
        citekeys: set[str] = set()
        for sec in stats.get("sections", {}):
            if sec == str(chapter) or sec.startswith(chapter_prefix):
                citekeys.update(self._project.get_sources_by_section(sec))
        return self._enrich_sources(list(citekeys))

    def get_source(self, source_id: str) -> dict | None:
        src = self._library.get_source_by_citekey(source_id)
        if not src:
            return None
        paper = self._paper.get_paper_by_id(src.paper_id)
        if not paper:
            return None
        frags = self._paper.get_fragments(src.paper_id)
        return {
            "id": source_id,
            "title": paper.title or "",
            "authors": paper.authors or "",
            "year": paper.year,
            "doi": paper.doi or "",
            "abstract": paper.abstract or "",
            "fragment_count": len(frags),
            "quality_score": 0,
            "primary_chapter": None,
            "primary_section": None,
            "relevance_nr1": 0,
            "relevance_nr2": 0,
            "citation_priority": "medium",
            "note_path": "",
        }

    def get_all_sources(self) -> list[dict]:
        all_src = self._library.get_all_sources()
        return self._enrich_sources([s.citekey for s in all_src])

    def get_existing_source_ids(self) -> set[str]:
        return self._library.get_existing_citekeys()

    # ── fragment queries ──────────────────────────────────────────────

    def get_fragments(
        self,
        source_id: str | None = None,
        chapter: int | None = None,
        section: str | None = None,
        fragment_type: str | None = None,
        limit: int = 50,
        section_type: str | None = None,
    ) -> list[dict]:
        if source_id:
            src = self._library.get_source_by_citekey(source_id)
            if not src:
                return []
            frags = self._paper.get_fragments(src.paper_id)
            return [self._frag_to_dict(f, source_id) for f in frags[:limit]]

        citekeys: list[str] = []
        if section:
            citekeys = self._project.get_sources_by_section(section)
        elif chapter:
            stats = self._project.get_coverage_stats()
            prefix = f"{chapter}."
            for sec in stats.get("sections", {}):
                if sec == str(chapter) or sec.startswith(prefix):
                    citekeys.extend(self._project.get_sources_by_section(sec))
            citekeys = list(set(citekeys))

        result: list[dict] = []
        for ck in citekeys:
            src = self._library.get_source_by_citekey(ck)
            if not src:
                continue
            frags = self._paper.get_fragments(src.paper_id)
            for f in frags:
                result.append(self._frag_to_dict(f, ck))
                if len(result) >= limit:
                    return result
        return result

    def retrieve_similar_fragments(
        self, query_embedding: list[float], top_k: int = 10, model: str | None = None
    ) -> list[dict]:
        return []  # RAG deferred

    # ── coverage / gaps ───────────────────────────────────────────────

    def get_coverage_stats(self) -> dict:
        return self._project.get_coverage_stats()

    def get_gaps(self, min_sources: int = 3) -> list[dict]:
        return []

    def get_fragment_stats(self) -> dict:
        all_sources = self._library.get_all_sources()
        total = 0
        by_type: dict[str, int] = {}
        for src in all_sources:
            frags = self._paper.get_fragments(src.paper_id)
            total += len(frags)
            for f in frags:
                ft = f.fragment_type or "key_idea"
                by_type[ft] = by_type.get(ft, 0) + 1
        return {"total": total, "by_type": by_type, "by_chapter": {}, "by_section": {}}

    # ── internal helpers ──────────────────────────────────────────────

    def _enrich_sources(self, citekeys: list[str]) -> list[dict]:
        result = []
        for ck in citekeys:
            src = self.get_source(ck)
            if src:
                result.append(src)
        return result

    @staticmethod
    def _frag_to_dict(f: object, citekey: str) -> dict:
        return {
            "id": getattr(f, "fragment_id", ""),
            "source_id": citekey,
            "citekey": citekey,
            "fragment_text": getattr(f, "fragment_text", ""),
            "fragment_type": getattr(f, "fragment_type", "key_idea") or "key_idea",
            "page_number": getattr(f, "page_number", None),
            "citation_intent": getattr(f, "citation_intent", None),
            "section": "",
            "relevance_score": 3,
            "usage_hint": "",
        }
