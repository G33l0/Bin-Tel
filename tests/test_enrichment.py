"""Filling gaps from evidence, and refusing to fill them from anything else.

The line these tests police: a value may be *restated* (the same institution's
details, applied to its own other rows) or *derived from a published standard*
(the scheme a prefix belongs to). It may never be invented.

Every BIN and institution here is synthetic.
"""

from __future__ import annotations

import pytest

from app.database.engine import DatabaseManager
from app.normalizers.iin_ranges import candidates_for_prefix, network_for_prefix
from app.repositories.bin_repository import BinRepository
from app.services.lookup_service import LookupService
from app.services.rebuild_service import RebuildService

HEADER = "bin,bank,network,card_type,country,city,website,legal_name"


@pytest.fixture
def build(tmp_path):
    """Rebuild from a list and hand back the outcome plus a lookup service."""
    database = tmp_path / "bintel.sqlite"
    listing = tmp_path / "bin-list.csv"
    manager = DatabaseManager(database)
    service = RebuildService(manager, database, staging_dir=tmp_path / "staging")

    def run(*rows: str, header: str = HEADER):
        listing.write_text(
            f"{header}\n" + "".join(f"{row}\n" for row in rows), encoding="utf-8"
        )
        outcome = service.rebuild(listing)
        return outcome, LookupService(BinRepository(manager))

    yield run
    manager.close()


# ---------------------------------------------------------------------------
# The published allocation table
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("digits,expected", [
    ("410000", "visa"),
    ("41000012", "visa"),
    ("510000", "mastercard"),
    ("550000", "mastercard"),
    ("222100", "mastercard"),
    ("272000", "mastercard"),
    ("340001", "amex"),
    ("370000", "amex"),
    ("352800", "jcb"),
    ("358900", "jcb"),
    ("601100", "discover"),
    ("644000", "discover"),
    ("650000", "discover"),
    ("620000", "unionpay"),
    ("300000", "diners"),
    ("360000", "diners"),
    ("220000", "mir"),
])
def test_a_published_prefix_resolves_to_its_scheme(digits, expected):
    assert network_for_prefix(digits).network == expected


@pytest.mark.parametrize("digits", ["272100", "359000", "700000", "999999", "219999"])
def test_an_unallocated_prefix_resolves_to_nothing(digits):
    """Outside every published range is Unknown, never the nearest scheme."""
    match = network_for_prefix(digits)
    assert match.network is None
    assert match.candidates == ()


@pytest.mark.parametrize("digits", ["622126", "622202", "622925"])
def test_a_prefix_two_schemes_publish_is_refused_not_guessed(digits):
    """Discover and UnionPay both publish 622126-622925."""
    match = network_for_prefix(digits)
    assert match.network is None
    assert match.is_ambiguous
    assert set(match.candidates) == {"discover", "unionpay"}


@pytest.mark.parametrize("digits", ["622125", "622926"])
def test_the_edges_of_the_overlap_are_not_ambiguous(digits):
    assert network_for_prefix(digits).network == "unionpay"


def test_a_non_numeric_prefix_matches_nothing():
    assert candidates_for_prefix("abcdef") == ()
    assert candidates_for_prefix("") == ()


# ---------------------------------------------------------------------------
# Deriving the network during a rebuild
# ---------------------------------------------------------------------------


def test_a_blank_network_is_filled_from_the_bin(build):
    outcome, lookup = build("410000,Cascade Bank,,,US,,,")
    assert outcome.enrichment.networks_derived == 1
    record = lookup.lookup("410000").best
    assert record.network is not None
    assert record.network.label == "Visa"


def test_a_stated_network_is_never_overwritten(build):
    """The list is the authority; the table only fills a blank."""
    _, lookup = build("410000,Cascade Bank,maestro,,US,,,")
    record = lookup.lookup("410000").best
    assert record.network is not None
    assert record.network.label == "Maestro"


def test_an_ambiguous_prefix_is_left_unset_and_counted(build):
    outcome, lookup = build("622202,Great Wall Financial,,,CN,,,")
    assert outcome.enrichment.networks_derived == 0
    assert outcome.enrichment.networks_ambiguous == 1
    assert lookup.lookup("622202").best.network is None


def test_an_unallocated_prefix_is_left_unset(build):
    outcome, lookup = build("999999,Somewhere Bank,,,US,,,")
    assert outcome.enrichment.networks_derived == 0
    assert lookup.lookup("999999").best.network is None


# ---------------------------------------------------------------------------
# Restating what one institution's other rows already said
# ---------------------------------------------------------------------------


def test_one_rows_country_reaches_the_same_institutions_other_bins(build):
    outcome, lookup = build(
        "410000,Cascade Federal Bank,,,US,Seattle,https://cascade.example,",
        "410001,Cascade Federal Bank,,,,,,",
        "410002,Cascade Federal Bank,,,,,,",
    )
    assert outcome.enrichment.countries_propagated == 2
    for digits in ("410000", "410001", "410002"):
        record = lookup.lookup(digits).best
        assert record.country is not None and record.country.iso2 == "US"


