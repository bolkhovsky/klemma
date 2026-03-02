"""Tests for auto-metadata extraction in klemma acquire (#33) and Zotero API (#70)."""

from pathlib import Path
from unittest.mock import MagicMock, patch  # noqa: I001

import pytest
import requests

# ---------------------------------------------------------------------------
# extract_pdf_metadata
# ---------------------------------------------------------------------------


class TestExtractPdfMetadata:
    def test_with_title_and_author(self):
        """PDF has title+author in doc.metadata → returned."""
        from klemma.literature.metadata import extract_pdf_metadata

        mock_doc = MagicMock()
        mock_doc.metadata = {"title": "Deep Learning for NLP", "author": "Smith J."}
        mock_doc.__enter__ = lambda s: s
        mock_doc.__exit__ = MagicMock(return_value=False)

        with patch("klemma.literature.metadata.fitz") as mock_fitz:
            mock_fitz.open.return_value = mock_doc
            result = extract_pdf_metadata(Path("/tmp/paper.pdf"))

        assert result["title"] == "Deep Learning for NLP"
        assert result["authors"] == "Smith J."

    def test_empty_metadata(self):
        """PDF has no metadata → empty dict values."""
        from klemma.literature.metadata import extract_pdf_metadata

        mock_doc = MagicMock()
        mock_doc.metadata = {"title": "", "author": ""}
        mock_doc.__enter__ = lambda s: s
        mock_doc.__exit__ = MagicMock(return_value=False)
        # No pages for fallback
        mock_doc.__len__ = lambda s: 0
        mock_doc.__iter__ = lambda s: iter([])

        with patch("klemma.literature.metadata.fitz") as mock_fitz:
            mock_fitz.open.return_value = mock_doc
            result = extract_pdf_metadata(Path("/tmp/paper.pdf"))

        assert result["title"] == ""
        assert result["authors"] == ""

    def test_first_page_fallback(self):
        """Title is generic ('Microsoft Word') → first-page heuristic extracts real title."""
        from klemma.literature.metadata import extract_pdf_metadata

        mock_doc = MagicMock()
        mock_doc.metadata = {"title": "Microsoft Word - paper.docx", "author": ""}
        mock_doc.__enter__ = lambda s: s
        mock_doc.__exit__ = MagicMock(return_value=False)

        # Simulate first page with text blocks of different sizes
        mock_page = MagicMock()
        mock_page.get_text.return_value = [
            # (x0, y0, x1, y1, "text", block_no, block_type)  — dict blocks
        ]
        # Use dict-style blocks from get_text("dict")
        mock_page.get_text.return_value = {
            "blocks": [
                {
                    "type": 0,  # text block
                    "lines": [
                        {
                            "spans": [
                                {"text": "Deep Learning for NLP", "size": 18.0},
                            ]
                        }
                    ],
                },
                {
                    "type": 0,
                    "lines": [
                        {
                            "spans": [
                                {"text": "Abstract. This paper...", "size": 10.0},
                            ]
                        }
                    ],
                },
            ]
        }
        mock_doc.__len__ = lambda s: 1
        mock_doc.__getitem__ = lambda s, i: mock_page

        with patch("klemma.literature.metadata.fitz") as mock_fitz:
            mock_fitz.open.return_value = mock_doc
            result = extract_pdf_metadata(Path("/tmp/paper.pdf"))

        assert result["title"] == "Deep Learning for NLP"


# ---------------------------------------------------------------------------
# lookup_s2
# ---------------------------------------------------------------------------


