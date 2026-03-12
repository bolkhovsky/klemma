"""Tests for online source ingest — BibTeX parsing, web fetch, DB migration."""

from unittest.mock import MagicMock, patch

from klemma.literature.web import _strip_html, fetch_url_text
from klemma.skills.acquirer import parse_bibtex_online

# ── BibTeX @online parser ─────────────────────────────────────────────────


class TestParseBibtexOnline:
    def test_basic_entry(self):
        bib = """
@online{ipcc2023,
  title = {Climate Change 2023: Synthesis Report},
  author = {IPCC},
  year = {2023},
  url = {https://www.ipcc.ch/report/ar6/syr/},
}
"""
        records = parse_bibtex_online(bib)
        assert len(records) == 1
        r = records[0]
        assert r.citekey == "ipcc2023"
        assert r.title == "Climate Change 2023: Synthesis Report"
        assert r.year == 2023
        assert r.url == "https://www.ipcc.ch/report/ar6/syr/"

    def test_author_and_normalisation(self):
        bib = """
@online{smith2020,
  title = {A Study},
  author = {Smith, John and Doe, Jane},
  year = {2020},
  url = {https://example.com/},
}
"""
        records = parse_bibtex_online(bib)
        assert len(records) == 1
        r = records[0]
        assert "John Smith" in r.authors
        assert "Jane Doe" in r.authors

    def test_ignores_non_online_entries(self):
        bib = """
@article{jones2021,
  title = {An Article},
  author = {Jones, Alice},
  year = {2021},
}
@online{web2022,
  title = {A Web Page},
  year = {2022},
  url = {https://example.com/page},
}
"""
        records = parse_bibtex_online(bib)
        assert len(records) == 1
        assert records[0].citekey == "web2022"

    def test_multiple_online_entries(self):
        bib = """
@online{a2020, title = {First}, year = {2020}, url = {https://a.com/}}
@online{b2021, title = {Second}, year = {2021}, url = {https://b.com/}}
"""
        records = parse_bibtex_online(bib)
        assert len(records) == 2
        assert {r.citekey for r in records} == {"a2020", "b2021"}

    def test_empty_input(self):
        assert parse_bibtex_online("") == []

    def test_no_online_entries(self):
        bib = "@book{foo2020, title={Foo}, author={Bar}}"
        assert parse_bibtex_online(bib) == []

    def test_abstract_field(self):
        bib = """
@online{doc2022,
  title = {Documentation},
  abstract = {This is the abstract.},
  url = {https://docs.example.com/},
  year = {2022},
}
"""
        records = parse_bibtex_online(bib)
        assert records[0].abstract == "This is the abstract."

    def test_missing_year_is_none(self):
        bib = "@online{nodate, title = {No Date}, url = {https://example.com/}}"
        records = parse_bibtex_online(bib)
        assert records[0].year is None

    def test_case_insensitive_type(self):
        bib = "@ONLINE{upper2023, title = {Upper}, url = {https://u.com/}, year = {2023}}"
        records = parse_bibtex_online(bib)
        assert len(records) == 1
        assert records[0].citekey == "upper2023"


# ── HTML stripping ────────────────────────────────────────────────────────


class TestStripHtml:
    def test_basic_tags_removed(self):
        text = _strip_html("<p>Hello <b>world</b></p>")
        assert "Hello" in text
        assert "world" in text
        assert "<" not in text

    def test_script_and_style_content_removed(self):
        html = "<div>Content</div><script>var x = 1;</script><style>.cls{}</style>"
        text = _strip_html(html)
        assert "Content" in text
        assert "var x" not in text
        assert ".cls" not in text

    def test_html_entities_decoded(self):
        text = _strip_html("<p>Sea &amp; ice &lt;prediction&gt;</p>")
        assert "&amp;" not in text
        assert "Sea & ice" in text

    def test_empty_html(self):
        assert _strip_html("") == ""

    def test_plain_text_unchanged(self):
        text = _strip_html("no tags here")
        assert text == "no tags here"


