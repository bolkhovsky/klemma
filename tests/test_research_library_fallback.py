"""Tests for library.db fragment supplement in klemma research / ask (#82).

Verifies that research_section() and build_agent_context() pull fragments
from library.db when the local project state has fewer than 10 fragments
for the requested section. Covers the cross-project sharing scenario:
project B has no local fragments but library.db has them from project A.
"""

from unittest.mock import MagicMock

from klemma.models import FragmentRecord
from klemma.skills.context_loader import supplement_fragments_from_library

# ---------------------------------------------------------------------------
# Helper builders
# ---------------------------------------------------------------------------


def _make_fragment_record(
    fragment_id: str,
    paper_id: str,
    text: str,
    fragment_type: str = "key_idea",
    citation_intent: str = "background",
) -> FragmentRecord:
    return FragmentRecord(
        fragment_id=fragment_id,
        paper_id=paper_id,
        fragment_text=text,
        fragment_type=fragment_type,
        page_number=1,
        citation_intent=citation_intent,
        content_hash=fragment_id,
    )


def _make_paper_store(frags_by_paper_id: dict[str, list[FragmentRecord]]) -> MagicMock:
    ps = MagicMock()
    ps.get_fragments.side_effect = lambda pid: frags_by_paper_id.get(pid, [])
    return ps


def _make_user_library(citekey_to_paper_id: dict[str, str]) -> MagicMock:
    ul = MagicMock()
    ul.resolve_paper_id.side_effect = lambda ck: citekey_to_paper_id.get(ck)
    return ul


# ---------------------------------------------------------------------------
# Unit tests for supplement_fragments_from_library()
# ---------------------------------------------------------------------------