class TestLookupS2:
    def test_success(self):
        """S2 returns matching paper → metadata returned."""
        from klemma.literature.metadata import lookup_s2

        s2_response = {
            "data": [
                {
                    "title": "Deep Learning for NLP: A Survey",
                    "authors": [
                        {"name": "John Smith"},
                        {"name": "Kate Jones"},
                    ],
                    "year": 2024,
                    "abstract": "We survey deep learning methods...",
                    "externalIds": {"DOI": "10.1234/test"},
                }
            ]
        }

        with patch("klemma.literature.metadata.requests") as mock_req:
            mock_resp = MagicMock()
            mock_resp.json.return_value = s2_response
            mock_resp.raise_for_status = MagicMock()
            mock_req.get.return_value = mock_resp
            result = lookup_s2("Deep Learning for NLP")

        assert result is not None
        assert result["year"] == 2024
        assert "Smith" in result["authors"]
        assert result["doi"] == "10.1234/test"

    def test_no_match(self):
        """S2 returns papers but no title match → None."""
        from klemma.literature.metadata import lookup_s2

        s2_response = {
            "data": [
                {
                    "title": "Completely Unrelated Paper on Chemistry",
                    "authors": [{"name": "Alice"}],
                    "year": 2020,
                    "abstract": "",
                    "externalIds": {},
                }
            ]
        }

        with patch("klemma.literature.metadata.requests") as mock_req:
            mock_resp = MagicMock()
            mock_resp.json.return_value = s2_response
            mock_resp.raise_for_status = MagicMock()
            mock_req.get.return_value = mock_resp
            result = lookup_s2("Deep Learning for NLP")

        assert result is None

    def test_api_error(self):
        """Requests raises → None (graceful)."""
        from klemma.literature.metadata import lookup_s2

        with patch("klemma.literature.metadata.requests") as mock_req:
            mock_req.get.side_effect = Exception("Connection timeout")
            result = lookup_s2("Deep Learning for NLP")

        assert result is None


# ---------------------------------------------------------------------------
# resolve_metadata
# ---------------------------------------------------------------------------


class TestResolveMetadata:
    def test_cli_wins(self):
        """CLI title/authors override PDF+S2."""
        from klemma.literature.metadata import resolve_metadata

        with patch("klemma.literature.metadata.extract_pdf_metadata") as mock_pdf, \
             patch("klemma.literature.metadata.lookup_s2") as mock_s2:
            mock_pdf.return_value = {"title": "PDF Title", "authors": "PDF Author"}
            mock_s2.return_value = {
                "title": "S2 Title", "authors": "S2 Author",
                "year": 2024, "abstract": "Abstract", "doi": "10.1/x",
            }

            result = resolve_metadata(
                Path("/tmp/paper.pdf"),
                cli_title="My Title",
                cli_authors="My Authors",
                cli_year=2023,
            )

        assert result["title"] == "My Title"
        assert result["authors"] == "My Authors"
        assert result["year"] == 2023
        # S2 enriches abstract and doi even when CLI provides title
        assert result["doi"] == "10.1/x"

    def test_pdf_then_s2(self):
        """PDF title → S2 enriches year+doi."""
        from klemma.literature.metadata import resolve_metadata

        with patch("klemma.literature.metadata.extract_pdf_metadata") as mock_pdf, \
             patch("klemma.literature.metadata.lookup_s2") as mock_s2:
            mock_pdf.return_value = {"title": "Deep Learning", "authors": ""}
            mock_s2.return_value = {
                "title": "Deep Learning", "authors": "Smith J., Jones K.",
                "year": 2024, "abstract": "We study...", "doi": "10.5555/dl",
            }

            result = resolve_metadata(Path("/tmp/paper.pdf"))

        assert result["title"] == "Deep Learning"
        assert result["authors"] == "Smith J., Jones K."
        assert result["year"] == 2024
        assert result["doi"] == "10.5555/dl"

    def test_no_sources(self):
        """No PDF metadata, S2 fails → empty fallback."""
        from klemma.literature.metadata import resolve_metadata

        with patch("klemma.literature.metadata.extract_pdf_metadata") as mock_pdf, \
             patch("klemma.literature.metadata.lookup_s2") as mock_s2:
            mock_pdf.return_value = {"title": "", "authors": ""}
            mock_s2.return_value = None

            result = resolve_metadata(Path("/tmp/paper.pdf"))

        assert result["title"] == ""
        assert result["authors"] == ""
        assert result["year"] is None
        assert result["doi"] == ""


# ---------------------------------------------------------------------------
# Citekey from resolved metadata
# ---------------------------------------------------------------------------


class TestCitekeyFromMetadata:
    def test_citekey_from_real_metadata(self):
        """Full flow: metadata resolved → good citekey (not 'unknown_paper')."""
        from klemma.skills.acquirer import PaperMetadata, _generate_citekey

        meta = PaperMetadata(
            url="https://example.com/paper.pdf",
            title="Deep Learning for NLP",
            authors="Smith J., Jones K.",
            year=2024,
        )
        citekey = _generate_citekey(meta)

        assert citekey.startswith("Smith2024")
        assert "unknown" not in citekey


