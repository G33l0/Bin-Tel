"""Advanced search: criteria, matching modes, pagination and suggestions."""

from __future__ import annotations

import pytest
from sqlalchemy import text

from app.models.schemas import AdvancedQuery, MatchMode, PageRequest, SortDirection


@pytest.fixture
def repository(manager):
    from app.repositories.search_repository import SearchRepository

    return SearchRepository(manager)


def _sample_row(manager) -> tuple[str, str]:
    with manager.session() as session:
        row = session.execute(
            text(
                "SELECT b.bin, i.display_name FROM bins b "
                "JOIN bin_institutions bi ON bi.bin_id = b.id "
                "JOIN institutions i ON i.id = bi.institution_id LIMIT 1"
            )
        ).one()
    return str(row[0]), str(row[1])


def test_an_empty_query_browses_everything(repository, manager):
    with manager.session() as session:
        total = int(session.execute(text("SELECT COUNT(*) FROM bins")).scalar() or 0)

    page = repository.search(AdvancedQuery(), PageRequest(page_size=25))
    assert page.total == total
    assert len(page.items) == 25


def test_a_single_digit_prefix_matches_every_bin_starting_with_it(repository, manager):
    with manager.session() as session:
        expected = int(
            session.execute(text("SELECT COUNT(*) FROM bins WHERE bin LIKE '4%'")).scalar() or 0
        )
    assert expected > 0

    page = repository.search(AdvancedQuery(bin_prefix="4"), PageRequest(page_size=1))
    assert page.total == expected
    assert page.items[0].bin.startswith("4")


def test_a_bin_range_is_inclusive_of_both_endpoints(repository, manager):
    digits, _ = _sample_row(manager)
    page = repository.search(
        AdvancedQuery(bin_from=digits, bin_to=digits), PageRequest()
    )
    assert page.total == 1
    assert page.items[0].bin == digits


@pytest.mark.parametrize(
    "mode",
    [MatchMode.EXACT, MatchMode.PREFIX, MatchMode.CONTAINS],
)
def test_each_institution_match_mode_finds_the_issuer(repository, manager, mode):
    _, issuer = _sample_row(manager)
    term = issuer if mode is not MatchMode.CONTAINS else issuer.split()[0]
    if mode is MatchMode.PREFIX:
        term = issuer[: max(4, len(issuer) // 2)]

    page = repository.search(
        AdvancedQuery(institution=term, institution_match=mode), PageRequest()
    )
    assert page.total >= 1
    assert any(issuer in (row.institution or "") for row in page.items)


def test_fuzzy_matching_tolerates_a_misspelled_issuer(repository, manager):
    _, issuer = _sample_row(manager)
    # Swap one character past the blocking prefix; fuzzy search still finds it.
    index = len(issuer) - 2
    replacement = "x" if issuer[index].lower() != "x" else "y"
    misspelled = issuer[:index] + replacement + issuer[index + 1 :]
    assert misspelled != issuer

    page = repository.search(
        AdvancedQuery(institution=misspelled, institution_match=MatchMode.FUZZY),
        PageRequest(),
    )
    assert page.total >= 1
    assert any(row.institution == issuer for row in page.items)


def test_filters_combine_conjunctively(repository, manager):
    with manager.session() as session:
        row = session.execute(
            text(
                "SELECT c.iso2, n.code FROM bins b "
                "JOIN countries c ON c.id = b.country_id "
                "JOIN networks n ON n.id = b.network_id LIMIT 1"
            )
        ).one()
        country, network = str(row[0]), str(row[1])
        expected = int(
            session.execute(
                text(
                    "SELECT COUNT(*) FROM bins b "
                    "JOIN countries c ON c.id = b.country_id "
                    "JOIN networks n ON n.id = b.network_id "
                    "WHERE c.iso2 = :country AND n.code = :network"
                ),
                {"country": country, "network": network},
            ).scalar()
            or 0
        )

    page = repository.search(
        AdvancedQuery(country_code=country, network_code=network), PageRequest()
    )
    assert page.total == expected
    assert expected > 0


def test_pagination_walks_the_whole_result_set_without_repeats(repository):
    request = PageRequest(page_size=20, sort_by="bin", direction=SortDirection.ASC)
    first = repository.search(AdvancedQuery(), request)

    seen: list[str] = []
    pages = min(4, first.page_count)
    for number in range(1, pages + 1):
        page = repository.search(AdvancedQuery(), request.at(number))
        seen.extend(row.bin for row in page.items)

    assert len(seen) == len(set(seen))
    assert seen == sorted(seen)


def test_sorting_reverses_with_the_direction(repository):
    ascending = repository.search(
        AdvancedQuery(), PageRequest(page_size=5, direction=SortDirection.ASC)
    )
    descending = repository.search(
        AdvancedQuery(), PageRequest(page_size=5, direction=SortDirection.DESC)
    )
    assert [r.bin for r in ascending.items] != [r.bin for r in descending.items]
    assert descending.items[0].bin > ascending.items[0].bin


def test_a_query_that_matches_nothing_returns_an_empty_page(repository):
    page = repository.search(AdvancedQuery(city="Nowhere-at-all"), PageRequest())
    assert page.total == 0
    assert list(page.items) == []


def test_suggestions_cover_bins_and_institutions(repository, manager):
    digits, issuer = _sample_row(manager)

    by_bin = repository.suggest(digits[:4])
    assert any(kind == "bin" for kind, _, _ in by_bin)

    by_name = repository.suggest(issuer[:5])
    assert any(kind == "institution" for kind, _, _ in by_name)


def test_filter_values_only_offer_what_the_database_holds(repository, manager):
    values = repository.filter_values()
    assert values["country"]
    with manager.session() as session:
        used = {
            str(code)
            for (code,) in session.execute(
                text(
                    "SELECT DISTINCT c.iso2 FROM bins b JOIN countries c ON c.id = b.country_id"
                )
            )
        }
    assert {code for code, _ in values["country"]} <= used


def test_boolean_criteria_are_tri_state(repository, manager):
    with manager.session() as session:
        prepaid = int(
            session.execute(text("SELECT COUNT(*) FROM bins WHERE is_prepaid = 1")).scalar() or 0
        )
    assert repository.search(AdvancedQuery(prepaid=True), PageRequest()).total == prepaid
    assert repository.search(AdvancedQuery(), PageRequest()).total > prepaid
