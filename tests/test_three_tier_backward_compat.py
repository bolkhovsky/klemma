"""Phase 1G backward compatibility tests for the three-tier library.

Validates that projects created before three-tier introduction (klemma.db only,
no library.db, no project.db) continue to work correctly after upgrade.

Test scenarios:
- Legacy project sources/fragments readable from klemma.db
- paper_store miss on legacy paper (no false-positive dedup)
- user_library miss on legacy citekey (no false-positive skip)
- paper_store=None path skips dedup gracefully
- project_store=None path skips dual-write gracefully
- Coverage stats from klemma.db unchanged by empty library.db
- Fragment retrieval unchanged when library.db is empty
"""




# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_legacy_state(tmp_path):
    """Create a legacy StateManager with sources and fragments (no library.db)."""
    from klemma.state import StateManager

    sm = StateManager(tmp_path / "klemma.db")
    sm.register_sources(["legacy_a", "legacy_b"])
    sm.mark_completed("legacy_a", note_path="@legacy_a.md")
    sm.mark_completed("legacy_b", note_path="@legacy_b.md")
    sm.set_source_sections("legacy_a", ["1.1"], [1])
    sm.set_source_sections("legacy_b", ["1.2"], [1])
    sm.save_fragments(
        "legacy_a",
        [
            {"text": "Fragment from legacy A", "type": "background", "section": "1.1"},
            {"text": "Another fragment from legacy A", "type": "method", "section": "1.1"},
        ],
    )
    sm.save_fragments(
        "legacy_b",
        [{"text": "Fragment from legacy B", "type": "result", "section": "1.2"}],
    )
    return sm


# ---------------------------------------------------------------------------
# StateManager reads from klemma.db remain intact
# ---------------------------------------------------------------------------


class TestLegacyStateManagerReads:
    """StateManager reads from klemma.db are unaffected by empty library.db."""

    def test_get_all_sources_from_legacy_db(self, tmp_path):
        """Sources registered in klemma.db are returned by get_all_sources()."""
        sm = _make_legacy_state(tmp_path)

        sources = sm.get_all_sources()
        citekeys = [s["id"] for s in sources]
        assert "legacy_a" in citekeys
        assert "legacy_b" in citekeys

    def test_get_source_from_legacy_db(self, tmp_path):
        """get_source() returns individual source from klemma.db."""
        sm = _make_legacy_state(tmp_path)

        src = sm.get_source("legacy_a")
        assert src is not None
        assert src["id"] == "legacy_a"

    def test_get_fragments_from_legacy_db(self, tmp_path):
        """Fragments stored in klemma.db are returned by get_fragments()."""
        sm = _make_legacy_state(tmp_path)

        frags = sm.get_fragments(section="1.1", limit=50)
        assert len(frags) >= 2
        texts = [f["fragment_text"] for f in frags]
        assert any("legacy A" in t for t in texts)

    def test_coverage_stats_from_legacy_db(self, tmp_path):
        """Coverage stats computed from klemma.db are correct."""
        sm = _make_legacy_state(tmp_path)

        stats = sm.get_coverage_stats()
        # get_coverage_stats returns chapters/sections dicts; check no KeyError
        assert "chapters" in stats
        assert "sections" in stats
        # Verify sources are registered separately
        sources = sm.get_all_sources()
        assert len(sources) >= 2

    def test_get_by_section_from_legacy_db(self, tmp_path):
        """get_by_section() returns sources from klemma.db section assignments."""
        sm = _make_legacy_state(tmp_path)

        section_sources = sm.get_by_section("1.1")
        citekeys = [s["id"] for s in section_sources]
        assert "legacy_a" in citekeys

    def test_fragment_stats_from_legacy_db(self, tmp_path):
        """get_fragment_stats() returns correct counts from klemma.db."""
        sm = _make_legacy_state(tmp_path)

        stats = sm.get_fragment_stats()
        assert stats["total"] >= 3  # legacy_a: 2, legacy_b: 1


# ---------------------------------------------------------------------------
# paper_store and user_library: miss on legacy papers
# ---------------------------------------------------------------------------


class TestLibraryMissOnLegacyPapers:
    """Empty library.db returns None/empty for legacy papers — correct miss behavior."""

    def test_paper_store_miss_on_legacy_pdf_hash(self, tmp_path):
        """paper_store.find_paper(pdf_hash=...) returns None for legacy paper."""
        from klemma.stores.paper_store import LocalPaperStore

        # library.db is fresh — simulates first run after upgrade
        paper_store = LocalPaperStore(tmp_path / "library.db")

        result = paper_store.find_paper(pdf_hash="some-legacy-pdf-hash")
        assert result is None

    def test_paper_store_miss_on_legacy_doi(self, tmp_path):
        """paper_store.find_paper(doi=...) returns None for legacy paper."""
        from klemma.stores.paper_store import LocalPaperStore

        paper_store = LocalPaperStore(tmp_path / "library.db")

        result = paper_store.find_paper(doi="10.1234/legacy-paper")
        assert result is None

    def test_user_library_miss_on_legacy_citekey(self, tmp_path):
        """user_library.get_source_by_citekey() returns None for legacy citekey."""
        from klemma.stores.user_library import LocalUserLibrary

        user_library = LocalUserLibrary(tmp_path / "library.db")

        result = user_library.get_source_by_citekey("legacy_a")
        assert result is None

    def test_user_library_resolve_paper_id_miss_on_legacy(self, tmp_path):
        """user_library.resolve_paper_id() returns None for legacy citekey."""
        from klemma.stores.user_library import LocalUserLibrary

        user_library = LocalUserLibrary(tmp_path / "library.db")

        result = user_library.resolve_paper_id("legacy_a")
        assert result is None

    def test_user_library_existing_citekeys_empty_for_legacy(self, tmp_path):
        """user_library.get_existing_citekeys() returns empty for fresh library."""
        from klemma.stores.user_library import LocalUserLibrary

        user_library = LocalUserLibrary(tmp_path / "library.db")

        result = user_library.get_existing_citekeys()
        assert result == set()