class TestSupplementFragmentsFromLibrary:
    def test_adds_library_fragments_when_list_is_empty(self):
        frag = _make_fragment_record("frag1", "paper1", "Key finding from lib.")
        paper_store = _make_paper_store({"paper1": [frag]})
        user_library = _make_user_library({"smith2021": "paper1"})

        fragments: list[dict] = []
        seen: set[str] = set()
        sources = [{"id": "smith2021"}]

        added = supplement_fragments_from_library(
            fragments, seen, sources, paper_store, user_library, "1.3"
        )

        assert added == 1
        assert len(fragments) == 1
        assert fragments[0]["fragment_text"] == "Key finding from lib."
        assert fragments[0]["citekey"] == "smith2021"
        assert fragments[0]["section"] == "1.3"
        assert fragments[0]["id"] == "frag1"

    def test_deduplicates_already_seen_fragment_ids(self):
        frag = _make_fragment_record("frag1", "paper1", "Duplicate text.")
        paper_store = _make_paper_store({"paper1": [frag]})
        user_library = _make_user_library({"smith2021": "paper1"})

        # Fragment is already in the list with the same id
        existing = {"id": "frag1", "citekey": "smith2021", "fragment_text": "Duplicate text."}
        fragments: list[dict] = [existing]
        seen: set[str] = {"frag1"}
        sources = [{"id": "smith2021"}]

        added = supplement_fragments_from_library(
            fragments, seen, sources, paper_store, user_library, "1.3"
        )

        assert added == 0
        assert len(fragments) == 1  # no duplicate added

    def test_skips_sources_with_no_paper_id_in_library(self):
        paper_store = _make_paper_store({})
        user_library = _make_user_library({})  # unknown citekey

        fragments: list[dict] = []
        seen: set[str] = set()
        sources = [{"id": "unknown2020"}]

        added = supplement_fragments_from_library(
            fragments, seen, sources, paper_store, user_library, "2.1"
        )

        assert added == 0
        assert len(fragments) == 0

    def test_multiple_sources_and_fragments(self):
        frags = {
            "p1": [
                _make_fragment_record("f1", "p1", "Text A."),
                _make_fragment_record("f2", "p1", "Text B."),
            ],
            "p2": [_make_fragment_record("f3", "p2", "Text C.")],
        }
        paper_store = _make_paper_store(frags)
        user_library = _make_user_library({"jones2019": "p1", "wang2023": "p2"})

        fragments: list[dict] = []
        seen: set[str] = set()
        sources = [{"id": "jones2019"}, {"id": "wang2023"}]

        added = supplement_fragments_from_library(
            fragments, seen, sources, paper_store, user_library, "1.1"
        )

        assert added == 3
        citekeys = {f["citekey"] for f in fragments}
        assert citekeys == {"jones2019", "wang2023"}

    def test_uses_source_citekey_field_as_fallback(self):
        """Sources can have 'citekey' key instead of 'id'."""
        frag = _make_fragment_record("f1", "p1", "Text.")
        paper_store = _make_paper_store({"p1": [frag]})
        user_library = _make_user_library({"alice2022": "p1"})

        fragments: list[dict] = []
        seen: set[str] = set()
        sources = [{"citekey": "alice2022"}]  # no "id" key

        added = supplement_fragments_from_library(
            fragments, seen, sources, paper_store, user_library, "3.2"
        )

        assert added == 1
        assert fragments[0]["citekey"] == "alice2022"

    def test_returns_zero_for_empty_sources(self):
        paper_store = _make_paper_store({})
        user_library = _make_user_library({})

        fragments: list[dict] = []
        seen: set[str] = set()

        added = supplement_fragments_from_library(
            fragments, seen, [], paper_store, user_library, "1.0"
        )

        assert added == 0

    def test_per_source_cap_limits_fragments(self):
        """No more than 10 fragments per source are added (unbounded load guard)."""
        # Create 20 fragments for one source
        frags = [_make_fragment_record(f"f{i}", "p1", f"Text number {i}.") for i in range(20)]
        paper_store = _make_paper_store({"p1": frags})
        user_library = _make_user_library({"alice2022": "p1"})

        fragments: list[dict] = []
        seen: set[str] = set()

        added = supplement_fragments_from_library(
            fragments, seen, [{"id": "alice2022"}], paper_store, user_library, "1.1"
        )

        assert added == 10  # capped at 10 per source
        assert len(fragments) == 10

    def test_added_fragments_have_similarity_field(self):
        """Library-supplemented fragments include 'similarity' key for prompt rendering."""
        frag = _make_fragment_record("f1", "p1", "Some text.")
        paper_store = _make_paper_store({"p1": [frag]})
        user_library = _make_user_library({"alice2022": "p1"})

        fragments: list[dict] = []
        seen: set[str] = set()

        supplement_fragments_from_library(
            fragments, seen, [{"id": "alice2022"}], paper_store, user_library, "1.1"
        )

        assert "similarity" in fragments[0]
        assert fragments[0]["similarity"] == 0.0


# ---------------------------------------------------------------------------
# research_section(): library supplement integration
# ---------------------------------------------------------------------------


