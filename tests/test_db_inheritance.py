"""Tests for DB inheritance — child project inherits parent's read-only data (#55)."""

import pytest

from klemma.state import StateManager


@pytest.fixture
def parent_state(tmp_path):
    """Create a parent StateManager with populated data."""
    db = tmp_path / "parent" / "klemma.db"
    sm = StateManager(db)
    # Register and complete parent sources
    sm.register_sources(["parent-src-1", "parent-src-2"])
    sm.mark_completed(
        "parent-src-1", note_path="@parent-src-1.md",
        quality_score=4, primary_chapter=1, primary_section="1.2",
        relevance_nr1=3, relevance_nr2=2, citation_priority="high",
    )
    sm.mark_completed(
        "parent-src-2", note_path="@parent-src-2.md",
        quality_score=3, primary_chapter=2, primary_section="2.1",
        relevance_nr1=2, relevance_nr2=1, citation_priority="medium",
    )
    # Save parent fragments
    sm.save_fragments("parent-src-1", [
        {"text": "Parent fragment 1", "type": "key_idea", "chapter": 1,
         "section": "1.2", "relevance": 4, "citation_intent": "method"},
        {"text": "Parent fragment 2", "type": "evidence", "chapter": 1,
         "section": "1.2", "relevance": 3},
    ])
    sm.save_fragments("parent-src-2", [
        {"text": "Parent fragment 3", "type": "key_idea", "chapter": 2,
         "section": "2.1", "relevance": 5, "citation_intent": "result_comparison"},
    ])
    # Save parent reference gaps
    sm.save_reference_gaps("parent-src-1", [
        {"ref_authors": "Smith J.", "ref_year": 2020, "ref_title": "Missing Paper",
         "why_relevant": "foundational", "dissertation_sections": ["1.2"]},
    ])
    return sm


@pytest.fixture
def child_state(tmp_path):
    """Create a child StateManager (empty)."""
    db = tmp_path / "child" / "klemma.db"
    return StateManager(db)


@pytest.fixture
def linked_states(parent_state, child_state):
    """Child state with parent attached."""
    child_state.set_parent(parent_state.db_path)
    return child_state, parent_state


