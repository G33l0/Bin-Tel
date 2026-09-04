"""Shared fixtures.

Every database these tests touch is generated. No real cardholder data, no
real payment-card numbers and no real licence keys appear anywhere in this
suite -- the issuer names come from the synthetic generator that ships in
``scripts/build_sample_database.py`` and are invented institutions.
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _load_sample_builder():
    """Import ``scripts/build_sample_database.py`` without a package."""
    spec = importlib.util.spec_from_file_location(
        "bintel_sample_builder", ROOT / "scripts" / "build_sample_database.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="session")
def sample_builder():
    return _load_sample_builder()


@pytest.fixture(scope="session")
def sample_package(tmp_path_factory, sample_builder) -> tuple[Path, Path]:
    """A generated database package and its manifest, built once per session."""
    output = tmp_path_factory.mktemp("package")
    return sample_builder.build(output, bin_count=240, version="2026.01.1", seed=7)


@pytest.fixture(scope="session")
def sample_database(sample_package) -> Path:
    return sample_package[0]


@pytest.fixture
def database_path(tmp_path, sample_database) -> Path:
    """A private copy of the generated database, safe to mutate."""
    destination = tmp_path / "bintel.sqlite"
    shutil.copy2(sample_database, destination)
    return destination


@pytest.fixture
def manager(database_path):
    from app.database.engine import DatabaseManager

    manager = DatabaseManager(database_path)
    manager.open()
    yield manager
    manager.close()


@pytest.fixture(autouse=True)
def _no_shipped_datasets(tmp_path_factory, monkeypatch):
    """Keep the repository's 343,000-row dataset out of every test's data dir.

    The application seeds its shipped datasets beside the user's list, and in a
    checkout the "bundle" is the repository — so without this every test that
    asks for a BIN list path would copy 26 MB and then try to build from it.
    Tests that exercise seeding point this at a fixture of their own.
    """
    from app.services import bin_list as module

    monkeypatch.setattr(
        module, "bundled_datasets_dir", lambda: tmp_path_factory.mktemp("nodata")
    )


@pytest.fixture
def paths(tmp_path, monkeypatch):
    """Application paths rooted in a temporary directory."""
    from app.core import paths as paths_module

    monkeypatch.setenv("BINTEL_DATA_DIR", str(tmp_path / "data"))
    paths_module.reset_paths_cache()
    resolved = paths_module.get_paths()
    yield resolved
    paths_module.reset_paths_cache()


@pytest.fixture
def config(paths):
    from app.core.config import ConfigManager

    manager = ConfigManager(paths)
    manager.load()
    return manager


@pytest.fixture
def user_store(tmp_path):
    from app.database.user_store import UserDataStore

    store = UserDataStore(tmp_path / "bintel-user.sqlite")
    store.open()
    yield store
    store.close()


@pytest.fixture
def context(config, paths, database_path):
    """A full application context pointed at a private copy of the database."""
    import shutil

    from app.core.context import AppContext

    shutil.copy2(database_path, config.database_path())
    context = AppContext(config=config, paths=paths)
    context.open_database()
    yield context
    context.shutdown()


@pytest.fixture(scope="session")
def scenario_database(tmp_path_factory) -> Path:
    """The lookup-engine scenarios, built once per session.

    Every record is synthetic — see :mod:`tests.fixtures.scenarios`.
    """
    from tests.fixtures import scenarios

    return scenarios.build(tmp_path_factory.mktemp("scenarios") / "scenarios.sqlite")


@pytest.fixture
def scenario_manager(tmp_path, scenario_database):
    """An open manager over a private copy of the scenario database."""
    from app.database.engine import DatabaseManager

    destination = tmp_path / "scenarios.sqlite"
    shutil.copy2(scenario_database, destination)
    manager = DatabaseManager(destination)
    manager.open()
    yield manager
    manager.close()


@pytest.fixture
def lookup(scenario_manager):
    from app.repositories.bin_repository import BinRepository
    from app.services.lookup_service import LookupService

    return LookupService(BinRepository(scenario_manager))


@pytest.fixture(scope="session")
def qapp():
    """One QApplication for the whole session; Qt allows no more."""
    from PyQt6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    return app
