"""The first launch builds from your list. It must never need a server.

The bug these tests exist to keep dead: a welcome screen that contacts a
download service before the user can do anything, and refuses to continue when
that service is unreachable. Bin-Tel builds its database from a CSV on this
machine, so the first run needs no network at all.

Synthetic BINs throughout.
"""

from __future__ import annotations

import time

import pytest
from PyQt6.QtCore import QThreadPool

from app.core.config import ConfigManager, UpdateCheckMode
from app.core.context import AppContext
from app.services.bin_list import BIN_LIST_FILENAME, seed_bin_list

pytestmark = pytest.mark.gui


@pytest.fixture
def first_run(paths, qapp):
    """A window over an empty data directory, as a new install would be."""
    from app.ui.windows.first_run_window import FirstRunWindow

    config = ConfigManager(paths)
    config.load()
    config.settings.database.check_mode = UpdateCheckMode.MANUAL
    config.settings.database.automatic_updates = False
    context = AppContext(config, paths)

    window = FirstRunWindow(context)
    yield window, context, config
    window.close()
    QThreadPool.globalInstance().waitForDone(5000)
    context.shutdown()


def settle(qapp, window, seconds: float = 30.0) -> None:
    """Wait for the build to actually finish, rather than spinning."""
    deadline = time.time() + seconds
    while time.time() < deadline and not window.database_ready:
        qapp.processEvents()
        time.sleep(0.02)
    QThreadPool.globalInstance().waitForDone(5000)
    qapp.processEvents()


def add_bins(config, *rows: str) -> None:
    path = config.bin_list_path()
    path.write_text(path.read_text() + "".join(f"{row}\n" for row in rows), encoding="utf-8")


# ---------------------------------------------------------------------------
# No server is contacted
# ---------------------------------------------------------------------------


def test_the_first_run_needs_no_network(first_run, qapp):
    """The whole point: an unreachable server must not block the first launch."""
    window, context, config = first_run

    def explode(*_args, **_kwargs):  # pragma: no cover - must never be called
        raise AssertionError("the first run contacted the update service")

    context.updates.check = explode  # type: ignore[method-assign]

    add_bins(config, "410000,Cascade Federal Bank")
    window.inspect_list()
    qapp.processEvents()
    assert window.primary_button.isEnabled()

    window._on_primary()
    settle(qapp, window)
    assert window.database_ready


def test_the_primary_action_builds_rather_than_downloads(first_run, qapp):
    window, _, config = first_run
    add_bins(config, "410000,Cascade Federal Bank")
    window.inspect_list()
    qapp.processEvents()
    assert window.primary_button.text() == "Build my database"


# ---------------------------------------------------------------------------
# A brand new install
# ---------------------------------------------------------------------------


def test_a_list_is_created_where_the_user_can_edit_it(first_run, paths):
    """A packaged app unpacks to a temporary folder, so it cannot be there."""
    _, _, config = first_run
    path = config.bin_list_path()
    assert path.exists()
    assert path.name == BIN_LIST_FILENAME
    assert path.parent == paths.data_dir
    path.write_text(path.read_text() + "410000,Test Bank\n", encoding="utf-8")


def test_an_empty_list_explains_itself_and_offers_a_way_forward(first_run, qapp):
    window, _, _ = first_run
    window.inspect_list()
    qapp.processEvents()
    assert not window.primary_button.isEnabled()
    message = window.banner.message_label.text().lower()
    assert "no rows" in message or "add rows" in message
    for button in (window.choose_button, window.open_list_button):
        assert button.isEnabled()


def test_the_count_of_buildable_bins_is_shown(first_run, qapp):
    window, _, config = first_run
    add_bins(config, "410000,Cascade Bank", "520001,Harbor Mutual", "37828224,Apex Charge")
    window.inspect_list()
    qapp.processEvents()
    assert window.records_row.value_widget.text() == "3"
    assert window.primary_button.isEnabled()


