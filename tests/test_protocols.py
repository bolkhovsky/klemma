"""Tests for three-tier Protocol interfaces and data classes (ADR-014)."""

from klemma.hashing import compute_content_hash
from klemma.models import FragmentRecord, PaperRecord, UserSource
from klemma.protocols import PaperStore, ProjectStore, UserLibrary


class TestPaperRecord:
    def test_required_field(self):
        r = PaperRecord(paper_id="abc123")
        assert r.paper_id == "abc123"
        assert r.pdf_hash is None
        assert r.doi is None
        assert r.title == ""
        assert r.year is None

    def test_all_fields(self):
        r = PaperRecord(
            paper_id="abc",
            pdf_hash="deadbeef",
            doi="10.1234/test",
            title="Test Paper",
            authors="Smith J.",
            year=2024,
            abstract="Abstract text.",
        )
        assert r.doi == "10.1234/test"
        assert r.year == 2024


class TestFragmentRecord:
    def test_required_fields(self):
        r = FragmentRecord(
            fragment_id="hash123",
            paper_id="paper1",
            fragment_text="Important finding",
        )
        assert r.fragment_id == "hash123"
        assert r.fragment_type == "key_idea"
        assert r.page_number is None
        assert r.citation_intent is None

    def test_content_hash_matches_fragment_id(self):
        h = compute_content_hash("paper1", "text", 5)
        r = FragmentRecord(
            fragment_id=h,
            paper_id="paper1",
            fragment_text="text",
            content_hash=h,
        )
        assert r.fragment_id == r.content_hash


class TestUserSource:
    def test_required_fields(self):
        s = UserSource(citekey="smith2024", paper_id="abc")
        assert s.status == "pending"
        assert s.pdf_path is None
        assert s.quality_score is None
        assert s.chapters == []
        assert s.sections == []

    def test_all_fields(self):
        s = UserSource(
            citekey="smith2024",
            paper_id="abc",
            status="completed",
            pdf_path="/path/to/pdf",
            quality_score=4,
            chapters=[1, 2],
            sections=["1.1", "2.3"],
        )
        assert s.quality_score == 4
        assert len(s.chapters) == 2


class TestProtocolsAreRuntimeCheckable:
    """Verify Protocols can be used with isinstance() checks."""

    def test_paper_store_is_protocol(self):
        assert hasattr(PaperStore, "__protocol_attrs__") or hasattr(
            PaperStore, "_is_runtime_protocol"
        )

    def test_user_library_is_protocol(self):
        assert hasattr(UserLibrary, "__protocol_attrs__") or hasattr(
            UserLibrary, "_is_runtime_protocol"
        )

    def test_project_store_is_protocol(self):
        assert hasattr(ProjectStore, "__protocol_attrs__") or hasattr(
            ProjectStore, "_is_runtime_protocol"
        )

    def test_non_implementing_class_fails_check(self):
        class Empty:
            pass

        assert not isinstance(Empty(), PaperStore)
        assert not isinstance(Empty(), UserLibrary)
        assert not isinstance(Empty(), ProjectStore)
