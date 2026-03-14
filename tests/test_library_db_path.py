"""Tests for library_db_path in KlemmaConfig (Phase 1B of #82).

Verifies that library_db_path can be set via klemmarc, system config, or
project config, and that _init_components() uses it when set.
"""

from contextlib import ExitStack
from pathlib import Path
from unittest.mock import MagicMock, patch

import yaml

from klemma.config import KlemmaConfig, resolve_effective_config

# ---------------------------------------------------------------------------
# Config model
# ---------------------------------------------------------------------------


class TestLibraryDbPathField:
    def test_default_is_none(self):
        cfg = KlemmaConfig()
        assert cfg.library_db_path is None

    def test_accepts_path_string(self):
        cfg = KlemmaConfig.model_validate({"library_db_path": "/data/library.db"})
        assert cfg.library_db_path == Path("/data/library.db")

    def test_accepts_path_object(self):
        p = Path("/data/library.db")
        cfg = KlemmaConfig.model_validate({"library_db_path": p})
        assert cfg.library_db_path == p

    def test_tilde_stored_as_is_expanded_at_use_time(self):
        """Pydantic stores ~ as-is; _init_components() must call .expanduser()."""
        cfg = KlemmaConfig.model_validate({"library_db_path": "~/shared/library.db"})
        # stored as-is — NOT expanded by Pydantic
        assert cfg.library_db_path == Path("~/shared/library.db")
        # expansion happens at use time via .expanduser()
        assert cfg.library_db_path.expanduser() != cfg.library_db_path


# ---------------------------------------------------------------------------
# Config resolution (klemmarc / system / project layers)
# ---------------------------------------------------------------------------


