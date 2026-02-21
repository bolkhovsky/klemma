"""Tests for interactive init wizard: discovery functions and init_project with values."""

import yaml


class TestDiscoverObsidianVault:
    def test_finds_vault_in_documents(self, tmp_path, monkeypatch):
        vault = tmp_path / "Documents" / "Obsidian Vault"
        (vault / ".obsidian").mkdir(parents=True)
        monkeypatch.setattr("klemma.discovery.Path.home", lambda: tmp_path)

        from klemma.discovery import discover_obsidian_vault

        result = discover_obsidian_vault()
        assert result == vault

    def test_finds_vault_in_documents_subdir(self, tmp_path, monkeypatch):
        vault = tmp_path / "Documents" / "MyNotes"
        (vault / ".obsidian").mkdir(parents=True)
        monkeypatch.setattr("klemma.discovery.Path.home", lambda: tmp_path)

        from klemma.discovery import discover_obsidian_vault

        result = discover_obsidian_vault()
        assert result == vault

    def test_returns_none_when_no_vault(self, tmp_path, monkeypatch):
        (tmp_path / "Documents").mkdir()
        monkeypatch.setattr("klemma.discovery.Path.home", lambda: tmp_path)

        from klemma.discovery import discover_obsidian_vault

        result = discover_obsidian_vault()
        assert result is None


class TestDiscoverZoteroStorage:
    def test_finds_storage(self, tmp_path, monkeypatch):
        storage = tmp_path / "Zotero" / "storage"
        storage.mkdir(parents=True)
        monkeypatch.setattr("klemma.discovery.Path.home", lambda: tmp_path)

        from klemma.discovery import discover_zotero_storage

        result = discover_zotero_storage()
        assert result == storage

    def test_returns_none_when_missing(self, tmp_path, monkeypatch):
        monkeypatch.setattr("klemma.discovery.Path.home", lambda: tmp_path)

        from klemma.discovery import discover_zotero_storage

        result = discover_zotero_storage()
        assert result is None


class TestDiscoverBbtJson:
    def test_finds_bbt_export(self, tmp_path, monkeypatch):
        zotero = tmp_path / "Zotero"
        zotero.mkdir()
        bbt_file = zotero / "My Library.json"
        bbt_file.write_text('[{"itemType": "journalArticle", "citationKey": "smith2024"}]')
        monkeypatch.setattr("klemma.discovery.Path.home", lambda: tmp_path)

        from klemma.discovery import discover_bbt_json

        result = discover_bbt_json()
        assert result == bbt_file

    def test_returns_none_when_no_zotero(self, tmp_path, monkeypatch):
        monkeypatch.setattr("klemma.discovery.Path.home", lambda: tmp_path)

        from klemma.discovery import discover_bbt_json

        result = discover_bbt_json()
        assert result is None

    def test_skips_non_bbt_json(self, tmp_path, monkeypatch):
        zotero = tmp_path / "Zotero"
        zotero.mkdir()
        (zotero / "settings.json").write_text('{"setting": true}')
        monkeypatch.setattr("klemma.discovery.Path.home", lambda: tmp_path)

        from klemma.discovery import discover_bbt_json

        result = discover_bbt_json()
        assert result is None


class TestDetectLanguage:
    def test_detects_from_lang(self, monkeypatch):
        monkeypatch.setenv("LANG", "en_US.UTF-8")
        monkeypatch.delenv("LC_ALL", raising=False)
        monkeypatch.delenv("LC_MESSAGES", raising=False)

        from klemma.discovery import detect_language

        assert detect_language() == "en"

    def test_detects_russian(self, monkeypatch):
        monkeypatch.setenv("LANG", "ru_RU.UTF-8")
        monkeypatch.delenv("LC_ALL", raising=False)
        monkeypatch.delenv("LC_MESSAGES", raising=False)

        from klemma.discovery import detect_language

        assert detect_language() == "ru"

    def test_defaults_to_ru(self, monkeypatch):
        monkeypatch.delenv("LANG", raising=False)
        monkeypatch.delenv("LC_ALL", raising=False)
        monkeypatch.delenv("LC_MESSAGES", raising=False)

        from klemma.discovery import detect_language

        assert detect_language() == "ru"


