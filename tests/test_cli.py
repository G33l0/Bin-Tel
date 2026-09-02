"""The maintenance CLI: every command runs, and none of them corrupt anything."""

from __future__ import annotations

import json

import pytest

from app.cli import EXIT_OK, main


def run(capsys, *argv: str) -> tuple[int, str]:
    code = main(list(argv))
    return code, capsys.readouterr().out


def test_version_prints_the_application_version(capsys):
    code, out = run(capsys, "version")
    assert code == EXIT_OK
    assert "Bin-Tel" in out


def test_verify_db_passes_on_a_generated_package(capsys, database_path):
    code, out = run(capsys, "verify-db", "--database", str(database_path))
    assert code == EXIT_OK
    assert "ok" in out.lower() or "passed" in out.lower()


def test_verify_db_fails_on_a_broken_file(capsys, tmp_path):
    broken = tmp_path / "broken.sqlite"
    broken.write_text("not a database", encoding="utf-8")
    code, _ = run(capsys, "verify-db", "--database", str(broken))
    assert code != EXIT_OK


def test_stats_reports_real_counts(capsys, database_path):
    code, out = run(capsys, "stats", "--json", "--database", str(database_path))
    assert code == EXIT_OK
    payload = json.loads(out)
    assert payload["counts"]["bins"] > 0
    assert payload["counts"]["institutions"] > 0
    assert payload["metadata"]["version"]
    assert payload["top_countries"]


def test_lookup_resolves_a_bin(capsys, database_path, manager):
    from sqlalchemy import text

    with manager.session() as session:
        digits = str(session.execute(text("SELECT bin FROM bins LIMIT 1")).scalar())

    code, out = run(capsys, "lookup", digits, "--database", str(database_path))
    assert code == EXIT_OK
    assert digits in out


def test_lookup_refuses_a_card_length_number(capsys, database_path):
    code, out = run(
        capsys, "lookup", "4111111111111111", "--database", str(database_path)
    )
    assert code != EXIT_OK
    assert "4111111111111111" not in out


def test_export_writes_a_bin_record(capsys, tmp_path, database_path, manager):
    from sqlalchemy import text

    with manager.session() as session:
        digits = str(session.execute(text("SELECT bin FROM bins LIMIT 1")).scalar())
    destination = tmp_path / "record.json"

    code, _ = run(
        capsys,
        "export",
        "--bin",
        digits,
        "--format",
        "json",
        "--output",
        str(destination),
        "--database",
        str(database_path),
    )
    assert code == EXIT_OK
    payload = json.loads(destination.read_text(encoding="utf-8"))
    assert payload["record"]["BIN"] == digits
    # An export carries issuer metadata and nothing that identifies a person.
    assert not {key.lower() for key in payload["record"]} & {
        "cardholder",
        "cvv",
        "pin",
        "account number",
    }


def test_init_db_creates_an_empty_but_structurally_sound_database(capsys, tmp_path):
    from app.core.constants import SCHEMA_VERSION
    from app.database.engine import DatabaseManager
    from app.database.integrity import verify_database
    from app.database.schema import missing_tables

    target = tmp_path / "fresh.sqlite"
    code, _ = run(capsys, "init-db", "--database", str(target), "--db-version", "2026.01.1")
    assert code == EXIT_OK

    report = verify_database(target)
    assert report.integrity_result == "ok"
    assert report.schema_version == SCHEMA_VERSION
    assert report.database_version == "2026.01.1"
    # It holds no records yet, which is exactly why full verification fails --
    # an empty shell is for importing into, not for shipping.
    assert not report.ok
    assert report.errors == ["The database contains no BIN records."]

    manager = DatabaseManager(target)
    manager.open()
    try:
        assert missing_tables(manager.engine) == []
    finally:
        manager.close()


def test_init_db_refuses_to_clobber_without_force(capsys, database_path):
    code, _ = run(capsys, "init-db", "--database", str(database_path))
    assert code != EXIT_OK


def test_reindex_runs_and_leaves_the_database_valid(capsys, database_path):
    from app.database.integrity import verify_database

    code, _ = run(capsys, "reindex", "--database", str(database_path))
    assert code == EXIT_OK
    assert verify_database(database_path).ok


