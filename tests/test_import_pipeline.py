"""The import path: column mapping, staging by default, and what survives it.

Every fixture is synthetic issuer metadata. No cardholder data appears here.
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import text

from app.importers.base import BaseImporter
from app.models.entities import RangeType


@pytest.fixture
def empty_database(tmp_path):
    """A fresh database with the schema and reference data, nothing else."""
    from app.database.engine import DatabaseManager
    from app.database.schema import create_schema

    manager = DatabaseManager(tmp_path / "import.sqlite")
    manager.open(create_if_missing=True)
    create_schema(manager.engine)
    yield manager
    manager.close()


# -- column mapping -----------------------------------------------------------


@pytest.mark.parametrize(
    ("column", "field"),
    [
        ("BIN", "bin"),
        ("bin_number", "bin"),
        ("Issuing Bank", "issuer"),
        ("card_scheme", "network"),
        ("Country Code", "country"),
        ("postcode", "postal_code"),
        ("valid_from", "effective_from"),
        ("end_date", "effective_to"),
        ("allocation_type", "range_type"),
        ("role", "relationship"),
    ],
)
def test_source_columns_map_onto_record_fields(column, field):
    mapped = BaseImporter.map_row({column: "value"})
    assert field in mapped


def test_an_unrecognised_column_is_ignored_rather_than_fatal():
    mapped = BaseImporter.map_row({"bin": "414720", "a_column_from_the_future": "x"})
    assert mapped == {"bin": "414720"}


def test_a_row_without_a_bin_produces_no_record():
    assert BaseImporter.to_record({"issuer": "A Bank"}) is None


def test_aliases_split_on_common_separators():
    mapped = BaseImporter.map_row({"bin": "414720", "aliases": "A|B|C"})
    assert mapped["aliases"] == ["A", "B", "C"]


# -- staging is the default ---------------------------------------------------


def _write_csv(path, rows: list[dict]) -> None:
    import csv

    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def test_a_bad_row_is_held_and_the_good_ones_promoted(tmp_path, empty_database, capsys):
    from app.cli import EXIT_OK, main

    source = tmp_path / "mixed.csv"
    _write_csv(
        source,
        [
            {"bin": "810001", "issuer": "Good Bank", "country": "US", "network": "visa"},
            {"bin": "not-a-bin", "issuer": "Broken", "country": "US", "network": "visa"},
            {"bin": "810002", "issuer": "Good Bank", "country": "US", "network": "visa"},
        ],
    )
    empty_database.close()

    code = main(
        ["import-data", "--source", str(source), "--database", str(empty_database.path)]
    )
    out = capsys.readouterr().out
    assert code == EXIT_OK
    assert "3 received" in out
    assert "2 promoted" in out
    assert "1 rejected" in out
    assert "held back" in out


def test_only_promoted_records_reach_production(tmp_path, empty_database):
    from app.cli import main

    source = tmp_path / "mixed.csv"
    _write_csv(
        source,
        [
            {"bin": "810003", "issuer": "Good Bank", "country": "US", "network": "visa"},
            # Malformed rather than absent: a row with no BIN at all is not a
            # record and never reaches staging, whereas this one gets there and
            # is rejected — which is what the staging table is for.
            {"bin": "not-a-bin", "issuer": "Broken", "country": "US", "network": "visa"},
        ],
    )
    path = empty_database.path
    empty_database.close()
    main(["import-data", "--source", str(source), "--database", str(path)])

    from app.database.engine import DatabaseManager

    manager = DatabaseManager(path)
    manager.open()
    try:
        with manager.session() as session:
            assert session.execute(text("SELECT COUNT(*) FROM bins")).scalar() == 1
            assert (
                session.execute(
                    text("SELECT COUNT(*) FROM staging_records WHERE status = 'rejected'")
                ).scalar()
                == 1
            )
    finally:
        manager.close()


def test_an_eight_digit_child_keeps_its_own_issuer(tmp_path, empty_database):
    """The case the engine exists for, end to end through the CLI."""
    from app.cli import main
    from app.database.engine import DatabaseManager
    from app.repositories.bin_repository import BinRepository
    from app.services.lookup_service import LookupService

    source = tmp_path / "nested.csv"
    _write_csv(
        source,
        [
            {"bin": "810004", "issuer": "Root Bank", "country": "US", "network": "visa"},
            {"bin": "81000455", "issuer": "Child Bank", "country": "US", "network": "visa"},
        ],
    )
    path = empty_database.path
    empty_database.close()
    main(["import-data", "--source", str(source), "--database", str(path)])

    manager = DatabaseManager(path)
    manager.open()
    try:
        service = LookupService(BinRepository(manager))
        assert service.lookup("810004").best.issuer_name == "Root Bank"
        assert service.lookup("81000455").best.issuer_name == "Child Bank"
        assert service.lookup("810004").more_specific_count == 1
    finally:
        manager.close()


def test_effective_dates_become_a_timeline(tmp_path, empty_database):
    from app.cli import main
    from app.database.engine import DatabaseManager
    from app.repositories.bin_repository import BinRepository
    from app.services.lookup_service import LookupService

    source = tmp_path / "temporal.csv"
    _write_csv(
        source,
        [
            {
                "bin": "820001",
                "issuer": "Former Holder Bank",
                "country": "US",
                "network": "visa",
                "effective_from": "2019-01-01",
                "effective_to": "2023-06-30",
                "relationship": "former_issuer",
            },
            {
                "bin": "820001",
                "issuer": "Present Holder Bank",
                "country": "US",
                "network": "visa",
                "effective_from": "2023-07-01",
                "effective_to": "",
                "relationship": "issuer",
            },
        ],
    )
    path = empty_database.path
    empty_database.close()
    main(["import-data", "--source", str(source), "--database", str(path)])

    manager = DatabaseManager(path)
    manager.open()
    try:
        result = LookupService(BinRepository(manager)).lookup("820001")
        assert result.best is not None
        assert result.best.issuer_name == "Present Holder Bank"
        historical = [item for item in result.relationships if not item.is_current]
        assert historical
        assert historical[0].display_name == "Former Holder Bank"
        assert historical[0].effective_period == "2019–2023"
        assert not result.is_conflicted, "a timeline is not a disagreement"
    finally:
        manager.close()


def test_no_stage_writes_straight_to_production(tmp_path, empty_database, capsys):
    from app.cli import EXIT_OK, main

    source = tmp_path / "direct.csv"
    _write_csv(
        source,
        [{"bin": "830001", "issuer": "Direct Bank", "country": "US", "network": "visa"}],
    )
    path = empty_database.path
    empty_database.close()

    code = main(
        ["import-data", "--source", str(source), "--no-stage", "--database", str(path)]
    )
    assert code == EXIT_OK
    assert "through staging" not in capsys.readouterr().out


def test_a_json_source_imports(tmp_path, empty_database):
    from app.cli import EXIT_OK, main

    source = tmp_path / "records.json"
    source.write_text(
        json.dumps(
            [
                {
                    "bin": "840001",
                    "scheme": "visa",
                    "type": "credit",
                    "bank": {"name": "JSON Bank", "url": "jsonbank.example"},
                    "country": {"alpha2": "US"},
                }
            ]
        ),
        encoding="utf-8",
    )
    path = empty_database.path
    empty_database.close()
    assert main(["import-data", "--source", str(source), "--database", str(path)]) == EXIT_OK


def test_range_type_survives_the_import(tmp_path, empty_database):
    from app.cli import main
    from app.database.engine import DatabaseManager

    source = tmp_path / "ranges.csv"
    _write_csv(
        source,
        [
            {
                "bin": "85000100",
                "bin_high": "85000199",
                "issuer": "Range Bank",
                "country": "US",
                "network": "visa",
                "allocation_type": RangeType.ACCOUNT_RANGE.value,
            }
        ],
    )
    path = empty_database.path
    empty_database.close()
    main(["import-data", "--source", str(source), "--database", str(path)])

    manager = DatabaseManager(path)
    manager.open()
    try:
        with manager.session() as session:
            assert (
                session.execute(text("SELECT range_type FROM bin_ranges LIMIT 1")).scalar()
                == RangeType.ACCOUNT_RANGE.value
            )
    finally:
        manager.close()