class TestDBInheritance:

    def test_child_inherits_parent_sources(self, linked_states):
        """Parent sources visible through child's get_all_sources()."""
        child, _parent = linked_states
        sources = child.get_all_sources()
        ids = {s["id"] for s in sources}
        assert "parent-src-1" in ids
        assert "parent-src-2" in ids
        assert len(sources) == 2

    def test_child_inherits_parent_fragments(self, linked_states):
        """Parent fragments visible through child's get_fragments()."""
        child, _parent = linked_states
        frags = child.get_fragments(limit=100)
        assert len(frags) == 3
        texts = {f["fragment_text"] for f in frags}
        assert "Parent fragment 1" in texts
        assert "Parent fragment 3" in texts

    def test_child_wins_on_duplicate_source(self, linked_states):
        """If both have same source ID, child version returned."""
        child, _parent = linked_states
        # Register same source in child with different metadata
        child.register_sources(["parent-src-1"])
        child.mark_completed(
            "parent-src-1", note_path="@child-override.md",
            quality_score=5, primary_chapter=3, primary_section="3.1",
        )
        sources = child.get_all_sources()
        src_map = {s["id"]: s for s in sources}
        # Child's version wins
        assert src_map["parent-src-1"]["quality_score"] == 5
        assert src_map["parent-src-1"]["primary_chapter"] == 3
        # Parent's other source still visible
        assert "parent-src-2" in src_map

    def test_write_isolation(self, linked_states):
        """Writes go to child only — parent DB unchanged."""
        child, parent = linked_states
        child.register_sources(["child-only"])
        child.mark_completed("child-only", note_path="@child.md", quality_score=5)

        # Child sees both parent + own sources
        child_sources = child.get_all_sources()
        child_ids = {s["id"] for s in child_sources}
        assert "child-only" in child_ids
        assert "parent-src-1" in child_ids

        # Parent sees only its own
        parent_sources = parent.get_all_sources()
        parent_ids = {s["id"] for s in parent_sources}
        assert "child-only" not in parent_ids
        assert "parent-src-1" in parent_ids

    def test_inherited_coverage_stats(self, linked_states):
        """Coverage stats aggregate parent + child data."""
        child, _parent = linked_states
        stats = child.get_coverage_stats()
        # Parent has chapter 1 (1 source) and chapter 2 (1 source)
        assert stats["chapters"].get(1, 0) >= 1
        assert stats["chapters"].get(2, 0) >= 1

    def test_inherited_coverage_stats_with_child_data(self, linked_states):
        """Coverage sums when child adds data in same chapter."""
        child, _parent = linked_states
        child.register_sources(["child-ch1"])
        child.mark_completed(
            "child-ch1", note_path="@c.md", quality_score=4,
            primary_chapter=1, primary_section="1.3",
        )
        stats = child.get_coverage_stats()
        # Chapter 1 should have 2 sources total (parent + child)
        assert stats["chapters"].get(1, 0) == 2

    def test_inherited_similar_fragments(self, linked_states):
        """RAG search spans both DBs — parent fragment embeddings accessible."""
        child, parent = linked_states
        # Store embeddings in parent fragments
        parent_frags = parent.get_fragments(limit=100)
        vec_a = [0.5, 0.5, 0.0]
        vec_b = [0.0, 0.5, 0.5]
        for i, frag in enumerate(parent_frags[:2]):
            parent.save_fragment_embedding(
                frag["id"], vec_a if i == 0 else vec_b, "test-model",
            )
        # Query from child — should find parent's fragments
        query = [0.4, 0.5, 0.1]  # similar to vec_a
        results = child.retrieve_similar_fragments(query, top_k=5, model="test-model")
        assert len(results) >= 1
        # First result should be closest to query (vec_a)
        assert results[0]["similarity"] > 0

    def test_inherited_fragment_embeddings(self, linked_states):
        """get_fragment_embeddings() merges parent + child dicts."""
        child, parent = linked_states
        # Parent embeddings
        parent_frags = parent.get_fragments(limit=100)
        parent.save_fragment_embedding(parent_frags[0]["id"], [0.1, 0.2], "m1")
        # Child own fragment
        child.register_sources(["child-src"])
        child.mark_completed("child-src", note_path="@c.md", quality_score=3)
        child.save_fragments("child-src", [
            {"text": "Child frag", "type": "key_idea", "section": "3.1", "relevance": 4},
        ])
        child_frags = child.get_fragments(source_id="child-src")
        child.save_fragment_embedding(child_frags[0]["id"], [0.3, 0.4], "m1")

        merged = child.get_fragment_embeddings(model="m1")
        # Should have at least 2 entries (one from parent, one from child)
        assert len(merged) >= 2

    def test_inherited_source_embeddings(self, linked_states):
        """get_all_embeddings() merges parent + child source embeddings."""
        child, parent = linked_states
        parent.save_embedding("parent-src-1", [0.1, 0.2, 0.3], "spec")
        parent.save_embedding("parent-src-2", [0.4, 0.5, 0.6], "spec")

        merged = child.get_all_embeddings(model="spec")
        assert "parent-src-1" in merged
        assert "parent-src-2" in merged
        assert len(merged) == 2

    def test_inherited_source_embeddings_child_wins(self, linked_states):
        """When child has same source embedding, child version wins."""
        child, parent = linked_states
        parent.save_embedding("parent-src-1", [0.1, 0.2, 0.3], "spec")
        # Override in child
        child.register_sources(["parent-src-1"])
        child.save_embedding("parent-src-1", [0.9, 0.8, 0.7], "spec")

        merged = child.get_all_embeddings(model="spec")
        assert merged["parent-src-1"] == pytest.approx([0.9, 0.8, 0.7], abs=0.01)

    def test_inherited_reference_gaps(self, linked_states):
        """Parent's reference gaps visible through child."""
        child, _parent = linked_states
        gaps = child.get_reference_gaps(limit=50)
        titles = [g["ref_title"] for g in gaps]
        assert "Missing Paper" in titles

    def test_no_inheritance_when_disabled(self, parent_state, child_state):
        """inherit_db=false → child sees only its own data."""
        # Don't call set_parent — no inheritance
        sources = child_state.get_all_sources()
        assert len(sources) == 0

    def test_no_parent_no_change(self, child_state):
        """Single project without parent works as before."""
        child_state.register_sources(["solo-src"])
        child_state.mark_completed("solo-src", note_path="@s.md", quality_score=4)
        sources = child_state.get_all_sources()
        assert len(sources) == 1
        assert sources[0]["id"] == "solo-src"

    def test_get_by_chapter_inherits(self, linked_states):
        """get_by_chapter() returns parent sources for matching chapter."""
        child, _parent = linked_states
        ch1_sources = child.get_by_chapter(1)
        ids = {s["id"] for s in ch1_sources}
        assert "parent-src-1" in ids

    def test_get_by_section_inherits(self, linked_states):
        """get_by_section() returns parent sources for matching section."""
        child, _parent = linked_states
        sec_sources = child.get_by_section("2.1")
        ids = {s["id"] for s in sec_sources}
        assert "parent-src-2" in ids

    def test_sources_without_embeddings_excludes_parent_embedded(self, linked_states):
        """get_sources_without_embeddings() skips sources embedded in parent DB."""
        child, parent = linked_states
        # Parent has embeddings for both sources
        parent.save_embedding("parent-src-1", [0.1, 0.2, 0.3], "spec")
        parent.save_embedding("parent-src-2", [0.4, 0.5, 0.6], "spec")

        # Child inherits the sources but has no local embeddings
        # Without the fix, both would be reported as missing
        missing = child.get_sources_without_embeddings()
        assert "parent-src-1" not in missing
        assert "parent-src-2" not in missing

    def test_sources_without_embeddings_reports_truly_missing(self, linked_states):
        """Child-only sources without embeddings are still reported."""
        child, parent = linked_states
        # Embed parent sources
        parent.save_embedding("parent-src-1", [0.1, 0.2, 0.3], "spec")
        parent.save_embedding("parent-src-2", [0.4, 0.5, 0.6], "spec")

        # Register a child-only source without embedding
        child.register_sources(["child-new"])
        child.mark_completed("child-new", note_path="@child-new.md")

        missing = child.get_sources_without_embeddings()
        # Parent sources are embedded — not missing
        assert "parent-src-1" not in missing
        assert "parent-src-2" not in missing
        # child-new has no embedding — still reported
        assert "child-new" in missing

    def test_sources_without_embeddings_no_parent(self, child_state):
        """Without parent, method returns all un-embedded completed sources."""
        child_state.register_sources(["solo1"])
        child_state.mark_completed("solo1", note_path="@s.md")
        missing = child_state.get_sources_without_embeddings()
        assert "solo1" in missing
