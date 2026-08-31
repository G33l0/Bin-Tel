"""Database health scoring — every figure is measured, never assumed."""

from __future__ import annotations

import sqlite3

import pytest
from sqlalchemy import text

from app.services.health_service import DatabaseHealthService, HealthGrade


@pytest.fixture
def health(manager):
    return DatabaseHealthService(manager)


def test_a_freshly_built_package_is_healthy(health):
    report = health.evaluate()
    assert report.error is None
    assert all(check.passed for check in report.checks)
    assert report.summary == "Every health check passed."
    assert report.grade is HealthGrade.EXCELLENT
    assert report.percent == 100


def test_every_check_is_reported(health):
    report = health.evaluate()
    keys = {check.key for check in report.checks}
    assert keys == {
        "integrity",
        "indexes",
        "duplicates",
        "orphans",
        "conflicts",
        "relationships",
        "completeness",
    }


def test_the_score_is_a_weighted_mean_of_the_checks(health):
    report = health.evaluate()
    total_weight = sum(check.weight for check in report.checks)
    expected = (
        sum(check.score * check.weight for check in report.checks) / total_weight
    )
    assert report.score == pytest.approx(expected)


def test_counts_come_from_the_database(health, manager):
    report = health.evaluate()
    with manager.session() as session:
        bins = int(session.execute(text("SELECT COUNT(*) FROM bins")).scalar() or 0)
    assert report.records == bins


def test_a_missing_index_lowers_the_index_score(health, manager, database_path):
    before = health.evaluate().check("indexes")
    assert before is not None and before.score == 1.0

    with manager.session() as session:
        name = str(
            session.execute(
                text(
                    "SELECT name FROM sqlite_master WHERE type='index' "
                    "AND name LIKE 'ix_%' LIMIT 1"
                )
            ).scalar()
        )
    with manager.engine.begin() as connection:
        connection.exec_driver_sql(f"DROP INDEX {name}")

    after = health.evaluate().check("indexes")
    assert after is not None
    assert after.score < 1.0
    assert name in after.detail or "missing" in after.detail.lower()


def test_orphaned_rows_are_counted_and_can_be_removed(health, manager):
    with manager.engine.begin() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
        connection.exec_driver_sql(
            "INSERT INTO bin_institutions (bin_id, institution_id, relationship_type, "
            "is_primary, confidence) VALUES (999999, 999999, 'issuer', 0, 0.5)"
        )

    report = health.evaluate()
    assert report.orphans > 0
    assert (report.check("orphans") or report.checks[0]).score < 1.0

    removed = health.remove_orphans()
    assert sum(removed.values()) > 0
    assert health.evaluate().orphans == 0


def test_a_closed_database_reports_an_error_rather_than_a_score(manager):
    manager.close()
    report = DatabaseHealthService(manager).evaluate()
    assert report.error
    assert report.grade is HealthGrade.UNKNOWN


def test_a_quick_evaluation_still_scores(health):
    report = health.evaluate(quick=True)
    assert report.percent > 0
    assert report.check("integrity") is not None
