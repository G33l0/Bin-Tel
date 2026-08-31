"""Watchlists: membership, change detection after an update, and alerts."""

from __future__ import annotations

import sqlite3

import pytest
from sqlalchemy import text

from app.core.errors import ValidationError
from app.models.user_entities import WatchTargetType
from app.services.change_detection import ChangeDetectionService
from app.services.watchlist_service import WatchlistService


@pytest.fixture
def watchlists(user_store, database_path):
    return WatchlistService(user_store, ChangeDetectionService(database_path))


def _a_bin(database_path) -> str:
    connection = sqlite3.connect(database_path)
    try:
        return str(connection.execute("SELECT bin FROM bins LIMIT 1").fetchone()[0])
    finally:
        connection.close()


def _an_institution(database_path) -> str:
    connection = sqlite3.connect(database_path)
    try:
        return str(
            connection.execute("SELECT uid FROM institutions LIMIT 1").fetchone()[0]
        )
    finally:
        connection.close()


# -- watchlists ---------------------------------------------------------------


def test_creating_and_listing(watchlists):
    created = watchlists.create("Issuers to follow", "Cards we accept")
    listed = watchlists.list()

    assert [item.id for item in listed] == [created.id]
    assert listed[0].name == "Issuers to follow"
    assert listed[0].item_count == 0


def test_a_watchlist_needs_a_name(watchlists):
    with pytest.raises(ValidationError):
        watchlists.create("   ")


def test_names_are_unique(watchlists):
    watchlists.create("Duplicated")
    with pytest.raises(ValidationError):
        watchlists.create("duplicated")


def test_renaming_and_deleting(watchlists):
    created = watchlists.create("Before")
    watchlists.rename(created.id, "After", "New description")
    assert watchlists.list()[0].name == "After"

    watchlists.delete(created.id)
    assert watchlists.list() == []


# -- items --------------------------------------------------------------------


def test_adding_and_removing_a_watched_bin(watchlists, database_path):
    created = watchlists.create("BINs")
    digits = _a_bin(database_path)

    item = watchlists.add_item(created.id, WatchTargetType.BIN, digits, label="A BIN")
    assert item.has_snapshot, "the baseline must be captured on add"
    assert watchlists.is_watched(WatchTargetType.BIN, digits)
    assert watchlists.list()[0].item_count == 1

    watchlists.remove_item(item.id)
    assert not watchlists.is_watched(WatchTargetType.BIN, digits)


def test_the_same_target_cannot_be_added_twice(watchlists, database_path):
    created = watchlists.create("BINs")
    digits = _a_bin(database_path)
    watchlists.add_item(created.id, WatchTargetType.BIN, digits)
    with pytest.raises(ValidationError):
        watchlists.add_item(created.id, WatchTargetType.BIN, digits)


def test_an_empty_target_is_refused(watchlists):
    created = watchlists.create("BINs")
    with pytest.raises(ValidationError):
        watchlists.add_item(created.id, WatchTargetType.BIN, "  ")


def test_adding_to_a_deleted_watchlist_is_refused(watchlists, database_path):
    created = watchlists.create("Temporary")
    watchlists.delete(created.id)
    with pytest.raises(ValidationError):
        watchlists.add_item(created.id, WatchTargetType.BIN, _a_bin(database_path))


# -- change detection ---------------------------------------------------------


def test_no_change_produces_no_alerts(watchlists, database_path):
    created = watchlists.create("Quiet")
    watchlists.add_item(created.id, WatchTargetType.BIN, _a_bin(database_path))

    assert watchlists.scan_for_changes(from_version="1", to_version="2") == []
    assert watchlists.unread_count() == 0


def test_a_changed_field_raises_an_alert(watchlists, database_path):
    created = watchlists.create("Watched")
    digits = _a_bin(database_path)
    watchlists.add_item(created.id, WatchTargetType.BIN, digits, label="A BIN")

    connection = sqlite3.connect(database_path)
    try:
        connection.execute(
            "UPDATE bins SET status = 'inactive' WHERE bin = ?", (digits,)
        )
        connection.commit()
    finally:
        connection.close()

    alerts = watchlists.scan_for_changes(from_version="2026.01.1", to_version="2026.02.1")
    assert alerts
    assert any(alert.field == "status" for alert in alerts)
    assert all(alert.target_value == digits for alert in alerts)
    assert watchlists.unread_count() == len(alerts)