class TestInitProjectWithValues:
    def test_creates_valid_yaml_config(self, tmp_path):
        from klemma.setup import InitValues, init_project

        values = InitValues(
            project_type="paper",
            title="Test Paper",
            language="en",
            vault_path="/tmp/vault",
            zotero_storage="/tmp/Zotero/storage",
            zotero_library_json="/tmp/Zotero/lib.json",
        )
        result = init_project(tmp_path, project_type="paper", values=values)

        assert ".klemma/config.yaml" in result["created"]

        config_path = tmp_path / ".klemma" / "config.yaml"
        cfg = yaml.safe_load(config_path.read_text())

        assert cfg["project"]["type"] == "paper"
        assert cfg["project"]["title"] == "Test Paper"
        assert cfg["ai"]["language"] == "en"
        assert cfg["zotero"]["library_json"] == "/tmp/Zotero/lib.json"
        assert cfg["zotero"]["storage_path"] == "/tmp/Zotero/storage"
        assert cfg["obsidian"]["vault_path"] == "/tmp/vault"

    def test_creates_klemma_md_with_title(self, tmp_path):
        from klemma.setup import InitValues, init_project

        values = InitValues(title="My Paper on Ice")
        init_project(tmp_path, values=values)

        md = (tmp_path / "KLEMMA.md").read_text()
        assert "My Paper on Ice" in md

    def test_omits_empty_paths(self, tmp_path):
        from klemma.setup import InitValues, init_project

        values = InitValues(title="Minimal")
        init_project(tmp_path, values=values)

        cfg = yaml.safe_load((tmp_path / ".klemma" / "config.yaml").read_text())
        assert "zotero" not in cfg
        assert "obsidian" not in cfg

    def test_no_values_uses_template(self, tmp_path):
        from klemma.setup import init_project

        init_project(tmp_path, project_type="thesis")

        cfg_text = (tmp_path / ".klemma" / "config.yaml").read_text()
        # Template has placeholder paths
        assert "type: thesis" in cfg_text

    def test_paper_with_description_and_keywords(self, tmp_path):
        from klemma.setup import InitValues, init_project

        values = InitValues(
            project_type="paper",
            title="Ice Sheet Paper",
            description="Analysis of ice sheet dynamics under warming",
            keywords=["ice sheets", "climate", "GrIS"],
        )
        init_project(tmp_path, project_type="paper", values=values)

        cfg = yaml.safe_load((tmp_path / ".klemma" / "config.yaml").read_text())
        assert cfg["project"]["description"] == "Analysis of ice sheet dynamics under warming"
        assert cfg["project"]["priority_terms"] == ["ice sheets", "climate", "GrIS"]

        md = (tmp_path / "KLEMMA.md").read_text()
        assert "Analysis of ice sheet dynamics" in md
        assert "ice sheets, climate, GrIS" in md


class TestClaudeSkillsSetup:
    def test_symlinks_skills_on_init(self, tmp_path):
        from klemma.setup import _EXAMPLES_DIR, init_project

        skills_source = _EXAMPLES_DIR / ".claude" / "skills"
        if not skills_source.is_dir():
            return  # skip if skills not present (CI)

        result = init_project(tmp_path)

        skill_names = [d.name for d in skills_source.iterdir() if d.is_dir() and not d.name.startswith(".")]
        for name in skill_names:
            target = tmp_path / ".claude" / "skills" / name
            assert target.is_symlink(), f"Expected symlink for {name}"
            assert target.resolve() == (skills_source / name).resolve()
            assert f".claude/skills/{name}" in result["created"]

    def test_skips_existing_skill_symlinks(self, tmp_path):
        from klemma.setup import _EXAMPLES_DIR, init_project

        skills_source = _EXAMPLES_DIR / ".claude" / "skills"
        if not skills_source.is_dir():
            return

        # First init creates symlinks
        init_project(tmp_path)
        # Second init skips them
        result = init_project(tmp_path)

        skill_names = [d.name for d in skills_source.iterdir() if d.is_dir() and not d.name.startswith(".")]
        for name in skill_names:
            assert f".claude/skills/{name}" in result["skipped"]


