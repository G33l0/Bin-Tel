"""Who uses a BIN now, who stopped, and never one presented as the other.

Three false positives are what these tests exist to keep dead:

* a former issuer named as the current one;
* one of several current issuers named as though it were the only one;
* an institution attached to a BIN for some *other* reason — a parent, a
  processor — named as the issuer.

Every BIN and institution here is synthetic.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.database.engine import DatabaseManager
from app.database.schema import create_schema
from app.repositories.bin_repository import BinRepository
from app.services.ingest_service import IngestService, RawBinRecord
from app.services.lookup_service import LookupService

CASCADE = "Cascade Federal Bank"
MERIDIAN = "Meridian Trust Bank"
HARBOR = "Harbor Mutual Savings"
PACIFIC = "Pacific Coast Savings"
NORTHSHORE = "Northshore Credit Union"


def moment(year: int, month: int = 1, day: int = 1) -> datetime:
    return datetime(year, month, day, tzinfo=UTC)


@pytest.fixture
def lookup(tmp_path):
    """A database describing every shape of issuance the engine must tell apart."""
    manager = DatabaseManager(tmp_path / "bintel.sqlite")
    manager.open(create_if_missing=True)
    create_schema(manager.engine)

    rows = [
        # One current issuer, the ordinary case.
        {"bin": "410000", "issuer": CASCADE, "country": "US"},
        # Two institutions currently using one BIN.
        {"bin": "520001", "issuer": HARBOR, "country": "US"},
        {"bin": "520001", "issuer": PACIFIC, "country": "US"},
        # A succession: one stopped in 2024, the other took over.
        {
            "bin": "530001",
            "issuer": CASCADE,
            "country": "US",
            "relationship": "former_issuer",
            "effective_from": moment(2019),
            "effective_to": moment(2024, 6, 30),
        },
        {
            "bin": "530001",
            "issuer": MERIDIAN,
            "country": "GB",
            "effective_from": moment(2024, 7, 1),
        },
        # A former issuer and nobody since.
        {
            "bin": "540001",
            "issuer": NORTHSHORE,
            "country": "US",
            "relationship": "former_issuer",
            "effective_to": moment(2022, 3, 31),
        },
        # A former issuer whose end date was never recorded.
        {
            "bin": "550001",
            "issuer": NORTHSHORE,
            "country": "US",
            "relationship": "former_issuer",
        },
        # Three institutions at once.
        {"bin": "560001", "issuer": HARBOR, "country": "US"},
        {"bin": "560001", "issuer": PACIFIC, "country": "US"},
        {"bin": "560001", "issuer": MERIDIAN, "country": "GB"},
    ]
    with manager.session() as session:
        ingest = IngestService(session, source_code="test", source_name="Test")
        for row in rows:
            ingest.ingest(RawBinRecord.model_validate(row))
        session.commit()

    yield LookupService(BinRepository(manager))
    manager.close()


def record(lookup, digits):
    result = lookup.lookup(digits)
    assert result.found, f"{digits} should be in the database"
    return result, result.best


# ---------------------------------------------------------------------------
# One current issuer
# ---------------------------------------------------------------------------


def test_a_single_current_issuer_is_named_plainly(lookup):
    _, best = record(lookup, "410000")
    assert best.issuer_name == CASCADE
    assert best.issuer_is_known
    assert not best.has_shared_issuance
    assert best.former_issuers == ()


# ---------------------------------------------------------------------------
# Several current issuers
# ---------------------------------------------------------------------------


def test_two_current_issuers_are_both_named(lookup):
    _, best = record(lookup, "520001")
    names = {item.display_name for item in best.current_issuers}
    assert names == {HARBOR, PACIFIC}
    assert best.has_shared_issuance


def test_neither_of_two_current_issuers_is_presented_as_the_only_one(lookup):
    """The headline must not silently pick a winner."""
    _, best = record(lookup, "520001")
    assert best.issuer_name != HARBOR
    assert best.issuer_name != PACIFIC
    assert HARBOR in best.issuer_name
    assert PACIFIC in best.issuer_name


def test_three_current_issuers_are_all_named(lookup):
    _, best = record(lookup, "560001")
    names = {item.display_name for item in best.current_issuers}
    assert names == {HARBOR, PACIFIC, MERIDIAN}
    for name in names:
        assert name in best.issuer_name


def test_several_current_issuers_are_reported_as_a_conflict(lookup):
    """Two banks on one BIN is a disagreement the reader has to see."""
    result, _ = record(lookup, "520001")
    assert result.is_conflicted
    assert result.confidence_level == "conflicted"


def test_a_shared_bin_is_never_reported_at_high_confidence(lookup):
    result, _ = record(lookup, "520001")
    assert result.confidence_score < 0.9


# ---------------------------------------------------------------------------
# Former issuers
# ---------------------------------------------------------------------------


def test_a_former_issuer_is_never_named_as_the_current_one(lookup):
    """The single most misleading thing this application could say."""
    _, best = record(lookup, "540001")
    assert best.issuer_name != NORTHSHORE
    assert not best.issuer_is_known
    assert best.current_issuers == ()


def test_a_former_issuer_is_still_reported_together_with_when_it_stopped(lookup):
    _, best = record(lookup, "540001")
    former = best.former_issuers
    assert [item.display_name for item in former] == [NORTHSHORE]
    assert former[0].effective_to.date() == moment(2022, 3, 31).date()
    assert "2022-03-31" in former[0].ended_label


def test_a_former_issuer_with_no_end_date_says_so_rather_than_inventing_one(lookup):
    _, best = record(lookup, "550001")
    assert not best.issuer_is_known
    former = best.former_issuers
    assert former[0].display_name == NORTHSHORE
    assert former[0].effective_to is None
    assert "not recorded" in former[0].ended_label


def test_a_former_issuer_is_never_current_even_without_an_end_date(lookup):
    """"Former issuer, current" is a contradiction, not a state."""
    _, best = record(lookup, "550001")
    relationship = best.institutions[0]
    assert relationship.relationship_type == "former_issuer"
    assert not relationship.is_current
    assert not relationship.is_currently_issuing


def test_a_succession_names_the_successor_and_reports_the_predecessor(lookup):
    _, best = record(lookup, "530001")
    assert best.issuer_name == MERIDIAN
    assert best.issuer_is_known
    former = best.former_issuers
    assert [item.display_name for item in former] == [CASCADE]
    assert former[0].effective_to.date() == moment(2024, 6, 30).date()


def test_a_succession_is_not_a_conflict(lookup):
    """One bank replacing another over time is a timeline, not a disagreement."""
    result, _ = record(lookup, "530001")
    assert not result.is_conflicted
    assert result.conflicting_institutions == ()


def test_a_former_issuer_never_supplies_the_records_issuer_fields(lookup):
    """If nobody issues now, the legal name and website are unknown too."""
    _, best = record(lookup, "540001")
    assert best.primary_institution is None
    assert best.issuer_legal_name == "Unknown"
    fields = dict(best.to_field_pairs())
    assert fields["Issuer"] == "Unknown"
    assert NORTHSHORE not in fields.values()


# ---------------------------------------------------------------------------
# Never a false positive
# ---------------------------------------------------------------------------


def test_an_unrecorded_bin_names_nobody(lookup):
    result = lookup.lookup("999999")
    assert not result.found
    assert result.best is None


def test_a_neighbouring_bin_is_never_borrowed(lookup):
    """410000 being one away from 410001 says nothing about who issues it."""
    assert not lookup.lookup("410001").found


def test_every_named_institution_is_backed_by_a_stored_relationship(lookup):
    """Nothing is named that the database does not actually record."""
    for digits in ("410000", "520001", "530001", "540001", "560001"):
        _, best = record(lookup, digits)
        stored = {item.display_name for item in best.institutions}
        for item in best.current_issuers:
            assert item.display_name in stored
        for item in best.former_issuers:
            assert item.display_name in stored
        if best.issuer_is_known:
            for name in best.issuer_name.split(" · "):
                assert name in stored


# ---------------------------------------------------------------------------
# Exports carry the same tense the screen does
# ---------------------------------------------------------------------------


def test_a_text_export_never_names_a_former_issuer_as_the_issuer(lookup):
    from app.services.export_service import ExportFormat, ExportService

    _, best = record(lookup, "540001")
    text = ExportService().render_record(best, ExportFormat.TXT)
    assert "Issuer:" in text
    issuer_line = next(
        line for line in text.splitlines() if line.startswith("Issuer:")
    )
    assert NORTHSHORE not in issuer_line
    assert NORTHSHORE in text, "the former issuer is still reported, just not as current"
    assert "2022-03-31" in text


def test_a_json_export_separates_current_from_former(lookup):
    import json

    from app.services.export_service import ExportFormat, ExportService

    _, best = record(lookup, "530001")
    payload = json.loads(ExportService().render_record(best, ExportFormat.JSON))
    assert payload["current_issuers"] == [MERIDIAN]
    assert [item["name"] for item in payload["former_issuers"]] == [CASCADE]
    assert payload["former_issuers"][0]["effective_to"].startswith("2024-06-30")


def test_a_json_export_lists_every_current_issuer_of_a_shared_bin(lookup):
    import json

    from app.services.export_service import ExportFormat, ExportService

    _, best = record(lookup, "520001")
    payload = json.loads(ExportService().render_record(best, ExportFormat.JSON))
    assert set(payload["current_issuers"]) == {HARBOR, PACIFIC}
    assert payload["former_issuers"] == []


def test_the_csv_export_carries_a_former_issuer_column(lookup):
    from app.services.export_service import ExportFormat, ExportService

    _, best = record(lookup, "530001")
    text = ExportService().render_record(best, ExportFormat.CSV)
    assert "Former Issuers" in text
    assert CASCADE in text


# ---------------------------------------------------------------------------
# Standing outranks confidence when the record's own attributes are merged
# ---------------------------------------------------------------------------


def test_the_record_takes_its_country_from_the_current_issuer(tmp_path):
    """Not from the bank that stopped using it, however it was ordered."""
    manager = DatabaseManager(tmp_path / "bintel.sqlite")
    manager.open(create_if_missing=True)
    create_schema(manager.engine)
    with manager.session() as session:
        ingest = IngestService(session, source_code="test", source_name="Test")
        # The historical claim is read first, deliberately.
        ingest.ingest(
            RawBinRecord.model_validate(
                {
                    "bin": "570001",
                    "issuer": CASCADE,
                    "country": "US",
                    "relationship": "former_issuer",
                    "effective_to": moment(2024, 6, 30),
                }
            )
        )
        ingest.ingest(
            RawBinRecord.model_validate(
                {"bin": "570001", "issuer": MERIDIAN, "country": "GB"}
            )
        )
        session.commit()

    lookup = LookupService(BinRepository(manager))
    best = lookup.lookup("570001").best
    assert best.issuer_name == MERIDIAN
    assert best.country is not None and best.country.iso2 == "GB"
    manager.close()


def test_a_former_issuer_never_overwrites_a_current_records_attributes(tmp_path):
    """The same, with the rows the other way round."""
    manager = DatabaseManager(tmp_path / "bintel.sqlite")
    manager.open(create_if_missing=True)
    create_schema(manager.engine)
    with manager.session() as session:
        ingest = IngestService(session, source_code="test", source_name="Test")
        ingest.ingest(
            RawBinRecord.model_validate(
                {"bin": "580001", "issuer": MERIDIAN, "country": "GB"}
            )
        )
        ingest.ingest(
            RawBinRecord.model_validate(
                {
                    "bin": "580001",
                    "issuer": CASCADE,
                    "country": "US",
                    "relationship": "former_issuer",
                    "effective_to": moment(2024, 6, 30),
                }
            )
        )
        session.commit()

    lookup = LookupService(BinRepository(manager))
    best = lookup.lookup("580001").best
    assert best.issuer_name == MERIDIAN
    assert best.country is not None and best.country.iso2 == "GB"
    manager.close()