# ── fetch_url_text ────────────────────────────────────────────────────────


class TestFetchUrlText:
    @patch("requests.get")
    def test_fetches_html_and_strips(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {"content-type": "text/html; charset=utf-8"}
        mock_resp.iter_content.return_value = [
            b"<html><body><p>Sea ice prediction methods</p></body></html>"
        ]
        mock_get.return_value = mock_resp

        text = fetch_url_text("https://example.com/paper")
        assert "Sea ice prediction methods" in text

    @patch("requests.get")
    def test_returns_empty_on_error(self, mock_get):
        mock_get.side_effect = Exception("Connection refused")
        text = fetch_url_text("https://example.com/bad")
        assert text == ""

    @patch("requests.get")
    def test_returns_empty_for_non_text_content_type(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {"content-type": "application/pdf"}
        mock_resp.iter_content.return_value = [b"%PDF-1.4"]
        mock_get.return_value = mock_resp

        text = fetch_url_text("https://example.com/doc.pdf")
        assert text == ""

    @patch("requests.get")
    def test_respects_max_chars(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {"content-type": "text/html"}
        content = b"<p>" + b"A" * 10000 + b"</p>"
        mock_resp.iter_content.return_value = [content]
        mock_get.return_value = mock_resp

        text = fetch_url_text("https://example.com/long", max_chars=100)
        assert len(text) <= 100


# ── DB schema v12 migration ───────────────────────────────────────────────


class TestSchemaV12:
    def test_url_and_source_type_columns_exist(self, tmp_path):
        """v12 migration adds url and source_type to sources."""

        from klemma.state import StateManager

        db_path = tmp_path / "test.db"
        state = StateManager(str(db_path))
        state._init_db()

        with state._conn() as conn:
            cols = {row[1] for row in conn.execute("PRAGMA table_info(sources)")}
        assert "url" in cols
        assert "source_type" in cols

    def test_register_online_source_roundtrip(self, tmp_path):
        """register_online_source() writes url/source_type and can be read back."""
        from klemma.state import StateManager

        db_path = tmp_path / "test.db"
        state = StateManager(str(db_path))
        state._init_db()

        state.register_online_source(
            citekey="ipcc2023",
            title="IPCC AR6 Synthesis",
            authors="IPCC",
            year=2023,
            url="https://www.ipcc.ch/report/ar6/syr/",
        )

        source = state.get_source("ipcc2023")
        assert source is not None
        assert source["title"] == "IPCC AR6 Synthesis"
        assert source["authors"] == "IPCC"
        assert source["year"] == 2023
        assert source["url"] == "https://www.ipcc.ch/report/ar6/syr/"
        assert source["source_type"] == "online"

    def test_register_online_source_idempotent(self, tmp_path):
        """Calling register_online_source twice is safe."""
        from klemma.state import StateManager

        db_path = tmp_path / "test.db"
        state = StateManager(str(db_path))
        state._init_db()

        state.register_online_source(
            citekey="web2022",
            title="Original Title",
            authors="Author A",
            year=2022,
            url="https://example.com/",
        )
        # Second call should not raise and preserves existing data
        state.register_online_source(
            citekey="web2022",
            title="",  # empty — should not overwrite
            authors="",
            year=None,
            url="",
        )
        source = state.get_source("web2022")
        assert source["title"] == "Original Title"
        assert source["source_type"] == "online"

    def test_update_source_info_url_and_source_type(self, tmp_path):
        """update_source_info() accepts url and source_type kwargs."""
        from klemma.state import StateManager

        db_path = tmp_path / "test.db"
        state = StateManager(str(db_path))
        state._init_db()

        state.register_sources(["mykey2024"])
        state.update_source_info(
            "mykey2024",
            title="My Title",
            url="https://example.com/my",
            source_type="online",
        )
        source = state.get_source("mykey2024")
        assert source["url"] == "https://example.com/my"
        assert source["source_type"] == "online"
