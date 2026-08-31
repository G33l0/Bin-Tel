"""The staging pipeline, quality metrics, range indexing and the v2 migration."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import text

from app.core.constants import SCHEMA_VERSION
from app.models.entities import StagingStatus
from app.services.ingest_service import IngestService, RawBinRecord
from app.services.quality_service import DataQualityService
from app.services.staging_service import StagingService
from tests.fixtures.scenarios import MERIDIAN


@pytest.fixture
def staging(scenario_manager):
    session = scenario_manager.new_session()
    ingest = IngestService(session, source_code="tests", source_name="Test source")
    ingest.seed_reference_data()
    try:
        yield StagingService(session), ingest, session
    finally:
        session.rollback()
        session.close()


# -- staging ------------------------------------------------------------------


def test_a_clean_record_reaches_production(staging):
    service, ingest, session = staging
    report = service.run(
        [RawBinRecord(bin="710001", issuer="Brand New Bank", country="US", confidence=0.9)],
        ingest,
    )
    assert report.promoted == 1
    assert report.rejected == 0
    session.flush()
    assert session.execute(
        text("SELECT COUNT(*) FROM bins WHERE bin = '710001'")
    ).scalar() == 1


def test_an_unreadable_prefix_never_reaches_production(staging):
    service, ingest, session = staging
    report = service.run([RawBinRecord(bin="not-a-bin", issuer="X")], ingest)
    assert report.rejected == 1
    assert report.promoted == 0
    held = service.pending(report.batch_id)
    assert held and held[0].status == StagingStatus.REJECTED.value
    assert "Prefix rejected" in (held[0].issues or "")


def test_an_impossible_confidence_is_rejected(staging):
    service, ingest, _ = staging
    report = service.run([RawBinRecord(bin="710002", issuer="X", confidence=5.0)], ingest)
    assert report.rejected == 1


def test_a_relationship_that_ends_before_it_begins_is_rejected(staging):
    service, ingest, _ = staging
    report = service.run(
        [
            RawBinRecord(
                bin="710003",
                issuer="X",
                effective_from=datetime(2025, 1, 1, tzinfo=UTC),
                effective_to=datetime(2020, 1, 1, tzinfo=UTC),
            )
        ],
        ingest,
    )
    assert report.rejected == 1


def test_a_repair_is_recorded_rather_than_silent(staging):
    service, ingest, _ = staging
    report = service.run(
        [RawBinRecord(bin="710004", bin_high="710000", issuer="X", confidence=0.8)],
        ingest,
    )
    assert report.promoted == 1
    assert any("transposed" in note for note in report.issues)


def test_a_bad_record_does_not_stop_the_good_ones(staging):
    service, ingest, _ = staging
    report = service.run(
        [
            RawBinRecord(bin="710005", issuer="Good Bank", confidence=0.9),
            RawBinRecord(bin="", issuer="Bad"),
            RawBinRecord(bin="710006", issuer="Also Good", confidence=0.9),
        ],
        ingest,
    )
    assert report.promoted == 2
    assert report.rejected == 1


def test_staging_resolves_to_an_existing_institution(staging):
    service, ingest, session = staging
    report = service.run(
        [
            RawBinRecord(
                bin="710007",
                issuer=MERIDIAN,
                country="US",
                website="meridiantrust.example",
                confidence=0.9,
            )
        ],
        ingest,
    )
    assert report.promoted == 1
    session.flush()
    issuer = session.execute(
        text(
            "SELECT i.display_name FROM bins b "
            "JOIN bin_institutions bi ON bi.bin_id = b.id "
            "JOIN institutions i ON i.id = bi.institution_id "
            "WHERE b.bin = '710007'"
        )
    ).scalar()
    assert issuer == MERIDIAN


def test_a_record_with_no_issuer_is_still_promotable(staging):
    """An allocation naming nobody is a fact. Inventing an issuer is not."""
    service, ingest, _ = staging
    report = service.run([RawBinRecord(bin="710008", confidence=0.6)], ingest)
    assert report.promoted == 1


def test_nothing_is_promoted_before_processing(staging):
    service, ingest, session = staging
    batch = service.receive([RawBinRecord(bin="710009", issuer="Held Bank")])
    session.flush()
    assert session.execute(
        text("SELECT COUNT(*) FROM bins WHERE bin = '710009'")
    ).scalar() == 0
    assert service.counts(batch) == {StagingStatus.RECEIVED.value: 1}


def test_staged_records_can_be_cleared(staging):
    service, ingest, _ = staging
    report = service.run([RawBinRecord(bin="710010", issuer="X", confidence=0.9)], ingest)
    assert service.clear(report.batch_id) >= 1
    assert service.counts(report.batch_id) == {}


# -- quality metrics ----------------------------------------------------------


@pytest.fixture
def quality(scenario_manager):
    return DataQualityService(scenario_manager)


def test_metrics_are_counted_from_the_database(quality, scenario_manager):
    report = quality.evaluate()
    with scenario_manager.session() as session:
        bins = int(session.execute(text("SELECT COUNT(*) FROM bins")).scalar() or 0)
    resolution = report.get("institution_resolution")
    assert resolution is not None
    assert resolution.denominator == bins


def test_the_resolution_metric_reflects_an_unresolved_prefix(quality):
    """600001 exists and names nobody, so resolution cannot be 100%."""
    resolution = quality.evaluate().get("institution_resolution")
    assert resolution is not None
    assert resolution.numerator < resolution.denominator


def test_eight_digit_and_legacy_coverage_partition_the_database(quality):
    report = quality.evaluate()
    extended = report.get("extended_coverage")
    roots = report.get("root_coverage")
    assert extended is not None and roots is not None
    assert extended.numerator + roots.numerator == extended.denominator


def test_a_metric_with_nothing_to_measure_reports_so(quality):
    from app.services.quality_service import QualityMetric

    metric = QualityMetric(key="x", label="X", numerator=0, denominator=0)
    assert not metric.measured
    assert metric.ratio is None
    assert metric.display == "Not measured"


def test_metrics_round_trip_through_the_database(quality):
    report = quality.evaluate(database_version="2026.01.1")
    assert quality.store(report) == len(report.metrics)
    stored = quality.stored("2026.01.1")
    assert {item.key for item in stored} == {item.key for item in report.metrics}


def test_a_closed_database_reports_an_error(scenario_manager):
    scenario_manager.close()
    report = DataQualityService(scenario_manager).evaluate()
    assert report.error
    assert report.metrics == []


# -- range indexing -----------------------------------------------------------


def test_range_index(scenario_manager):
    """The span columns are indexed, so containment never scans the table."""
    with scenario_manager.session() as session:
        indexes = {
            str(name)
            for (name,) in session.execute(
                text("SELECT name FROM sqlite_master WHERE type='index'")
            )
        }
    assert "ix_bins_span" in indexes
    assert "ix_bin_ranges_span" in indexes
    assert "ix_bins_length_int" in indexes


def test_a_containment_lookup_uses_an_index(scenario_manager):
    with scenario_manager.session() as session:
        plan = session.execute(
            text(
                "EXPLAIN QUERY PLAN SELECT id FROM bins "
                "WHERE span_low <= 41000012 AND span_high >= 41000012 "
                "AND prefix_length <= 8"
            )
        ).all()
    assert any("USING INDEX" in " ".join(map(str, row)) for row in plan), plan


def test_database_integrity(scenario_manager):
    from app.database.integrity import verify_database

    report = verify_database(scenario_manager.path)
    assert report.ok, report.errors
    assert report.schema_version == SCHEMA_VERSION


# -- the v2 migration ---------------------------------------------------------


def test_a_schema_1_package_migrates_and_backfills(tmp_path, sample_builder):
    """A package built before prefix identity existed is upgraded losslessly."""
    import sqlite3

    from app.database import migrations
    from app.database.engine import DatabaseManager
    from app.database.schema import read_schema_version

    package, _ = sample_builder.build(tmp_path, bin_count=40, version="2026.01.1", seed=3)

    # Wind the package back to schema 1 by dropping what v2 added.
    connection = sqlite3.connect(package)
    try:
        for statement in (
            "DROP TABLE IF EXISTS staging_records",
            "DROP TABLE IF EXISTS institution_relationships",
            "DROP TABLE IF EXISTS data_quality_metrics",
            "UPDATE database_metadata SET value = '1' WHERE key = 'schema_version'",
        ):
            connection.execute(statement)
        connection.commit()
    finally:
        connection.close()

    manager = DatabaseManager(package)
    manager.open()
    try:
        assert read_schema_version(manager.engine) == 1
        assert migrations.can_migrate(1)
        result = migrations.migrate(manager.engine)
        assert result.migrated
        assert result.to_version == SCHEMA_VERSION

        with manager.session() as session:
            rows = session.execute(
                text("SELECT bin, prefix, prefix_length, span_low, span_high FROM bins LIMIT 5")
            ).all()
            assert rows
            for value, prefix, length, low, high in rows:
                assert prefix == value, "the published digits are the assignment"
                assert length == len(value)
                assert low <= high
            assert session.execute(
                text("SELECT COUNT(*) FROM bin_institutions WHERE is_current IS NULL")
            ).scalar() == 0
            tables = {
                str(name)
                for (name,) in session.execute(
                    text("SELECT name FROM sqlite_master WHERE type='table'")
                )
            }
            assert {"staging_records", "institution_relationships", "data_quality_metrics"} <= tables
    finally:
        manager.close()