def test_bad_rows_are_reported_but_do_not_block_the_build(first_run, qapp):
    window, _, config = first_run
    add_bins(config, "410000,Cascade Bank", "not-a-bin,Nobody")
    window.inspect_list()
    qapp.processEvents()
    assert window.primary_button.isEnabled()
    assert "skipped" in window.banner.message_label.text().lower()


# ---------------------------------------------------------------------------
# Building
# ---------------------------------------------------------------------------


def test_building_produces_a_usable_database(first_run, qapp):
    window, context, config = first_run
    add_bins(config, "410000,Cascade Federal Bank", "520001,Harbor Mutual")
    window.inspect_list()
    qapp.processEvents()
    window._on_primary()
    settle(qapp, window)

    assert window.database_ready
    assert window.primary_button.text() == "Get Started"
    assert context.database.is_installed
    assert window.version_row.value_widget.text() != "Not built yet"


def test_a_csv_elsewhere_on_disk_can_be_chosen(first_run, qapp, tmp_path):
    """The list does not have to live where Bin-Tel put it."""
    window, _, config = first_run
    elsewhere = tmp_path / "my-own-bins.csv"
    elsewhere.write_text("bin,bank\n414720,Chase Bank\n", encoding="utf-8")

    config.set_bin_list_path(elsewhere)
    config.save_settings()
    window.inspect_list()
    qapp.processEvents()

    assert window.list_path == elsewhere
    assert window.records_row.value_widget.text() == "1"
    assert window.primary_button.isEnabled()


def test_the_chosen_csv_is_remembered(first_run, paths, tmp_path):
    _, _, config = first_run
    elsewhere = tmp_path / "elsewhere.csv"
    elsewhere.write_text("bin,bank\n414720,Chase Bank\n", encoding="utf-8")
    config.set_bin_list_path(elsewhere)
    config.save_settings()

    reloaded = ConfigManager(paths)
    reloaded.load()
    assert reloaded.bin_list_path() == elsewhere


def test_pointing_back_at_the_default_works(first_run):
    _, _, config = first_run
    config.set_bin_list_path(None)
    assert config.bin_list_path().name == BIN_LIST_FILENAME


# ---------------------------------------------------------------------------
# Seeding
# ---------------------------------------------------------------------------


def test_seeding_never_overwrites_an_existing_list(tmp_path):
    path = tmp_path / "bin-list.csv"
    path.write_text("bin,bank\n410000,Mine\n", encoding="utf-8")
    seed_bin_list(path)
    assert "410000,Mine" in path.read_text(encoding="utf-8")


def test_a_seeded_list_is_readable_and_writable(tmp_path):
    path = seed_bin_list(tmp_path / "nested" / "bin-list.csv")
    assert path.exists()
    assert "bin,bank" in path.read_text(encoding="utf-8")
    path.write_text(path.read_text() + "410000,Test\n", encoding="utf-8")


def test_a_large_list_says_how_long_the_build_will_take(qtbot, context, tmp_path):
    """A shipped install arrives with 343,000 rows. Silence looks like a hang."""
    from app.ui.windows.first_run_window import FirstRunWindow

    listing = tmp_path / "bin-list.csv"
    rows = "\n".join(f"{410000 + n},Bank {n}" for n in range(25_000))
    listing.write_text(f"bin,bank\n{rows}\n", encoding="utf-8")
    context.config.set_bin_list_path(listing)

    window = FirstRunWindow(context)
    qtbot.addWidget(window)
    window.inspect_list()

    assert window.primary_button.isEnabled()
    assert "minute" in window.banner.message_label.text()


def test_a_small_list_says_nothing_about_time(qtbot, context, tmp_path):
    from app.ui.windows.first_run_window import FirstRunWindow

    listing = tmp_path / "bin-list.csv"
    listing.write_text("bin,bank\n410000,Cascade Bank\n", encoding="utf-8")
    context.config.set_bin_list_path(listing)

    window = FirstRunWindow(context)
    qtbot.addWidget(window)
    window.inspect_list()

    assert window.primary_button.isEnabled()
    assert "minute" not in window.banner.message_label.text()