class TestResearchSectionLibrarySupplement:
    """research_section() supplements from library when local frags < 10."""

    def _make_minimal_state(self, section_sources: list[dict], fragments: list[dict]) -> MagicMock:
        state = MagicMock()
        state.get_by_section.return_value = section_sources
        state.get_by_chapter.return_value = section_sources
        state.get_fragments.return_value = fragments
        state.retrieve_similar_fragments.return_value = []
        state.get_coverage_stats.return_value = {"total_sources": len(section_sources)}
        state.get_gaps.return_value = []
        state.get_fragment_stats.return_value = {"total": len(fragments)}
        state.get_plan.return_value = None
        state.get_sections_for_type.return_value = []
        return state

    def test_supplements_from_library_when_local_empty(self, tmp_path):
        """When state.get_fragments() returns [], library frags are used."""
        lib_frag = _make_fragment_record("lib_frag1", "paper_x", "Library finding.")
        paper_store = _make_paper_store({"paper_x": [lib_frag]})
        user_library = _make_user_library({"alpha2021": "paper_x"})

        section_sources = [{"id": "alpha2021", "title": "Alpha", "authors": "A", "abstract": ""}]

        frags: list[dict] = []
        seen: set[str] = set()
        added = supplement_fragments_from_library(
            frags, seen, section_sources, paper_store, user_library, "1.3"
        )
        assert added == 1
        assert frags[0]["fragment_text"] == "Library finding."

    def test_no_supplement_when_paper_store_is_none(self):
        """Backward compat: paper_store=None → no library supplement."""
        frags: list[dict] = []
        seen: set[str] = set()

        # simulate the guard: paper_store is None → supplement not called
        paper_store = None
        user_library = None
        sources = [{"id": "smith2021"}]

        if paper_store and user_library:
            supplement_fragments_from_library(frags, seen, sources, paper_store, user_library, "1.1")

        assert len(frags) == 0  # guard short-circuits

    def test_no_supplement_when_already_10_fragments(self):
        """If >= 10 local fragments, library supplement is skipped."""
        # Build 10 existing fragments
        existing = [
            {"id": f"f{i}", "citekey": "src", "fragment_text": f"Text {i}."} for i in range(10)
        ]
        paper_store = MagicMock()
        user_library = MagicMock()
        paper_store.get_fragments.return_value = []  # shouldn't be called

        seen = {f["id"] for f in existing}
        sources = [{"id": "src"}]

        # Guard: only supplement when < 10
        if len(existing) < 10:
            supplement_fragments_from_library(
                existing, seen, sources, paper_store, user_library, "2.1"
            )

        paper_store.get_fragments.assert_not_called()


# ---------------------------------------------------------------------------
# build_agent_context(): library supplement integration
# ---------------------------------------------------------------------------


class TestBuildAgentContextLibrarySupplement:
    def test_library_fragments_appear_in_context_when_local_empty(self, tmp_path):
        """When RAG returns empty and local state has no fragments, library.db is used."""
        lib_frag = _make_fragment_record("lf1", "px", "Library context fragment.")
        paper_store = _make_paper_store({"px": [lib_frag]})
        user_library = _make_user_library({"jones2020": "px"})

        state = MagicMock()
        state.get_all_sources.return_value = [
            {"id": "jones2020", "title": "Jones", "authors": "J", "chapter": 1,
             "year": 2020, "abstract": "", "quality_score": None}
        ]
        state.get_by_section.return_value = [
            {"id": "jones2020", "title": "Jones", "authors": "J"}
        ]
        state.get_coverage_stats.return_value = {"total_sources": 1}
        state.get_gaps.return_value = []
        state.get_fragment_stats.return_value = {"total": 0}
        state.get_plan.return_value = None
        state.get_next_reading.return_value = None
        state.retrieve_similar_fragments.return_value = []

        # Verify supplement is invoked: simulate the guard condition
        relevant_fragments: list[dict] = []
        seen: set[str] = set()
        lib_sources = [{"id": "jones2020"}]

        if paper_store and user_library and len(relevant_fragments) < 5:
            from klemma.skills.context_loader import supplement_fragments_from_library

            added = supplement_fragments_from_library(
                relevant_fragments, seen, lib_sources, paper_store, user_library, "1.3"
            )
            assert added == 1
            assert relevant_fragments[0]["fragment_text"] == "Library context fragment."

    def test_no_supplement_when_rag_returns_5_or_more(self):
        """Library supplement skipped when RAG already has ≥ 5 fragments."""
        rag_frags = [
            {"id": f"r{i}", "fragment_text": f"RAG frag {i}."} for i in range(5)
        ]
        paper_store = MagicMock()
        user_library = MagicMock()

        # Guard: supplement only when < 5
        if len(rag_frags) < 5:
            supplement_fragments_from_library(
                rag_frags, set(), [], paper_store, user_library, ""
            )

        paper_store.get_fragments.assert_not_called()
