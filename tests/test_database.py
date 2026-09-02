"""Schema, indexes, integrity, migrations, backups and version detection."""

from __future__ import annotations

import sqlite3

import pytest
from sqlalchemy import text

from app.core.constants import SCHEMA_VERSION
from app.database import migrations
from app.database.integrity import verify_database
from app.database.schema import (
    list_indexes,
    missing_tables,
    read_metadata,
    read_schema_version,
    rebuild_indexes,
)


def test_a_generated_package_passes_verification(database_path):
    report = verify_database(database_path)
    assert report.ok, report.errors
    assert report.errors == []
    assert report.bin_count > 0
    assert report.institution_count > 0
    assert report.schema_version == SCHEMA_VERSION


def test_no_expected_table_is_missing(manager):
    assert missing_tables(manager.engine) == []


def test_every_declared_index_exists(manager):
    shipped = set(list_indexes(manager.engine))
    assert shipped, "the package should ship with indexes"
    # Rebuilding reports the extra covering indexes; all of them must already
    # be present in a package the pipeline built.
    assert set(rebuild_indexes(manager.engine)) <= shipped


def test_rebuilding_indexes_is_idempotent(manager):
    first = sorted(rebuild_indexes(manager.engine))
    second = sorted(rebuild_indexes(manager.engine))
    assert first == second


def test_foreign_keys_hold(manager):
    with manager.session() as session:
        violations = session.execute(text("PRAGMA foreign_key_check")).all()
    assert violations == []


def test_metadata_records_the_published_version(manager):
    with manager.session() as session:
        metadata = read_metadata(session)
    assert metadata.get("version")
    assert metadata.get("schema_version") == str(SCHEMA_VERSION)


def test_schema_version_is_readable(manager):
    assert read_schema_version(manager.engine) == SCHEMA_VERSION


def test_a_truncated_file_fails_verification(tmp_path, database_path):
    broken = tmp_path / "broken.sqlite"
    broken.write_bytes(database_path.read_bytes()[: 4096 * 3])

    report = verify_database(broken)
    assert not report.ok
    assert report.errors


def test_a_file_that_is_not_a_database_fails_verification(tmp_path):
    impostor = tmp_path / "not-a-database.sqlite"
    impostor.write_text("this is plainly not SQLite", encoding="utf-8")

    report = verify_database(impostor)
    assert not report.ok


def test_a_missing_file_fails_verification(tmp_path):
    report = verify_database(tmp_path / "absent.sqlite")
    assert not report.ok


def test_a_corrupted_page_is_detected(tmp_path, database_path):
    corrupted = tmp_path / "corrupted.sqlite"
    data = bytearray(database_path.read_bytes())
    # Scribble over the middle of the file, well past the header.
    midpoint = len(data) // 2
    data[midpoint : midpoint + 2048] = b"\x00" * 2048
    corrupted.write_bytes(bytes(data))

    report = verify_database(corrupted)
    assert not report.ok


# -- migrations ---------------------------------------------------------------


def test_no_migration_is_pending_for_a_current_package(manager):
    assert migrations.pending(SCHEMA_VERSION) == []
    assert migrations.can_migrate(SCHEMA_VERSION)


def test_an_unknown_future_schema_cannot_be_migrated():
    assert not migrations.can_migrate(SCHEMA_VERSION + 5)


def test_an_unreadable_schema_version_cannot_be_migrated():
    assert not migrations.can_migrate(None)


def test_migrating_a_current_package_is_a_no_op(manager):
    result = migrations.migrate(manager.engine)
    assert result.applied == []
    assert result.from_version == SCHEMA_VERSION
    assert result.to_version == SCHEMA_VERSION


def test_a_package_without_the_card_level_column_gains_it(manager):
    """An older package simply has no tier recorded, which is the truth."""
    with manager.engine.begin() as connection:
        connection.exec_driver_sql("ALTER TABLE bins DROP COLUMN card_level")
    assert migrations.pending(SCHEMA_VERSION - 1)
    migrations._to_v3(manager.engine)
    columns = {
        row[1]
        for row in manager.engine.raw_connection()
        .cursor()
        .execute("PRAGMA table_info(bins)")
        .fetchall()
    }
    assert "card_level" in columns
    # Running it again is harmless: the column is already there.
    migrations._to_v3(manager.engine)


def test_optional_tables_are_created_when_absent(manager):
    with manager.engine.begin() as connection:
        connection.execute(text("DROP TABLE IF EXISTS database_statistics"))
    created = migrations.ensure_optional_tables(manager.engine)
    assert "database_statistics" in created
    assert migrations.ensure_optional_tables(manager.engine) == []


# -- backup and restore -------------------------------------------------------


@pytest.fixture
def backups(tmp_path, database_path):
    from app.services.backup_service import BackupService

    return BackupService(database_path, tmp_path / "backups", keep=3)


def _bin_count(path) -> int:
    connection = sqlite3.connect(path)
    try:
        return int(connection.execute("SELECT COUNT(*) FROM bins").fetchone()[0])
    finally:
        connection.close()


def test_a_backup_round_trips(backups, database_path):
    before = _bin_count(database_path)
    assert before > 0
    snapshot = backups.create(version="2026.01.1")
    assert snapshot.exists()

    connection = sqlite3.connect(database_path)
    try:
        connection.execute("DELETE FROM bins")
        connection.commit()
    finally:
        connection.close()
    assert _bin_count(database_path) == 0

    backups.restore(snapshot)
    report = verify_database(database_path)
    assert report.ok, report.errors
    assert _bin_count(database_path) == before


def test_restore_latest_picks_the_newest_snapshot(backups):
    first = backups.create(version="2026.01.1")
    second = backups.create(version="2026.01.2")
    assert first != second

    restored = backups.restore_latest()
    assert restored == second


def test_retention_prunes_the_oldest_snapshots(backups):
    for index in range(6):
        backups.create(version=f"2026.01.{index}")
    assert len(backups.list()) == 3


def test_restore_with_no_snapshots_reports_nothing_to_do(tmp_path, database_path):
    from app.services.backup_service import BackupService

    empty = BackupService(database_path, tmp_path / "no-backups")
    assert empty.restore_latest() is None