def test_a_removed_record_raises_an_alert(watchlists, database_path):
    created = watchlists.create("Watched")
    digits = _a_bin(database_path)
    watchlists.add_item(created.id, WatchTargetType.BIN, digits)

    connection = sqlite3.connect(database_path)
    try:
        connection.execute("DELETE FROM bin_institutions WHERE bin_id IN "
                           "(SELECT id FROM bins WHERE bin = ?)", (digits,))
        connection.execute("DELETE FROM bins WHERE bin = ?", (digits,))
        connection.commit()
    finally:
        connection.close()

    alerts = watchlists.scan_for_changes(to_version="2026.02.1")
    assert alerts
    from app.models.user_entities import ChangeType

    assert any(alert.change_type is ChangeType.BIN_REMOVED for alert in alerts)
    assert all(alert.severity == "warning" for alert in alerts)


def test_a_second_scan_compares_against_the_new_baseline(watchlists, database_path):
    created = watchlists.create("Watched")
    digits = _a_bin(database_path)
    watchlists.add_item(created.id, WatchTargetType.BIN, digits)

    connection = sqlite3.connect(database_path)
    try:
        connection.execute("UPDATE bins SET status = 'inactive' WHERE bin = ?", (digits,))
        connection.commit()
    finally:
        connection.close()

    assert watchlists.scan_for_changes(to_version="2026.02.1")
    assert watchlists.scan_for_changes(to_version="2026.03.1") == [], (
        "the same change must not be reported twice"
    )


def test_an_institution_can_be_watched(watchlists, database_path):
    created = watchlists.create("Institutions")
    uid = _an_institution(database_path)
    item = watchlists.add_item(created.id, WatchTargetType.INSTITUTION, uid)
    assert item.has_snapshot

    connection = sqlite3.connect(database_path)
    try:
        connection.execute(
            "UPDATE institutions SET website = 'changed.example' WHERE uid = ?", (uid,)
        )
        connection.commit()
    finally:
        connection.close()

    alerts = watchlists.scan_for_changes(to_version="2026.02.1")
    assert any(alert.field == "website" for alert in alerts)


# -- alerts -------------------------------------------------------------------


def test_alerts_can_be_acknowledged(watchlists, database_path):
    created = watchlists.create("Watched")
    digits = _a_bin(database_path)
    watchlists.add_item(created.id, WatchTargetType.BIN, digits)

    connection = sqlite3.connect(database_path)
    try:
        connection.execute("UPDATE bins SET status = 'inactive' WHERE bin = ?", (digits,))
        connection.commit()
    finally:
        connection.close()
    watchlists.scan_for_changes(to_version="2026.02.1")

    assert watchlists.unread_count() > 0
    assert watchlists.acknowledge() > 0
    assert watchlists.unread_count() == 0


def test_events_can_be_filtered_by_watchlist(watchlists, database_path):
    watched = watchlists.create("Watched")
    other = watchlists.create("Other")
    digits = _a_bin(database_path)
    watchlists.add_item(watched.id, WatchTargetType.BIN, digits)

    connection = sqlite3.connect(database_path)
    try:
        connection.execute("UPDATE bins SET status = 'inactive' WHERE bin = ?", (digits,))
        connection.commit()
    finally:
        connection.close()
    watchlists.scan_for_changes(to_version="2026.02.1")

    assert watchlists.events(watched.id)
    assert watchlists.events(other.id) == []


def test_clearing_events_removes_them(watchlists, database_path):
    created = watchlists.create("Watched")
    digits = _a_bin(database_path)
    watchlists.add_item(created.id, WatchTargetType.BIN, digits)

    connection = sqlite3.connect(database_path)
    try:
        connection.execute("UPDATE bins SET status = 'inactive' WHERE bin = ?", (digits,))
        connection.commit()
    finally:
        connection.close()
    watchlists.scan_for_changes(to_version="2026.02.1")

    assert watchlists.clear_events() > 0
    assert watchlists.events() == []


def test_deleting_a_watchlist_takes_its_items_with_it(watchlists, database_path):
    created = watchlists.create("Watched")
    watchlists.add_item(created.id, WatchTargetType.BIN, _a_bin(database_path))
    watchlists.delete(created.id)
    assert watchlists.item_count() == 0
