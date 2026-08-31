"""Analytics: counts agree with the database, filters scope them, caching."""

from __future__ import annotations

import pytest
from sqlalchemy import text

from app.models.schemas import AdvancedQuery
from app.services.analytics_service import AnalyticsService


@pytest.fixture
def analytics(manager):
    return AnalyticsService(manager)


def _scalar(manager, sql: str, **params) -> int:
    with manager.session() as session:
        return int(session.execute(text(sql), params).scalar() or 0)


def test_headline_counts_match_the_database(analytics, manager):
    snapshot = analytics.snapshot(version="2026.01.1")

    assert snapshot.total_bins == _scalar(manager, "SELECT COUNT(*) FROM bins")
    assert snapshot.total_institutions == _scalar(
        manager, "SELECT COUNT(*) FROM institutions"
    )
    assert snapshot.total_countries == _scalar(
        manager, "SELECT COUNT(DISTINCT country_id) FROM bins WHERE country_id IS NOT NULL"
    )
    assert snapshot.total_networks == _scalar(
        manager, "SELECT COUNT(DISTINCT network_id) FROM bins WHERE network_id IS NOT NULL"
    )


def test_card_type_counts_match_the_database(analytics, manager):
    snapshot = analytics.snapshot()
    assert snapshot.credit_bins == _scalar(
        manager, "SELECT COUNT(*) FROM bins WHERE card_type = 'credit'"
    )
    assert snapshot.debit_bins == _scalar(
        manager, "SELECT COUNT(*) FROM bins WHERE card_type = 'debit'"
    )
    assert snapshot.prepaid_bins == _scalar(
        manager, "SELECT COUNT(*) FROM bins WHERE is_prepaid = 1"
    )
    assert snapshot.commercial_bins == _scalar(
        manager, "SELECT COUNT(*) FROM bins WHERE is_commercial = 1"
    )


def test_a_distribution_sums_to_the_total(analytics):
    snapshot = analytics.snapshot()
    country = snapshot.distribution("country")
    assert not country.is_empty
    assert sum(item.value for item in country.slices) == snapshot.total_bins
    assert country.total == snapshot.total_bins


def test_shares_are_percentages_of_the_total(analytics):
    snapshot = analytics.snapshot()
    country = snapshot.distribution("country")
    largest = country.largest
    assert largest is not None
    assert 0 < largest.share(snapshot.total_bins) <= 1.0


def test_filtering_narrows_every_count(analytics, manager):
    with manager.session() as session:
        code = str(
            session.execute(
                text(
                    "SELECT c.iso2 FROM bins b JOIN countries c ON c.id = b.country_id "
                    "GROUP BY c.iso2 ORDER BY COUNT(*) DESC LIMIT 1"
                )
            ).scalar()
        )
    expected = _scalar(
        manager,
        "SELECT COUNT(*) FROM bins b JOIN countries c ON c.id = b.country_id "
        "WHERE c.iso2 = :code",
        code=code,
    )

    scoped = analytics.snapshot(query=AdvancedQuery(country_code=code))
    unscoped = analytics.snapshot()

    assert scoped.total_bins == expected
    assert scoped.total_bins < unscoped.total_bins
    assert scoped.total_countries == 1


def test_the_snapshot_is_cached_per_version(analytics):
    first = analytics.snapshot(version="2026.01.1")
    second = analytics.snapshot(version="2026.01.1")
    assert second is first

    third = analytics.snapshot(version="2026.02.1")
    assert third is not first, "a new database version must recompute"


def test_invalidating_the_cache_forces_a_recompute(analytics):
    first = analytics.snapshot(version="2026.01.1")
    analytics.invalidate()
    assert analytics.snapshot(version="2026.01.1") is not first


def test_a_filter_that_matches_nothing_yields_zeroes(analytics):
    snapshot = analytics.snapshot(query=AdvancedQuery(city="Nowhere-at-all"))
    assert snapshot.total_bins == 0
    assert snapshot.distribution("country").is_empty


def test_top_institutions_are_ordered_and_consistent(analytics, manager):
    snapshot = analytics.snapshot()
    assert snapshot.top_institutions
    counts = [count for _, count in snapshot.top_institutions]
    assert counts == sorted(counts, reverse=True)

    name, count = snapshot.top_institutions[0]
    actual = _scalar(
        manager,
        "SELECT COUNT(*) FROM bin_institutions bi "
        "JOIN institutions i ON i.id = bi.institution_id WHERE i.display_name = :name",
        name=name,
    )
    assert count == actual


def test_institution_analytics_cover_one_issuer(analytics, manager):
    with manager.session() as session:
        institution_id = int(
            session.execute(text("SELECT id FROM institutions LIMIT 1")).scalar()
        )
    distributions = analytics.institution_analytics(institution_id)
    assert distributions
    assert any(not item.is_empty for item in distributions.values())