class TestDiscoverRelevantSources:
    def _make_entry(self, title="", abstract="", keywords=""):
        """Create a mock ZoteroEntry-like object."""

        class FakeEntry:
            pass

        e = FakeEntry()
        e.title = title
        e.abstract = abstract
        e.keywords = keywords
        return e

    def test_matches_by_title(self, tmp_path):
        from klemma.discovery import discover_relevant_sources

        entries = {
            "smith2024": self._make_entry(title="Ice sheet dynamics in Greenland"),
            "jones2023": self._make_entry(title="Machine learning review"),
        }
        # No vault notes
        (tmp_path / "References").mkdir()

        results = discover_relevant_sources(
            vault_path=tmp_path,
            notes_folder="References",
            library_entries=entries,
            keywords=["ice sheet"],
        )
        assert len(results) == 1
        assert results[0]["citekey"] == "smith2024"

    def test_matches_by_abstract(self, tmp_path):
        from klemma.discovery import discover_relevant_sources

        entries = {
            "doe2024": self._make_entry(
                title="A Study", abstract="Climate change impacts on polar regions"
            ),
        }
        (tmp_path / "Refs").mkdir()

        results = discover_relevant_sources(
            vault_path=tmp_path,
            notes_folder="Refs",
            library_entries=entries,
            keywords=["climate"],
        )
        assert len(results) == 1

    def test_matches_by_vault_tags(self, tmp_path):
        from klemma.discovery import discover_relevant_sources

        # Create vault note with tags
        refs = tmp_path / "References"
        refs.mkdir()
        (refs / "@paper2024.md").write_text(
            "---\ntags:\n  - glaciology\n  - ice\n---\nSome content\n"
        )

        entries = {
            "paper2024": self._make_entry(title="Unrelated Title"),
        }

        results = discover_relevant_sources(
            vault_path=tmp_path,
            notes_folder="References",
            library_entries=entries,
            keywords=["glaciology"],
        )
        assert len(results) == 1
        assert results[0]["score"] >= 3  # vault tag match

    def test_returns_empty_without_keywords(self, tmp_path):
        from klemma.discovery import discover_relevant_sources

        results = discover_relevant_sources(
            vault_path=tmp_path,
            notes_folder="Refs",
            library_entries={"a": self._make_entry(title="Something")},
            keywords=[],
        )
        assert results == []

    def test_scores_sorted_descending(self, tmp_path):
        from klemma.discovery import discover_relevant_sources

        entries = {
            "weak": self._make_entry(abstract="ice sheet mentioned once"),
            "strong": self._make_entry(
                title="Ice sheet dynamics", abstract="ice sheet retreat analysis"
            ),
        }
        (tmp_path / "Refs").mkdir()

        results = discover_relevant_sources(
            vault_path=tmp_path,
            notes_folder="Refs",
            library_entries=entries,
            keywords=["ice sheet"],
        )
        assert len(results) == 2
        assert results[0]["citekey"] == "strong"
        assert results[0]["score"] > results[1]["score"]

    def test_uses_description_words(self, tmp_path):
        from klemma.discovery import discover_relevant_sources

        entries = {
            "a": self._make_entry(title="Greenland ice sheet modeling"),
        }
        (tmp_path / "Refs").mkdir()

        results = discover_relevant_sources(
            vault_path=tmp_path,
            notes_folder="Refs",
            library_entries=entries,
            keywords=[],
            description="Modeling ice sheet dynamics in Greenland",
        )
        assert len(results) == 1
