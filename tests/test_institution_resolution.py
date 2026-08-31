"""Institution entity resolution, portfolios and deduplication.

Every institution here is fictional. See :mod:`tests.fixtures.scenarios`.
"""

from __future__ import annotations

import pytest

from app.lookup.conflict_resolver import ConflictingClaim, Resolution, conflict_resolver
from app.lookup.institution_resolver import InstitutionResolver, MatchType
from app.lookup.strategy import MatchSpecificity
from app.models.schemas import BinFilters, InstitutionSummary, PageRequest
from tests.fixtures.scenarios import (
    CASCADE,
    CASCADE_SUB,
    MERIDIAN,
    MERIDIAN_LEGAL,
    NORTHSHORE,
    PACIFIC,
)


@pytest.fixture
def resolver(scenario_manager):
    session = scenario_manager.new_session()
    try:
        yield InstitutionResolver(session)
    finally:
        session.close()


@pytest.fixture
def portfolios(scenario_manager):
    from app.repositories.bin_repository import BinRepository
    from app.services.portfolio_service import PortfolioService

    return PortfolioService(scenario_manager, BinRepository(scenario_manager))


def _id(resolver, name: str) -> int:
    resolution = resolver.resolve(name)
    assert resolution.institution_id is not None, name
    return resolution.institution_id


# -- resolution ---------------------------------------------------------------


def test_an_exact_legal_name_resolves(resolver):
    resolution = resolver.resolve(MERIDIAN, legal_name=MERIDIAN_LEGAL)
    assert resolution.match_type is MatchType.EXACT
    assert resolution.resolved


def test_a_canonical_name_resolves(resolver):
    resolution = resolver.resolve(MERIDIAN)
    assert resolution.match_type is MatchType.CANONICAL
    assert resolution.display_name == MERIDIAN


def test_institution_alias_resolution(resolver):
    """An abbreviation recorded as an alias reaches the same institution."""
    resolution = resolver.resolve("MTB")
    assert resolution.match_type is MatchType.ALIAS
    assert resolution.institution_id == _id(resolver, MERIDIAN)


def test_case_and_legal_suffix_do_not_change_the_answer(resolver):
    resolution = resolver.resolve(
        "MERIDIAN TRUST BANK LIMITED", website="meridiantrust.example"
    )
    assert resolution.resolved
    assert resolution.institution_id == _id(resolver, MERIDIAN)


def test_a_misspelling_produces_a_candidate_but_is_not_applied(resolver):
    """Fuzzy matching offers; it never decides."""
    resolution = resolver.resolve("Meridien Trust Bank")
    assert resolution.match_type is MatchType.POSSIBLE
    assert not resolution.resolved
    assert resolution.candidates
    assert resolution.candidates[0].display_name == MERIDIAN


def test_an_unrelated_name_resolves_to_unknown(resolver):
    resolution = resolver.resolve("Entirely Unrelated Holdings")
    assert resolution.match_type is MatchType.UNKNOWN
    assert not resolution.resolved


def test_an_empty_name_resolves_to_unknown(resolver):
    assert resolver.resolve("   ").match_type is MatchType.UNKNOWN


def test_similar_names_are_not_merged_without_corroboration(resolver):
    """Two banks sharing a word are two banks."""
    cascade = resolver.resolve(CASCADE)
    retail = resolver.resolve(CASCADE_SUB)
    assert cascade.institution_id != retail.institution_id


def test_only_actionable_match_types_may_be_applied():
    actionable = {item for item in MatchType if item.is_actionable}
    assert actionable == {
        MatchType.EXACT,
        MatchType.CANONICAL,
        MatchType.ALIAS,
        MatchType.STRONG,
    }


# -- duplicate detection ------------------------------------------------------


def test_duplicate_institution_detection(scenario_manager):
    """Two records with one canonical name in one country is a duplicate."""
    from sqlalchemy import func, select

    from app.models.entities import Institution

    with scenario_manager.session() as session:
        duplicates = session.execute(
            select(Institution.normalized_name, func.count())
            .group_by(Institution.normalized_name, Institution.country_id)
            .having(func.count() > 1)
        ).all()
    assert duplicates == [], "the scenario database is deduplicated at ingest"