def test_one_rows_website_reaches_the_same_institutions_other_bins(build):
    _, lookup = build(
        "410000,Cascade Federal Bank,,,US,Seattle,https://cascade.example,",
        "410001,Cascade Federal Bank,,,,,,",
    )
    record = lookup.lookup("410001").best
    assert record.current_issuers[0].website == "https://cascade.example"


def test_case_and_spacing_do_not_split_one_institution(build):
    _, lookup = build(
        "410000,Cascade Federal Bank,,,US,,https://cascade.example,",
        "410001,cascade federal bank,,,,,,",
        "410002,CASCADE  FEDERAL  BANK,,,,,,",
    )
    names = {
        lookup.lookup(digits).best.issuer_name
        for digits in ("410000", "410001", "410002")
    }
    assert len(names) == 1


def test_a_country_is_never_taken_from_a_different_institution(build):
    """Two banks are two banks, however adjacent their BINs."""
    _, lookup = build(
        "410000,Cascade Federal Bank,,,US,,,",
        "410001,Northshore Credit Union,,,,,,",
    )
    assert lookup.lookup("410001").best.country is None


def test_a_country_is_never_taken_from_a_former_issuer(build):
    """A bank that stopped in 2024 does not say where the BIN is issued now."""
    outcome, lookup = build(
        "530001,Old Bank,,former_issuer,,2024-06-30",
        "530001,New Bank,,,,",
        header="bin,bank,country,relationship,effective_from,effective_to",
    )
    _ = outcome
    record = lookup.lookup("530001").best
    assert record.issuer_name == "New Bank"
    assert record.country is None


# ---------------------------------------------------------------------------
# The currency follows the country
# ---------------------------------------------------------------------------


def test_the_currency_is_derived_from_the_country(build):
    _, lookup = build(
        "410000,Cascade Bank,,,US,,,",
        "530001,Meridian Trust,,,GB,,,",
        "352800,Pacific Programme,,,JP,,,",
    )
    assert lookup.lookup("410000").best.currency_code == "USD"
    assert lookup.lookup("530001").best.currency_code == "GBP"
    assert lookup.lookup("352800").best.currency_code == "JPY"


def test_no_country_means_no_currency(build):
    _, lookup = build("410000,Cascade Bank,,,,,,")
    assert lookup.lookup("410000").best.currency_code is None


# ---------------------------------------------------------------------------
# What is never invented
# ---------------------------------------------------------------------------


def test_a_website_that_appears_nowhere_stays_unknown(build):
    """A plausible guess is worse than Unknown."""
    _, lookup = build("410000,Cascade Federal Bank,,,US,,,")
    record = lookup.lookup("410000").best
    assert record.current_issuers[0].website is None
    assert dict(record.to_field_pairs())["Website"] == "Unknown"


def test_the_card_type_is_never_derived_from_the_bin(build):
    """Nothing about the digits establishes credit versus debit."""
    _, lookup = build("410000,Cascade Bank,,,US,,,")
    assert lookup.lookup("410000").best.card_type is None


def test_the_issuer_is_never_derived_from_the_bin(build):
    """A neighbouring BIN's bank is never borrowed to fill a gap."""
    _, lookup = build("410000,Cascade Federal Bank,,,US,,,")
    assert not lookup.lookup("410001").found


def test_a_legal_name_that_appears_nowhere_stays_unknown(build):
    _, lookup = build("410000,Cascade Federal Bank,,,US,,,")
    assert lookup.lookup("410000").best.issuer_legal_name == "Unknown"


# ---------------------------------------------------------------------------
# Everything filled in is traceable
# ---------------------------------------------------------------------------


def test_every_derived_value_is_written_to_the_audit_trail(tmp_path):
    import sqlite3

    database = tmp_path / "bintel.sqlite"
    listing = tmp_path / "bin-list.csv"
    listing.write_text(
        f"{HEADER}\n"
        "410000,Cascade Federal Bank,,,US,Seattle,https://cascade.example,\n"
        "410001,Cascade Federal Bank,,,,,,\n"
        "340001,Apex Charge Services,,,US,,,\n",
        encoding="utf-8",
    )
    manager = DatabaseManager(database)
    RebuildService(manager, database, staging_dir=tmp_path / "staging").rebuild(listing)
    manager.close()

    rows = sqlite3.connect(database).execute(
        "SELECT entity_key, field, normalized_value, rule FROM normalization_events "
        "WHERE rule LIKE 'iin:%' OR rule LIKE 'bin:%'"
    ).fetchall()
    by_rule = {(key, field): (value, rule) for key, field, value, rule in rows}

    assert by_rule[("340001", "network")] == ("amex", "iin:amex:34")
    assert by_rule[("410000", "network")][1] == "iin:visa:4"
    assert by_rule[("410001", "country_id")][1] == "bin:issuer-country"