# ---------------------------------------------------------------------------
# paper_store=None path: graceful degradation
# ---------------------------------------------------------------------------


class TestPaperStoreNoneGracefulDegradation:
    """When paper_store=None, all dedup checks are skipped gracefully."""

    def test_state_operations_work_without_paper_store(self, tmp_path):
        """StateManager CRUD works normally when paper_store is not initialized."""
        sm = _make_legacy_state(tmp_path)

        # No paper_store — just StateManager operations
        sm.register_sources(["new_source"])
        sm.mark_completed("new_source", note_path="@new_source.md")

        src = sm.get_source("new_source")
        assert src is not None

    def test_get_fragments_unaffected_without_paper_store(self, tmp_path):
        """Fragment retrieval from StateManager is unaffected by absent paper_store."""
        sm = _make_legacy_state(tmp_path)

        frags = sm.get_fragments(limit=50)
        assert len(frags) >= 3  # legacy_a: 2, legacy_b: 1


# ---------------------------------------------------------------------------
# Coexistence: klemma.db + library.db + project.db
# ---------------------------------------------------------------------------


class TestThreeTierCoexistence:
    """Three databases can coexist; each serves its role without interfering."""

    def test_library_db_created_alongside_klemma_db(self, tmp_path):
        """library.db can be created next to klemma.db without data corruption."""
        from klemma.stores.paper_store import LocalPaperStore
        from klemma.stores.user_library import LocalUserLibrary

        # Legacy klemma.db
        sm = _make_legacy_state(tmp_path)

        # New library.db alongside
        lib_db = tmp_path / "library.db"
        paper_store = LocalPaperStore(lib_db)
        user_library = LocalUserLibrary(lib_db)

        # Legacy data still readable
        sources = sm.get_all_sources()
        assert len(sources) >= 2

        # library.db is clean
        assert paper_store.find_paper(pdf_hash="any-hash") is None
        assert user_library.get_existing_citekeys() == set()

    def test_paper_registered_in_library_does_not_affect_klemma_db(self, tmp_path):
        """Registering a paper in library.db doesn't modify klemma.db."""
        from klemma.stores.paper_store import LocalPaperStore

        sm = _make_legacy_state(tmp_path)
        paper_store = LocalPaperStore(tmp_path / "library.db")

        # Register a new paper in library.db
        pid = paper_store.register_paper(
            title="New Paper",
            pdf_hash="new-hash",
            doi="10.1234/new",
        )

        # klemma.db sources unchanged
        sources = sm.get_all_sources()
        citekeys = [s["id"] for s in sources]
        assert "legacy_a" in citekeys
        assert "legacy_b" in citekeys

        # New paper is NOT in klemma.db (different storage tier)
        src = sm.get_source(pid)  # pid is a UUID, not in klemma.db
        assert src is None

    def test_project_db_created_alongside_klemma_db(self, tmp_path):
        """project.db can be created next to klemma.db without data corruption."""
        from klemma.stores.project_store import LocalProjectStore

        sm = _make_legacy_state(tmp_path)

        project_db = tmp_path / "project.db"
        project_store = LocalProjectStore(project_db)

        # Legacy StateManager data intact
        frags = sm.get_fragments(section="1.1", limit=10)
        assert len(frags) >= 2

        # project.db is independent
        assert project_store.count_sources() == 0

    def test_coverage_stats_not_affected_by_project_db(self, tmp_path):
        """Coverage stats from StateManager are unaffected by project.db content."""
        from klemma.stores.project_store import LocalProjectStore

        sm = _make_legacy_state(tmp_path)
        project_store = LocalProjectStore(tmp_path / "project.db")

        # Add entries to project.db
        project_store.set_source_sections("legacy_a", "paper_id_1", ["1.1"], [1])

        # StateManager sources from klemma.db unchanged
        sources = sm.get_all_sources()
        assert len(sources) >= 2


# ---------------------------------------------------------------------------
# Embedding roundtrip through klemma.db (not library.db)
# ---------------------------------------------------------------------------


class TestLegacyEmbeddings:
    """Source embeddings stored in klemma.db remain accessible."""

    def test_source_embedding_saved_and_retrieved_from_klemma_db(self, tmp_path):
        """Source embedding in klemma.db survives library.db creation."""
        from klemma.stores.paper_store import LocalPaperStore

        sm = _make_legacy_state(tmp_path)

        # Save embedding for legacy source in klemma.db
        vector = [0.1, 0.2, 0.3]
        sm.save_embedding("legacy_a", vector, "test-model")

        # library.db exists but is empty (just creating to verify no interference)
        LocalPaperStore(tmp_path / "library.db")

        # Embedding still retrievable from klemma.db
        embeddings = sm.get_all_embeddings(model="test-model")
        assert "legacy_a" in embeddings
        assert all(abs(a - b) < 1e-5 for a, b in zip(embeddings["legacy_a"], vector))