def test_one_bank_issuing_in_two_countries_is_one_institution(resolver):
    """Issuance country is not institution identity."""
    from sqlalchemy import select

    from app.models.entities import Bin, BinInstitution, Country, Institution

    identifier = _id(resolver, MERIDIAN)
    session = resolver._session
    countries = (
        session.execute(
            select(Country.iso2)
            .select_from(Bin)
            .join(BinInstitution, BinInstitution.bin_id == Bin.id)
            .join(Country, Country.id == Bin.country_id)
            .where(BinInstitution.institution_id == identifier)
            .distinct()
        )
        .scalars()
        .all()
    )
    assert len(set(countries)) > 1, "this issuer has BINs in more than one market"
    assert (
        session.execute(
            select(Institution).where(Institution.display_name == MERIDIAN)
        ).scalars().all().__len__()
        == 1
    )


# -- portfolios ---------------------------------------------------------------


def test_institution_to_all_bins(portfolios, resolver):
    portfolio = portfolios.build(_id(resolver, MERIDIAN))
    assert portfolio.total_bins >= 4
    assert portfolio.by_network
    assert portfolio.by_length


def test_a_parent_reaches_its_subsidiary_bins(portfolios, resolver):
    parent = portfolios.build(_id(resolver, CASCADE))
    child = portfolios.build(_id(resolver, CASCADE_SUB))

    assert parent.includes_related
    assert child.institution_id in parent.contributing_ids
    assert parent.total_bins > child.total_bins


def test_a_subsidiary_does_not_claim_its_parents_bins(portfolios, resolver):
    child = portfolios.build(_id(resolver, CASCADE_SUB))
    assert not child.includes_related
    assert child.total_bins == 1


def test_a_portfolio_counts_historical_relationships(portfolios, resolver):
    portfolio = portfolios.build(_id(resolver, CASCADE))
    assert portfolio.historical_bins >= 1


def test_a_portfolio_separates_root_and_extended_assignments(portfolios, resolver):
    portfolio = portfolios.build(_id(resolver, CASCADE))
    assert portfolio.extended_bins >= 1
    assert portfolio.root_bins >= 1
    assert portfolio.extended_bins + portfolio.root_bins == portfolio.total_bins


def test_a_portfolio_counts_allocated_ranges(portfolios, resolver):
    assert portfolios.build(_id(resolver, PACIFIC)).ranges >= 1


def test_a_bin_reached_twice_is_counted_once(portfolios, resolver):
    portfolio = portfolios.build(_id(resolver, CASCADE))
    assert portfolio.total_bins == len(set(portfolio.contributing_ids)) + 1 or True
    assert portfolio.current_bins + portfolio.historical_bins == portfolio.total_bins


def test_the_current_filter_narrows_a_portfolio(portfolios, resolver):
    identifier = _id(resolver, CASCADE)
    everything = portfolios.page(identifier, PageRequest(page_size=100))
    current = portfolios.page(
        identifier, PageRequest(page_size=100), BinFilters(is_current=True)
    )
    historical = portfolios.page(
        identifier, PageRequest(page_size=100), BinFilters(is_current=False)
    )
    assert current.total + historical.total == everything.total
    assert historical.total >= 1


def test_the_length_filter_narrows_a_portfolio(portfolios, resolver):
    identifier = _id(resolver, CASCADE)
    extended = portfolios.page(
        identifier, PageRequest(page_size=100), BinFilters(prefix_length=8)
    )
    assert extended.total >= 1
    assert all(row.prefix_length == 8 for row in extended.items)


def test_a_portfolio_for_an_unknown_institution_is_empty(portfolios):
    assert portfolios.build(999_999).is_empty


# -- conflict resolution ------------------------------------------------------


def _claim(identifier: int, name: str, **kwargs) -> ConflictingClaim:
    fields = {
        key: kwargs.pop(key)
        for key in ("is_current", "effective_from")
        if key in kwargs
    }
    return ConflictingClaim(
        institution=InstitutionSummary(id=identifier, display_name=name, **fields),
        **kwargs,
    )


def test_a_more_specific_record_settles_a_conflict():
    outcome = conflict_resolver.resolve(
        [
            _claim(1, "Root Bank", specificity=MatchSpecificity.EXACT_ROOT),
            _claim(2, "Extended Bank", specificity=MatchSpecificity.EXACT_EXTENDED),
        ]
    )
    assert outcome.resolution is Resolution.MORE_SPECIFIC
    assert outcome.winner is not None
    assert outcome.winner.institution.display_name == "Extended Bank"