class TestLibraryDbPathResolution:
    def _setup_system(self, tmp_path, monkeypatch, content=""):
        system_home = tmp_path / ".klemma"
        system_home.mkdir()
        (system_home / "config.yaml").write_text(content, encoding="utf-8")
        monkeypatch.setattr("klemma.config.get_system_home", lambda: system_home)
        return system_home

    def _setup_project(self, tmp_path, content=""):
        project = tmp_path / "myproject"
        (project / ".klemma").mkdir(parents=True)
        (project / ".klemma" / "config.yaml").write_text(content, encoding="utf-8")
        return project

    def test_default_none_when_not_configured(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        self._setup_system(tmp_path, monkeypatch)
        project = self._setup_project(tmp_path)

        cfg, _, _ = resolve_effective_config([project])
        assert cfg.library_db_path is None

    def test_loaded_from_klemmarc(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        klemmarc = tmp_path / ".klemmarc.yaml"
        klemmarc.write_text(
            yaml.dump({"library_db_path": "/shared/library.db"}),
            encoding="utf-8",
        )
        self._setup_system(tmp_path, monkeypatch)
        project = self._setup_project(tmp_path)

        cfg, _, _ = resolve_effective_config([project])
        assert cfg.library_db_path == Path("/shared/library.db")

    def test_loaded_from_system_config(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        self._setup_system(
            tmp_path, monkeypatch,
            content=yaml.dump({"library_db_path": "/system/library.db"}),
        )
        project = self._setup_project(tmp_path)

        cfg, _, _ = resolve_effective_config([project])
        assert cfg.library_db_path == Path("/system/library.db")

    def test_project_config_overrides_klemmarc(self, tmp_path, monkeypatch):
        """Project-level library_db_path wins over klemmarc."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        klemmarc = tmp_path / ".klemmarc.yaml"
        klemmarc.write_text(
            yaml.dump({"library_db_path": "/klemmarc/library.db"}),
            encoding="utf-8",
        )
        self._setup_system(tmp_path, monkeypatch)
        project = self._setup_project(
            tmp_path,
            content=yaml.dump({"library_db_path": "/project/library.db"}),
        )

        cfg, _, _ = resolve_effective_config([project])
        assert cfg.library_db_path == Path("/project/library.db")

    def test_system_config_overrides_klemmarc(self, tmp_path, monkeypatch):
        """System config wins over klemmarc for library_db_path."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        klemmarc = tmp_path / ".klemmarc.yaml"
        klemmarc.write_text(
            yaml.dump({"library_db_path": "/klemmarc/library.db"}),
            encoding="utf-8",
        )
        self._setup_system(
            tmp_path, monkeypatch,
            content=yaml.dump({"library_db_path": "/system/library.db"}),
        )
        project = self._setup_project(tmp_path)

        cfg, _, _ = resolve_effective_config([project])
        assert cfg.library_db_path == Path("/system/library.db")


# ---------------------------------------------------------------------------
# _init_components() uses library_db_path
# ---------------------------------------------------------------------------


class TestInitComponentsUsesLibraryDbPath:
    """_init_components() must pass cfg.library_db_path to LocalPaperStore/LocalUserLibrary."""

    def _make_context_patches(self, system_home, project_root, cfg):
        """Return context managers that stub out everything except the stores."""
        state_mock = MagicMock()
        state_mock.get_coverage_stats.return_value = {"total_sources": 0}
        state_cls_mock = MagicMock(return_value=state_mock)
        return (
            patch("klemma.cli.ensure_system_home", return_value=system_home),
            patch("klemma.cli.discover_project_chain", return_value=[project_root]),
            patch("klemma.cli.resolve_effective_config", return_value=(cfg, MagicMock(), project_root)),
            patch("klemma.cli.StateManager", state_cls_mock),
            patch("klemma.cli.VaultAdapter"),
            patch("klemma.cli.create_library"),
            patch("klemma.cli.create_embeddings", return_value=None),
            patch("klemma.cli.load_project_context", return_value=""),
            patch("klemma.cli.load_available_tags", return_value=[]),
        )

    def test_uses_custom_library_db_path(self, tmp_path):
        from klemma.cli import _init_components

        custom_db = tmp_path / "custom" / "library.db"
        cfg = KlemmaConfig.model_validate({"library_db_path": str(custom_db)})
        system_home = tmp_path / ".klemma"
        system_home.mkdir()
        project_root = tmp_path / "proj"
        (project_root / ".klemma").mkdir(parents=True)

        paper_store_calls = []
        user_library_calls = []

        def fake_paper_store(db_path):
            paper_store_calls.append(db_path)
            m = MagicMock()
            m.count_sources = MagicMock(return_value=0)
            return m

        def fake_user_library(db_path):
            user_library_calls.append(db_path)
            return MagicMock()

        store_patches = [
            patch("klemma.stores.LocalPaperStore", fake_paper_store),
            patch("klemma.stores.LocalUserLibrary", fake_user_library),
            patch("klemma.stores.LocalProjectStore", return_value=MagicMock(count_sources=lambda: 0)),
        ]
        with ExitStack() as stack:
            for p in self._make_context_patches(system_home, project_root, cfg):
                stack.enter_context(p)
            for p in store_patches:
                stack.enter_context(p)
            _init_components()

        assert len(paper_store_calls) == 1
        assert paper_store_calls[0] == Path(custom_db)
        assert user_library_calls[0] == Path(custom_db)

    def test_resolves_relative_path_against_system_home(self, tmp_path):
        """Relative library_db_path must be resolved against system_home, not CWD."""
        from klemma.cli import _init_components

        cfg = KlemmaConfig.model_validate({"library_db_path": "data/library.db"})
        system_home = tmp_path / ".klemma"
        system_home.mkdir()
        project_root = tmp_path / "proj"
        (project_root / ".klemma").mkdir(parents=True)

        paper_store_calls = []

        def fake_paper_store(db_path):
            paper_store_calls.append(db_path)
            m = MagicMock()
            m.count_sources = MagicMock(return_value=0)
            return m

        store_patches = [
            patch("klemma.stores.LocalPaperStore", fake_paper_store),
            patch("klemma.stores.LocalUserLibrary", return_value=MagicMock()),
            patch("klemma.stores.LocalProjectStore", return_value=MagicMock(count_sources=lambda: 0)),
        ]
        with ExitStack() as stack:
            for p in self._make_context_patches(system_home, project_root, cfg):
                stack.enter_context(p)
            for p in store_patches:
                stack.enter_context(p)
            _init_components()

        assert len(paper_store_calls) == 1
        assert paper_store_calls[0] == system_home / "data" / "library.db"

    def test_falls_back_to_system_home_when_not_configured(self, tmp_path):
        from klemma.cli import _init_components

        cfg = KlemmaConfig()  # library_db_path is None
        system_home = tmp_path / ".klemma"
        system_home.mkdir()
        project_root = tmp_path / "proj"
        (project_root / ".klemma").mkdir(parents=True)

        paper_store_calls = []

        def fake_paper_store(db_path):
            paper_store_calls.append(db_path)
            m = MagicMock()
            m.count_sources = MagicMock(return_value=0)
            return m

        store_patches = [
            patch("klemma.stores.LocalPaperStore", fake_paper_store),
            patch("klemma.stores.LocalUserLibrary", return_value=MagicMock()),
            patch("klemma.stores.LocalProjectStore", return_value=MagicMock(count_sources=lambda: 0)),
        ]
        with ExitStack() as stack:
            for p in self._make_context_patches(system_home, project_root, cfg):
                stack.enter_context(p)
            for p in store_patches:
                stack.enter_context(p)
            _init_components()

        assert len(paper_store_calls) == 1
        assert paper_store_calls[0] == system_home / "library.db"