def test_backup_and_restore_round_trip(capsys, tmp_path, database_path):
    from app.database.integrity import verify_database

    backups = tmp_path / "backups"
    code, out = run(
        capsys, "backup", "--output", str(backups), "--database", str(database_path)
    )
    assert code == EXIT_OK
    snapshots = list(backups.glob("*"))
    assert snapshots

    code, _ = run(
        capsys, "restore", str(snapshots[0]), "--database", str(database_path)
    )
    assert code == EXIT_OK
    assert verify_database(database_path).ok


def test_dedupe_detect_only_changes_nothing(capsys, database_path):
    import sqlite3

    def count() -> int:
        connection = sqlite3.connect(database_path)
        try:
            return int(
                connection.execute("SELECT COUNT(*) FROM institutions").fetchone()[0]
            )
        finally:
            connection.close()

    before = count()
    code, _ = run(capsys, "dedupe", "--detect-only", "--database", str(database_path))
    assert code == EXIT_OK
    assert count() == before


def test_an_unknown_command_is_a_usage_error():
    with pytest.raises(SystemExit) as excinfo:
        main(["not-a-command"])
    assert excinfo.value.code != 0


# -- the lookup engine through the CLI ----------------------------------------


def test_lookup_reports_the_match_and_confidence(capsys, scenario_database, tmp_path):
    import shutil

    database = tmp_path / "scenarios.sqlite"
    shutil.copy2(scenario_database, database)

    code, out = run(capsys, "lookup", "41000012", "--database", str(database))
    assert code == EXIT_OK
    assert "Exact 8-digit" in out
    assert "Confidence: Verified" in out
    assert "Northshore Credit Union" in out


def test_lookup_reports_a_conflict(capsys, scenario_database, tmp_path):
    import shutil

    database = tmp_path / "scenarios.sqlite"
    shutil.copy2(scenario_database, database)

    code, out = run(capsys, "lookup", "520001", "--database", str(database))
    assert code == EXIT_OK
    assert "Conflicted" in out
    assert "Both readings are kept" in out


def test_lookup_says_when_nothing_is_resolved(capsys, scenario_database, tmp_path):
    import shutil

    database = tmp_path / "scenarios.sqlite"
    shutil.copy2(scenario_database, database)

    code, out = run(capsys, "lookup", "600001", "--database", str(database))
    assert code == EXIT_OK
    assert "No institution relationship is recorded" in out
    assert "Unknown" in out


def test_quality_prints_measured_metrics(capsys, database_path):
    code, out = run(capsys, "quality", "--json", "--database", str(database_path))
    assert code == EXIT_OK
    payload = json.loads(out)
    keys = {metric["key"] for metric in payload["metrics"]}
    assert {"institution_resolution", "extended_coverage", "duplicate_rate"} <= keys
    for metric in payload["metrics"]:
        assert metric["numerator"] <= metric["denominator"] or metric["denominator"] == 0


def test_quality_can_store_its_metrics(capsys, database_path):
    code, out = run(capsys, "quality", "--store", "--database", str(database_path))
    assert code == EXIT_OK
    assert "Stored" in out


def test_staging_reports_nothing_when_empty(capsys, database_path):
    code, out = run(capsys, "staging", "--database", str(database_path))
    assert code == EXIT_OK
    assert "Nothing is staged" in out


def test_learn_says_why_nothing_external_was_consulted(tmp_path, capsys, monkeypatch):
    """Silence would read as "asked and got nothing", which is not what happened."""
    from app.cli import main
    from app.core.paths import reset_paths_cache

    monkeypatch.setenv("BINTEL_DATA_DIR", str(tmp_path))
    reset_paths_cache()
    listing = tmp_path / "bin-list.csv"
    listing.write_text("bin,bank,country\n410000,Cascade Bank,US\n", encoding="utf-8")

    assert main(["rebuild", "--list", str(listing), "--data-dir", str(tmp_path)]) == 0
    capsys.readouterr()

    assert main(["learn", "410000", "--data-dir", str(tmp_path)]) == 0
    output = capsys.readouterr().out
    assert "learning is off" in output
    assert "410000" in output
    reset_paths_cache()