# ---------------------------------------------------------------------------
# DB: update_source_info + migration v6
# ---------------------------------------------------------------------------


class TestSourceInfoDB:
    @pytest.fixture()
    def state(self, tmp_path):
        from klemma.state import StateManager
        return StateManager(tmp_path / "test.db")

    def test_source_info_persisted(self, state):
        """After update_source_info(), DB has title/authors/year."""
        state.register_sources(["smith2024_dl"])
        state.update_source_info(
            "smith2024_dl",
            title="Deep Learning for NLP",
            authors="Smith J., Jones K.",
            year=2024,
            abstract="We survey deep learning...",
            doi="10.1234/test",
        )

        src = state.get_source("smith2024_dl")
        assert src["title"] == "Deep Learning for NLP"
        assert src["authors"] == "Smith J., Jones K."
        assert src["year"] == 2024
        assert src["abstract"] == "We survey deep learning..."
        assert src["doi"] == "10.1234/test"

    def test_update_source_info_partial(self, state):
        """Only non-empty values are set, existing data preserved."""
        state.register_sources(["test_paper"])
        state.update_source_info("test_paper", title="Original Title")

        # Second update with only year — title should be preserved
        state.update_source_info("test_paper", year=2024)

        src = state.get_source("test_paper")
        assert src["title"] == "Original Title"
        assert src["year"] == 2024

    def test_migration_v6_adds_columns(self, tmp_path):
        """Schema migration v6 adds title/authors/year/abstract/doi to sources."""
        from klemma.state import StateManager

        db_path = tmp_path / "migration.db"
        sm = StateManager(db_path)

        # Verify columns exist by querying table info
        with sm._conn() as conn:
            cols = {row[1] for row in conn.execute("PRAGMA table_info(sources)")}

        for col in ("title", "authors", "year", "abstract", "doi"):
            assert col in cols, f"Column '{col}' missing after migration v6"


# ---------------------------------------------------------------------------
# Zotero local API (#70)
# ---------------------------------------------------------------------------


class TestZoteroAPI:
    def test_is_zotero_running_true(self):
        """POST to BBT returns 200 → True."""
        from klemma.literature.zotero_api import is_zotero_running

        with patch("klemma.literature.zotero_api.requests") as mock_req:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_req.post.return_value = mock_resp
            assert is_zotero_running() is True

    def test_is_zotero_running_false(self):
        """Connection error → False."""
        from klemma.literature.zotero_api import is_zotero_running

        with patch("klemma.literature.zotero_api.requests") as mock_req:
            mock_req.post.side_effect = requests.ConnectionError("refused")
            assert is_zotero_running() is False

    def test_create_zotero_item_success(self):
        """POST 201 → True."""
        from klemma.literature.zotero_api import create_zotero_item

        with patch("klemma.literature.zotero_api.requests") as mock_req:
            mock_resp = MagicMock()
            mock_resp.status_code = 201
            mock_req.post.return_value = mock_resp

            result = create_zotero_item(
                "Deep Learning", "Smith J., Jones K.", 2024,
                "10.1234/test", "Abstract text", None,
            )

        assert result is True
        call_args = mock_req.post.call_args
        payload = call_args.kwargs.get("json") or call_args[1].get("json")
        item = payload["items"][0]
        assert item["title"] == "Deep Learning"
        assert item["DOI"] == "10.1234/test"

    def test_create_zotero_item_parse_authors(self):
        """Authors string parsed into Zotero creators array."""
        from klemma.literature.zotero_api import _parse_authors

        creators = _parse_authors("Smith J., Jones K.L.")
        assert len(creators) == 2
        assert creators[0]["lastName"] == "Smith"
        assert creators[0]["firstName"] == "J."
        assert creators[1]["lastName"] == "Jones"
        assert creators[1]["firstName"] == "K.L."

    def test_get_bbt_citekey_success(self):
        """BBT JSON-RPC returns citekey on first attempt."""
        from klemma.literature.zotero_api import get_bbt_citekey

        with patch("klemma.literature.zotero_api.requests") as mock_req, \
             patch("klemma.literature.zotero_api.time"):
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {
                "result": [{"citekey": "smith2024DeepLearning"}],
            }
            mock_req.post.return_value = mock_resp

            citekey = get_bbt_citekey("Deep Learning")

        assert citekey == "smith2024DeepLearning"