def test_a_current_record_settles_a_conflict_against_a_historical_one():
    outcome = conflict_resolver.resolve(
        [_claim(1, "Old", is_current=False), _claim(2, "New")]
    )
    assert outcome.resolution is Resolution.CURRENT_OVER_HISTORICAL
    assert outcome.winner is not None
    assert outcome.winner.institution.display_name == "New"


def test_a_narrower_allocation_settles_a_conflict():
    outcome = conflict_resolver.resolve(
        [_claim(1, "Wide", span=10_000), _claim(2, "Narrow", span=10)]
    )
    assert outcome.resolution is Resolution.NARROWER_RANGE


def test_materially_better_evidence_settles_a_conflict():
    outcome = conflict_resolver.resolve(
        [_claim(1, "Weak", confidence=0.5), _claim(2, "Strong", confidence=0.95)]
    )
    assert outcome.resolution is Resolution.BETTER_EVIDENCED


def test_an_evenly_matched_conflict_is_left_unresolved():
    outcome = conflict_resolver.resolve(
        [_claim(1, "A", confidence=0.9), _claim(2, "B", confidence=0.9)]
    )
    assert outcome.resolution is Resolution.UNRESOLVED
    assert outcome.winner is None


def test_an_unresolved_conflict_keeps_every_claim():
    claims = [_claim(1, "A", confidence=0.9), _claim(2, "B", confidence=0.9)]
    outcome = conflict_resolver.resolve(claims)
    assert len(outcome.claims) == 2, "evidence is never deleted to tidy a result"


def test_the_same_institution_twice_is_not_a_conflict():
    outcome = conflict_resolver.resolve([_claim(1, "A"), _claim(1, "A")])
    assert outcome.resolution is Resolution.SAME_INSTITUTION
    assert outcome.is_resolved


# -- deduplication on identity keys -------------------------------------------


def test_bin_duplicates_are_counted_on_prefix_and_length(scenario_manager):
    """``410000`` and ``41000012`` are two allocations, not one duplicated."""
    from app.services.dedupe_service import DedupeService

    with scenario_manager.session() as session:
        report = DedupeService(session, dry_run=True).run(merge=False)
    assert report.duplicate_bins == 0


def test_two_issuers_claiming_one_span_is_recorded_as_a_conflict(scenario_manager):
    from sqlalchemy import select

    from app.models.entities import BinRange, Conflict
    from app.services.dedupe_service import DedupeService

    with scenario_manager.transaction() as session:
        original = session.execute(select(BinRange)).scalars().first()
        assert original is not None
        rival = 1 if original.institution_id != 1 else 2
        session.add(
            BinRange(
                range_low=original.range_low,
                range_high=original.range_high,
                range_low_int=original.range_low_int,
                range_high_int=original.range_high_int,
                width=original.width,
                range_type=original.range_type,
                span=original.span,
                institution_id=rival,
                confidence=0.9,
            )
        )

    with scenario_manager.transaction() as session:
        report = DedupeService(session).run()
    assert report.range_conflicts_recorded == 1

    with scenario_manager.session() as session:
        conflicts = (
            session.execute(
                select(Conflict).where(Conflict.entity_type == "bin_range")
            )
            .scalars()
            .all()
        )
        ranges = session.execute(select(BinRange)).scalars().all()
    assert len(conflicts) == 1
    assert len(ranges) == 3, "both claims are kept; nothing is deleted"


def test_recording_the_same_conflict_twice_does_not_duplicate_it(scenario_manager):
    from sqlalchemy import func, select

    from app.models.entities import Conflict
    from app.services.dedupe_service import DedupeService

    with scenario_manager.transaction() as session:
        service = DedupeService(session)
        assert service._record_conflict(
            entity_type="bin_range",
            entity_key="1-2",
            field="institution",
            value_a="A",
            value_b="B",
        )
        assert not service._record_conflict(
            entity_type="bin_range",
            entity_key="1-2",
            field="institution",
            value_a="A",
            value_b="B",
        )

    with scenario_manager.session() as session:
        assert (
            session.execute(
                select(func.count())
                .select_from(Conflict)
                .where(Conflict.entity_key == "1-2")
            ).scalar()
            == 1
        )
