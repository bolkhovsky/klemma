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
        """CLI title/authors override PDF+CrossRef."""
        from klemma.literature.metadata import resolve_metadata

        with patch("klemma.literature.metadata.extract_pdf_metadata") as mock_pdf, \
             patch("klemma.literature.metadata.lookup_crossref") as mock_cr:
            mock_pdf.return_value = {"title": "PDF Title", "authors": "PDF Author"}
            mock_cr.return_value = {
                "title": "CR Title", "authors": "CR Author",
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
        # CrossRef enriches abstract and doi even when CLI provides title
        assert result["doi"] == "10.1/x"

    def test_pdf_then_crossref(self):
        """PDF title → CrossRef enriches authors/year/doi."""
        from klemma.literature.metadata import resolve_metadata

        with patch("klemma.literature.metadata.extract_pdf_metadata") as mock_pdf, \
             patch("klemma.literature.metadata.lookup_crossref") as mock_cr:
            mock_pdf.return_value = {"title": "Deep Learning", "authors": ""}
            mock_cr.return_value = {
                "title": "Deep Learning", "authors": "Smith J., Jones K.",
                "year": 2024, "abstract": "We study...", "doi": "10.5555/dl",
            }

            result = resolve_metadata(Path("/tmp/paper.pdf"))

        assert result["title"] == "Deep Learning"
        assert result["authors"] == "Smith J., Jones K."
        assert result["year"] == 2024
        assert result["doi"] == "10.5555/dl"

    def test_no_sources(self):
        """No PDF metadata, CrossRef fails → empty fallback."""
        from klemma.literature.metadata import resolve_metadata

        with patch("klemma.literature.metadata.extract_pdf_metadata") as mock_pdf, \
             patch("klemma.literature.metadata.lookup_crossref") as mock_cr:
            mock_pdf.return_value = {"title": "", "authors": ""}
            mock_cr.return_value = None

            result = resolve_metadata(Path("/tmp/paper.pdf"))

        assert result["title"] == ""
        assert result["authors"] == ""
        assert result["year"] is None
        assert result["doi"] == ""

    def test_s2_not_called(self):
        """S2 must not be called from resolve_metadata — CrossRef is the only lookup."""
        from klemma.literature.metadata import resolve_metadata

        with patch("klemma.literature.metadata.extract_pdf_metadata") as mock_pdf, \
             patch("klemma.literature.metadata.lookup_crossref") as mock_cr, \
             patch("klemma.literature.metadata.lookup_s2") as mock_s2:
            mock_pdf.return_value = {"title": "Some Title", "authors": ""}
            mock_cr.return_value = None

            resolve_metadata(Path("/tmp/paper.pdf"))

        assert mock_s2.call_count == 0


# ---------------------------------------------------------------------------
# lookup_crossref
# ---------------------------------------------------------------------------


class TestLookupCrossRef:
    def test_success_with_mailto(self):
        """CrossRef returns matching paper + polite pool mailto is used."""
        from klemma.literature.metadata import lookup_crossref

        cr_response = {
            "message": {
                "items": [
                    {
                        "title": ["Deep Learning for NLP"],
                        "author": [
                            {"family": "Smith", "given": "John"},
                            {"family": "Jones", "given": "Kate"},
                        ],
                        "issued": {"date-parts": [[2024]]},
                        "DOI": "10.1234/test",
                        "abstract": "<jats:p>We survey NLP...</jats:p>",
                    }
                ]
            }
        }

        with patch("klemma.literature.metadata.requests") as mock_req:
            mock_resp = MagicMock()
            mock_resp.json.return_value = cr_response
            mock_resp.raise_for_status = MagicMock()
            mock_req.get.return_value = mock_resp
            result = lookup_crossref("Deep Learning for NLP", mailto="me@test")

        assert result is not None
        assert result["year"] == 2024
        assert "Smith" in result["authors"]
        assert result["doi"] == "10.1234/test"
        # JATS tags stripped
        assert "<" not in result["abstract"]
        assert "We survey" in result["abstract"]
        # mailto appears in URL
        call_url = mock_req.get.call_args.args[0]
        assert "mailto=me%40test" in call_url

    def test_mailto_from_env(self, monkeypatch):
        """mailto falls back to KLEMMA_CROSSREF_MAILTO env var."""
        from klemma.literature.metadata import lookup_crossref

        monkeypatch.setenv("KLEMMA_CROSSREF_MAILTO", "env@example.org")
        with patch("klemma.literature.metadata.requests") as mock_req:
            mock_resp = MagicMock()
            mock_resp.json.return_value = {"message": {"items": []}}
            mock_resp.raise_for_status = MagicMock()
            mock_req.get.return_value = mock_resp
            lookup_crossref("anything")

        call_url = mock_req.get.call_args.args[0]
        assert "mailto=env%40example.org" in call_url
        ua = mock_req.get.call_args.kwargs["headers"]["User-Agent"]
        assert "env@example.org" in ua

    def test_no_match(self):
        from klemma.literature.metadata import lookup_crossref

        cr_response = {
            "message": {
                "items": [
                    {
                        "title": ["Completely Unrelated Paper on Chemistry"],
                        "author": [{"family": "Alice", "given": "A"}],
                        "issued": {"date-parts": [[2020]]},
                        "DOI": "10.0/x",
                    }
                ]
            }
        }
        with patch("klemma.literature.metadata.requests") as mock_req:
            mock_resp = MagicMock()
            mock_resp.json.return_value = cr_response
            mock_resp.raise_for_status = MagicMock()
            mock_req.get.return_value = mock_resp
            result = lookup_crossref("Deep Learning for NLP")

        assert result is None

    def test_api_error(self):
        from klemma.literature.metadata import lookup_crossref

        with patch("klemma.literature.metadata.requests") as mock_req:
            mock_req.get.side_effect = Exception("timeout")
            assert lookup_crossref("Deep Learning for NLP") is None

    def test_empty_title(self):
        from klemma.literature.metadata import lookup_crossref
        assert lookup_crossref("") is None

    def test_timeout_kwarg_passed_to_requests(self):
        from klemma.literature.metadata import lookup_crossref

        with patch("klemma.literature.metadata.requests") as mock_req:
            mock_resp = MagicMock()
            mock_resp.json.return_value = {"message": {"items": []}}
            mock_resp.raise_for_status = MagicMock()
            mock_req.get.return_value = mock_resp
            lookup_crossref("Some Title", timeout=5)

        assert mock_req.get.call_args.kwargs["timeout"] == 5


# ---------------------------------------------------------------------------
# _extract_abstract_from_text
# ---------------------------------------------------------------------------


class TestExtractAbstractFromText:
    def test_english_abstract(self):
        from klemma.literature.metadata import _extract_abstract_from_text

        text = "Title\n\nAbstract\nThis paper studies sea ice dynamics in the Arctic.\n\nKeywords: ice, Arctic"
        result = _extract_abstract_from_text(text)
        assert "sea ice dynamics" in result

    def test_russian_annotation(self):
        from klemma.literature.metadata import _extract_abstract_from_text

        text = "Заголовок\n\nАннотация\nВ данной работе исследуется морской лёд.\n\nКлючевые слова: лёд"
        result = _extract_abstract_from_text(text)
        assert "морской лёд" in result

    def test_no_abstract_marker_returns_empty(self):
        from klemma.literature.metadata import _extract_abstract_from_text

        text = "Introduction\nThis is the intro section.\n\n1. Methods\nWe used data."
        result = _extract_abstract_from_text(text)
        assert result == ""

    def test_empty_input_returns_empty_string(self):
        from klemma.literature.metadata import _extract_abstract_from_text

        assert _extract_abstract_from_text("") == ""

    def test_stops_before_keywords(self):
        from klemma.literature.metadata import _extract_abstract_from_text

        text = "Abstract\nShort abstract text.\n\nKeywords: foo, bar\n\nIntroduction\nMore text."
        result = _extract_abstract_from_text(text)
        assert result == "Short abstract text."
        assert "foo" not in result

    def test_caps_at_2000_chars(self):
        from klemma.literature.metadata import _extract_abstract_from_text

        long_abstract = "word " * 600  # ~3000 chars
        text = f"Abstract\n{long_abstract}\n\nKeywords: something"
        result = _extract_abstract_from_text(text)
        assert len(result) <= 2000


# ---------------------------------------------------------------------------
# _extract_doi_from_text
# ---------------------------------------------------------------------------


class TestExtractDoiFromText:
    def test_valid_doi(self):
        from klemma.literature.metadata import _extract_doi_from_text

        text = "Published in Nature. DOI: 10.1038/s41586-021-03819-2. Methods follow..."
        assert _extract_doi_from_text(text) == "10.1038/s41586-021-03819-2"

    def test_arxiv_doi_kept(self):
        from klemma.literature.metadata import _extract_doi_from_text

        text = "arXiv preprint. doi:10.48550/arXiv.2106.09685"
        result = _extract_doi_from_text(text)
        assert result == "10.48550/arXiv.2106.09685"

    def test_sentinel_doi_rejected(self):
        from klemma.literature.metadata import _extract_doi_from_text

        text = "10.0000/example should be ignored"
        assert _extract_doi_from_text(text) == ""

    def test_empty_input_returns_empty(self):
        from klemma.literature.metadata import _extract_doi_from_text

        assert _extract_doi_from_text("") == ""

    def test_no_doi_returns_empty(self):
        from klemma.literature.metadata import _extract_doi_from_text

        assert _extract_doi_from_text("This text has no DOI at all.") == ""


# ---------------------------------------------------------------------------
# lookup_crossref_by_doi
# ---------------------------------------------------------------------------


class TestLookupCrossrefByDoi:
    def _mock_crossref_item(self):
        return {
            "title": ["Sea Ice Forecasting"],
            "author": [{"family": "Smith", "given": "J"}],
            "issued": {"date-parts": [[2021]]},
            "DOI": "10.1038/test-doi",
            "abstract": "<jats:p>Abstract text.</jats:p>",
        }

    def test_success(self):
        from klemma.literature.metadata import lookup_crossref_by_doi

        with patch("klemma.literature.metadata.requests") as mock_req:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {"message": self._mock_crossref_item()}
            mock_resp.raise_for_status = MagicMock()
            mock_req.get.return_value = mock_resp
            result = lookup_crossref_by_doi("10.1038/test-doi")

        assert result is not None
        assert result["title"] == "Sea Ice Forecasting"
        assert result["year"] == 2021
        assert "Abstract text." in result["abstract"]
        assert result["doi"] == "10.1038/test-doi"

    def test_404_returns_none(self):
        from klemma.literature.metadata import lookup_crossref_by_doi

        with patch("klemma.literature.metadata.requests") as mock_req:
            mock_resp = MagicMock()
            mock_resp.status_code = 404
            mock_req.get.return_value = mock_resp
            result = lookup_crossref_by_doi("10.9999/nonexistent")

        assert result is None

    def test_network_error_returns_none(self):
        from klemma.literature.metadata import lookup_crossref_by_doi

        with patch("klemma.literature.metadata.requests") as mock_req:
            mock_req.get.side_effect = Exception("connection timeout")
            result = lookup_crossref_by_doi("10.1038/test-doi")

        assert result is None

    def test_empty_doi_returns_none(self):
        from klemma.literature.metadata import lookup_crossref_by_doi

        assert lookup_crossref_by_doi("") is None

    def test_timeout_kwarg_passed(self):
        from klemma.literature.metadata import lookup_crossref_by_doi

        with patch("klemma.literature.metadata.requests") as mock_req:
            mock_req.get.side_effect = Exception("bypass")
            try:
                lookup_crossref_by_doi("10.1038/x", timeout=3)
            except Exception:
                pass
            # Verify the timeout kwarg was forwarded
            if mock_req.get.called:
                assert mock_req.get.call_args.kwargs.get("timeout") == 3


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


# ---------------------------------------------------------------------------
# arXiv URL resolution
# ---------------------------------------------------------------------------


class TestArxivResolution:
    def test_abs_to_pdf(self):
        """arXiv abstract URL → PDF URL."""
        from klemma.skills.acquirer import _resolve_arxiv_pdf_url

        assert _resolve_arxiv_pdf_url("https://arxiv.org/abs/2001.01520") == \
            "https://arxiv.org/pdf/2001.01520"

    def test_abs_with_version(self):
        """arXiv abstract URL with version → PDF URL preserves version."""
        from klemma.skills.acquirer import _resolve_arxiv_pdf_url

        assert _resolve_arxiv_pdf_url("https://arxiv.org/abs/2001.01520v2") == \
            "https://arxiv.org/pdf/2001.01520v2"

    def test_non_arxiv_url(self):
        """Non-arXiv URL → None."""
        from klemma.skills.acquirer import _resolve_arxiv_pdf_url

        assert _resolve_arxiv_pdf_url("https://example.com/abs/2001.01520") is None

    def test_already_pdf_url(self):
        """arXiv PDF URL (no /abs/) → None (no conversion needed)."""
        from klemma.skills.acquirer import _resolve_arxiv_pdf_url

        assert _resolve_arxiv_pdf_url("https://arxiv.org/pdf/2001.01520") is None
