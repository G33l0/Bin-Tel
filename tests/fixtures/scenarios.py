"""Synthetic BIN datasets for the lookup engine.

Every value here is invented. The issuer names are fictional institutions, the
prefixes are chosen from ranges that are not live allocations, and nothing in
this module is derived from a proprietary dataset. No cardholder data, no full
card numbers, no payment credentials.

Each builder writes a real SQLite database with the production schema, so tests
exercise the same queries and indexes the application uses.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from app.database.engine import DatabaseManager
from app.database.schema import analyze, create_schema, write_metadata
from app.models.entities import (
    DatabaseMetadata,
    RangeType,
    RelationshipType,
)
from app.services.ingest_service import IngestService, RawBinRecord

#: Fictional institutions used throughout the scenarios.
MERIDIAN = "Meridian Trust Bank"
MERIDIAN_LEGAL = "Meridian Trust Bank, N.A."
NORTHSHORE = "Northshore Credit Union"
CASCADE = "Cascade Financial Group"
CASCADE_SUB = "Cascade Retail Bank"
HARBOR = "Harborview Savings"
PACIFIC = "Pacific Rim Bank"


def _utc(year: int, month: int = 1, day: int = 1) -> datetime:
    return datetime(year, month, day, tzinfo=UTC)


def build(path: Path, *, version: str = "2026.01.1") -> Path:
    """Write the full scenario database and return its path."""
    path.unlink(missing_ok=True)
    for suffix in ("-wal", "-shm"):
        path.with_name(path.name + suffix).unlink(missing_ok=True)

    manager = DatabaseManager(path)
    manager.open(create_if_missing=True)
    create_schema(manager.engine)

    with manager.transaction() as session:
        ingest = IngestService(
            session, source_code="scenarios", source_name="Synthetic scenarios"
        )
        ingest.seed_reference_data()
        for record in records():
            ingest.ingest(record)

    _link_extras(manager)
    analyze(manager.engine)

    with manager.transaction() as session:
        write_metadata(
            session,
            {
                DatabaseMetadata.VERSION: version,
                DatabaseMetadata.PUBLISHER: "Bin-Tel Project",
                DatabaseMetadata.NOTES: "Synthetic scenarios for the lookup engine.",
            },
        )
    manager.close()
    for suffix in ("-wal", "-shm"):
        path.with_name(path.name + suffix).unlink(missing_ok=True)
    return path


def records() -> list[RawBinRecord]:
    """The scenarios, each one a case the engine has to get right."""
    return [
        # -- 1. one BIN, one institution, nothing complicated -------------
        RawBinRecord(
            bin="510001",
            network="mastercard",
            card_type="credit",
            issuer=HARBOR,
            issuer_legal_name="Harborview Savings Bank",
            country="US",
            state="MA",
            city="Boston",
            postal_code="02110",
            website="harborview.example",
            confidence=0.95,
        ),
        # -- 2. one institution, many BINs, several networks/countries ----
        *(
            RawBinRecord(
                bin=prefix,
                network=network,
                card_type=card_type,
                issuer=MERIDIAN,
                issuer_legal_name=MERIDIAN_LEGAL,
                country=country,
                city=city,
                website="meridiantrust.example",
                confidence=0.95,
            )
            for prefix, network, card_type, country, city in (
                ("400001", "visa", "credit", "US", "New York"),
                ("400002", "visa", "debit", "US", "New York"),
                ("520001", "mastercard", "credit", "US", "New York"),
                ("400003", "visa", "credit", "GB", "London"),
            )
        ),
        # -- 3. a six-digit root with eight-digit assignments beneath it,
        #       belonging to *different* institutions. Answering a
        #       ``41000012`` query from the root would name the wrong bank.
        RawBinRecord(
            bin="410000",
            network="visa",
            card_type="credit",
            issuer=CASCADE,
            country="US",
            city="Seattle",
            website="cascadefg.example",
            confidence=0.8,
        ),
        RawBinRecord(
            bin="41000012",
            network="visa",
            card_type="debit",
            issuer=NORTHSHORE,
            issuer_legal_name="Northshore Federal Credit Union",
            country="US",
            city="Seattle",
            website="northshorecu.example",
            confidence=0.95,
        ),
        RawBinRecord(
            bin="41000034",
            network="visa",
            card_type="credit",
            issuer=CASCADE_SUB,
            country="US",
            city="Portland",
            website="cascaderetail.example",
            confidence=0.95,
        ),
        # -- 4. an account range, narrower than the root it sits under ----
        RawBinRecord(
            bin="450000",
            bin_high="450099",
            network="visa",
            card_type="credit",
            issuer=PACIFIC,
            country="SG",
            city="Singapore",
            website="pacificrim.example",
            confidence=0.9,
            range_type=RangeType.ISSUER_RANGE.value,
        ),
        RawBinRecord(
            bin="45000050",
            bin_high="45000059",
            network="visa",
            card_type="credit",
            issuer=HARBOR,
            country="SG",
            city="Singapore",
            website="harborview.example",
            confidence=0.95,
            range_type=RangeType.ACCOUNT_RANGE.value,
        ),
        # -- 5. a historical issuer change on one prefix -------------------
        RawBinRecord(
            bin="530001",
            network="mastercard",
            card_type="credit",
            issuer=CASCADE,
            country="US",
            city="Seattle",
            confidence=0.9,
            effective_from=_utc(2020),
            effective_to=_utc(2024),
        ),
        RawBinRecord(
            bin="530001",
            network="mastercard",
            card_type="credit",
            issuer=MERIDIAN,
            issuer_legal_name=MERIDIAN_LEGAL,
            country="US",
            city="New York",
            confidence=0.95,
            effective_from=_utc(2024),
        ),
        # -- 6. a prefix present in the database naming nobody --------------
        RawBinRecord(
            bin="600001",
            network="discover",
            card_type="credit",
            country="US",
            confidence=0.5,
        ),
        # -- 7. an alias, so an abbreviation resolves to the same issuer ---
        RawBinRecord(
            bin="400004",
            network="visa",
            card_type="credit",
            issuer="MTB",
            country="US",
            aliases=[MERIDIAN],
            confidence=0.7,
        ),
        # -- 8. missing fields, so completeness metrics have something to
        #       measure and the interface has an unknown to render ---------
        RawBinRecord(bin="620001", issuer=PACIFIC, confidence=0.6),
    ]


def _link_extras(manager: DatabaseManager) -> None:
    """Relationships the ingest path does not express on its own.

    Two things the record shape cannot carry: a parent/subsidiary link between
    institutions, and a second *equally specific* claim on one prefix, which is
    what a genuine conflict looks like.
    """
    from sqlalchemy import select

    from app.models.entities import (
        Bin,
        BinInstitution,
        Institution,
        InstitutionLinkType,
        InstitutionRelationship,
    )

    with manager.transaction() as session:

        def institution(name: str) -> Institution:
            return session.execute(
                select(Institution).where(Institution.display_name == name)
            ).scalar_one()

        group = institution(CASCADE)
        subsidiary = institution(CASCADE_SUB)
        session.add(
            InstitutionRelationship(
                institution_id=group.id,
                related_institution_id=subsidiary.id,
                relationship_type=InstitutionLinkType.SUBSIDIARY.value,
                is_current=True,
                confidence=0.95,
            )
        )

        # A second issuing claim on 520001, at the same specificity as the
        # first. Neither is dropped; the lookup reports the disagreement.
        contested = session.execute(select(Bin).where(Bin.bin == "520001")).scalar_one()
        rival = institution(PACIFIC)
        session.add(
            BinInstitution(
                bin_id=contested.id,
                institution_id=rival.id,
                relationship_type=RelationshipType.ISSUER.value,
                is_primary=False,
                is_current=True,
                confidence=0.85,
            )
        )