def test_the_report_counts_what_it_actually_changed(build):
    outcome, _ = build(
        "410000,Cascade Federal Bank,,,US,,https://cascade.example,",
        "410001,Cascade Federal Bank,,,,,,",
        "622202,Great Wall Financial,,,CN,,,",
    )
    enrichment = outcome.enrichment
    assert enrichment.networks_derived == 2  # the two Visa rows, not the dual-claimed one
    assert enrichment.networks_ambiguous == 1
    assert enrichment.countries_propagated == 1
    assert enrichment.total == (
        enrichment.networks_derived
        + enrichment.currencies_derived
        + enrichment.institution_fields_filled
        + enrichment.countries_propagated
    )


# ---------------------------------------------------------------------------
# Every BIN in the list comes back, and comes back indexed
# ---------------------------------------------------------------------------


def test_every_bin_in_a_large_list_resolves(tmp_path):
    """A BIN the list names is never Unknown, and never raises."""
    prefixes = ["4", "51", "55", "2221", "34", "37", "3528", "6011", "65", "644", "62"]
    rows: list[str] = []
    expected: list[str] = []
    for index, prefix in enumerate(prefix for prefix in prefixes for _ in range(60)):
        digits = f"{prefix}{index:0{(8 if index % 2 else 6) - len(prefix)}d}"[:8]
        if len(digits) not in (6, 8):
            continue
        expected.append(digits)
        # Most rows carry nothing but the BIN and the bank, as a real list does.
        rows.append(f"{digits},Institution {index % 40:02d},,,,,,")

    database = tmp_path / "bintel.sqlite"
    listing = tmp_path / "bin-list.csv"
    listing.write_text(f"{HEADER}\n" + "".join(f"{row}\n" for row in rows), encoding="utf-8")

    manager = DatabaseManager(database)
    RebuildService(manager, database, staging_dir=tmp_path / "staging").rebuild(listing)
    lookup = LookupService(BinRepository(manager))

    missing = [digits for digits in expected if not lookup.lookup(digits).found]
    assert missing == [], f"{len(missing)} listed BIN(s) did not resolve"

    without_issuer = [
        digits for digits in expected if not lookup.lookup(digits).best.issuer_is_known
    ]
    assert without_issuer == []
    manager.close()


def test_the_lookup_queries_use_indexes_rather_than_scanning(tmp_path):
    """A table scan here would degrade with every BIN added."""
    import sqlite3

    # Enough rows that a scan is genuinely the wrong plan: with a handful,
    # SQLite prefers scanning and is right to.
    database = tmp_path / "bintel.sqlite"
    listing = tmp_path / "bin-list.csv"
    rows = "".join(
        f"41{index:04d},Institution {index % 40:02d},,,US,,,\n" for index in range(400)
    )
    listing.write_text(f"{HEADER}\n{rows}", encoding="utf-8")
    manager = DatabaseManager(database)
    RebuildService(manager, database, staging_dir=tmp_path / "staging").rebuild(listing)
    manager.close()

    connection = sqlite3.connect(database)
    queries = {
        "bins by value": "SELECT * FROM bins WHERE bin_int BETWEEN 41000000 AND 41000099",
        "bins by span": "SELECT * FROM bins WHERE span_low<=41000012 AND span_high>=41000012",
        "links by bin": "SELECT * FROM bin_institutions WHERE bin_id=1 AND is_current=1",
    }
    for label, query in queries.items():
        plan = " ".join(
            row[3] for row in connection.execute("EXPLAIN QUERY PLAN " + query)
        )
        assert "USING INDEX" in plan, f"{label} is not indexed: {plan}"
        assert "SCAN" not in plan, f"{label} falls back to a scan: {plan}"


def test_a_rebuild_is_idempotent(tmp_path):
    """Building the same list twice derives the same values, not double them."""
    database = tmp_path / "bintel.sqlite"
    listing = tmp_path / "bin-list.csv"
    listing.write_text(
        f"{HEADER}\n"
        "410000,Cascade Federal Bank,,,US,Seattle,https://cascade.example,\n"
        "410001,Cascade Federal Bank,,,,,,\n",
        encoding="utf-8",
    )
    manager = DatabaseManager(database)
    service = RebuildService(manager, database, staging_dir=tmp_path / "staging")
    first = service.rebuild(listing)
    second = service.rebuild(listing)

    assert first.enrichment.networks_derived == second.enrichment.networks_derived
    assert first.enrichment.countries_propagated == second.enrichment.countries_propagated
    assert first.distinct_bins == second.distinct_bins
    manager.close()


def test_jcbs_legacy_allocations_stop_1800_reading_as_uatp():
    """JCB issued under 1800 and 2131 before moving to 35xx.

    Both sit inside major industry identifier 1, which UATP also publishes.
    Two schemes claim the prefix, so the honest answer is that the digits do
    not settle it — not the one scheme that happened to be listed first.
    """
    from app.normalizers.iin_ranges import network_for_prefix

    contested = network_for_prefix("180000")
    assert contested.is_ambiguous
    assert set(contested.candidates) == {"jcb", "uatp"}
    assert contested.network is None

    # 2131 is JCB's alone: Mastercard's 2-series starts at 2221.
    assert network_for_prefix("213100").network == "jcb"
    # And an ordinary airline prefix is still UATP's.
    assert network_for_prefix("111100").network == "uatp"
