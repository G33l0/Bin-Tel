"""SQLAlchemy ORM entities for the Bin-Tel intelligence database.

Design notes
------------
* The schema is normalised: a country, a network and an institution each exist
  exactly once and are referenced by id.
* A BIN is **not** assumed to belong to exactly one bank. ``bin_institutions``
  is a many-to-many association carrying a relationship type, a primary flag
  and a confidence score, so a BIN can legitimately name an issuer, a parent
  group and a processor at the same time.
* Provenance (``sources``, ``bin_claims``, ``conflicts``,
  ``normalization_events``) lives in the database for data-quality work but is
  never rendered in a normal lookup result.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    """Declarative base for every Bin-Tel table."""


# ---------------------------------------------------------------------------
# Controlled vocabularies
# ---------------------------------------------------------------------------


class CardType(StrEnum):
    CREDIT = "credit"
    DEBIT = "debit"
    PREPAID = "prepaid"
    CHARGE = "charge"
    DEFERRED_DEBIT = "deferred_debit"
    UNKNOWN = "unknown"

    @property
    def label(self) -> str:
        return {
            CardType.CREDIT: "Credit",
            CardType.DEBIT: "Debit",
            CardType.PREPAID: "Prepaid",
            CardType.CHARGE: "Charge",
            CardType.DEFERRED_DEBIT: "Deferred Debit",
            CardType.UNKNOWN: "Unknown",
        }[self]


class FundingType(StrEnum):
    CREDIT = "credit"
    DEBIT = "debit"
    PREPAID = "prepaid"
    CHARGE = "charge"
    UNKNOWN = "unknown"

    @property
    def label(self) -> str:
        return self.value.capitalize() if self is not FundingType.UNKNOWN else "Unknown"


class RecordStatus(StrEnum):
    ACTIVE = "active"
    INACTIVE = "inactive"
    RETIRED = "retired"
    REASSIGNED = "reassigned"
    UNKNOWN = "unknown"

    @property
    def label(self) -> str:
        return self.value.capitalize() if self is not RecordStatus.UNKNOWN else "Unknown"


class PrefixType(StrEnum):
    """How a stored prefix was assigned.

    The distinction matters: a six-digit root and an eight-digit assignment
    under it are *different* allocations, and in modern eight-digit issuance
    the root may be shared by several issuers. Collapsing one into the other
    would attribute a card to the wrong institution.
    """

    #: A six-digit IIN assigned in its own right.
    ROOT = "root"
    #: An eight-digit assignment, which may sit under a shared six-digit root.
    EXTENDED = "extended"
    #: An account range rather than a single prefix.
    RANGE = "range"

    @property
    def label(self) -> str:
        return {
            PrefixType.ROOT: "6-digit IIN",
            PrefixType.EXTENDED: "8-digit BIN",
            PrefixType.RANGE: "Account range",
        }[self]


class RangeType(StrEnum):
    """What kind of allocation a range row describes."""

    #: A scheme-assigned issuer range.
    ISSUER_RANGE = "issuer_range"
    #: A network account range, the most specific authoritative allocation.
    ACCOUNT_RANGE = "account_range"
    #: The expanded block under a six-digit root.
    ROOT_BLOCK = "root_block"
    UNKNOWN = "unknown"

    @property
    def label(self) -> str:
        return self.value.replace("_", " ").title()


class RelationshipType(StrEnum):
    """Why an institution is attached to a BIN.

    These describe *issuance and ownership*, never transaction routing. Nothing
    here should be read as saying where a transaction is switched.
    """

    ISSUER = "issuer"
    FORMER_ISSUER = "former_issuer"
    PARENT = "parent"
    SUBSIDIARY = "subsidiary"
    PROGRAM_ISSUER = "program_issuer"
    PROCESSOR_ASSOCIATED = "processor_associated"
    PROCESSOR = "processor"
    PROGRAM_MANAGER = "program_manager"
    ACQUIRER = "acquirer"
    LICENSEE = "licensee"
    PREDECESSOR = "predecessor"
    UNKNOWN = "unknown"

    @property
    def label(self) -> str:
        return {
            RelationshipType.ISSUER: "Issuer",
            RelationshipType.FORMER_ISSUER: "Former issuer",
            RelationshipType.PROGRAM_ISSUER: "Program issuer",
            RelationshipType.PROCESSOR_ASSOCIATED: "Processor (associated)",
            RelationshipType.UNKNOWN: "Unknown relationship",
        }.get(self, self.value.replace("_", " ").title())

    @property
    def is_issuing(self) -> bool:
        """Whether this relationship asserts the institution issues the card."""
        return self in (
            RelationshipType.ISSUER,
            RelationshipType.PROGRAM_ISSUER,
        )


class InstitutionLinkType(StrEnum):
    """How two institutions are related to each other."""

    PARENT = "parent"
    SUBSIDIARY = "subsidiary"
    PREDECESSOR = "predecessor"
    SUCCESSOR = "successor"
    BRAND_OF = "brand_of"
    PROCESSOR_FOR = "processor_for"

    @property
    def label(self) -> str:
        return self.value.replace("_", " ").title()

    @property
    def inverse(self) -> InstitutionLinkType:
        return {
            InstitutionLinkType.PARENT: InstitutionLinkType.SUBSIDIARY,
            InstitutionLinkType.SUBSIDIARY: InstitutionLinkType.PARENT,
            InstitutionLinkType.PREDECESSOR: InstitutionLinkType.SUCCESSOR,
            InstitutionLinkType.SUCCESSOR: InstitutionLinkType.PREDECESSOR,
            InstitutionLinkType.BRAND_OF: InstitutionLinkType.BRAND_OF,
            InstitutionLinkType.PROCESSOR_FOR: InstitutionLinkType.PROCESSOR_FOR,
        }[self]


class StagingStatus(StrEnum):
    """Where a staged record has reached in the promotion pipeline."""

    RECEIVED = "received"
    NORMALIZED = "normalized"
    VALIDATED = "validated"
    RESOLVED = "resolved"
    CONFLICTED = "conflicted"
    REJECTED = "rejected"
    PROMOTED = "promoted"

    @property
    def label(self) -> str:
        return self.value.capitalize()


class InstitutionType(StrEnum):
    BANK = "bank"
    CREDIT_UNION = "credit_union"
    FINTECH = "fintech"
    ISSUER = "issuer"
    PROCESSOR = "processor"
    GOVERNMENT = "government"
    OTHER = "other"
    UNKNOWN = "unknown"

    @property
    def label(self) -> str:
        return self.value.replace("_", " ").title() if self is not InstitutionType.UNKNOWN else "Unknown"


class AliasType(StrEnum):
    TRADING_NAME = "trading_name"
    FORMER_NAME = "former_name"
    ABBREVIATION = "abbreviation"
    LOCAL_NAME = "local_name"
    BRAND = "brand"
    VARIANT = "variant"


class ConflictStatus(StrEnum):
    OPEN = "open"
    RESOLVED = "resolved"
    IGNORED = "ignored"


class UpdateStatus(StrEnum):
    STARTED = "started"
    DOWNLOADING = "downloading"
    VERIFYING = "verifying"
    INSTALLING = "installing"
    SUCCESS = "success"
    FAILED = "failed"
    CANCELLED = "cancelled"
    ROLLED_BACK = "rolled_back"


# ---------------------------------------------------------------------------
# Reference data
# ---------------------------------------------------------------------------


class Country(Base):
    __tablename__ = "countries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    iso2: Mapped[str] = mapped_column(String(2), nullable=False, unique=True)
    iso3: Mapped[str | None] = mapped_column(String(3))
    numeric_code: Mapped[str | None] = mapped_column(String(3))
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    currency_code: Mapped[str | None] = mapped_column(String(3))
    region: Mapped[str | None] = mapped_column(String(64))
    subregion: Mapped[str | None] = mapped_column(String(64))
    flag_emoji: Mapped[str | None] = mapped_column(String(8))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    bins: Mapped[list[Bin]] = relationship(back_populates="country")
    institutions: Mapped[list[Institution]] = relationship(back_populates="country")

    __table_args__ = (
        Index("ix_countries_iso3", "iso3"),
        Index("ix_countries_name", "name"),
        CheckConstraint("length(iso2) = 2", name="ck_countries_iso2_length"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Country {self.iso2} {self.name!r}>"


class Network(Base):
    __tablename__ = "networks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    display_name: Mapped[str] = mapped_column(String(64), nullable=False)
    scheme_type: Mapped[str | None] = mapped_column(String(32))
    is_global: Mapped[bool] = mapped_column(Boolean, default=False)
    accent_color: Mapped[str | None] = mapped_column(String(9))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    bins: Mapped[list[Bin]] = relationship(back_populates="network")

    __table_args__ = (Index("ix_networks_name", "name"),)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Network {self.code}>"


class Source(Base):
    """Internal provenance record. Never surfaced in a lookup result."""

    __tablename__ = "sources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    reference: Mapped[str | None] = mapped_column(String(512))
    licence: Mapped[str | None] = mapped_column(String(128))
    trust_score: Mapped[float] = mapped_column(Float, default=0.5)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    __table_args__ = (CheckConstraint("trust_score >= 0 AND trust_score <= 1", name="ck_sources_trust"),)


# ---------------------------------------------------------------------------
# Institutions
# ---------------------------------------------------------------------------


class Institution(Base):
    __tablename__ = "institutions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    uid: Mapped[str] = mapped_column(String(40), nullable=False, unique=True)
    display_name: Mapped[str] = mapped_column(String(256), nullable=False)
    legal_name: Mapped[str | None] = mapped_column(String(256))
    normalized_name: Mapped[str] = mapped_column(String(256), nullable=False)
    normalized_legal_name: Mapped[str | None] = mapped_column(String(256))
    short_name: Mapped[str | None] = mapped_column(String(64))
    institution_type: Mapped[str] = mapped_column(String(32), default=InstitutionType.BANK.value)
    parent_id: Mapped[int | None] = mapped_column(
        ForeignKey("institutions.id", ondelete="SET NULL")
    )
    country_id: Mapped[int | None] = mapped_column(ForeignKey("countries.id", ondelete="SET NULL"))
    website: Mapped[str | None] = mapped_column(String(512))
    swift_bic: Mapped[str | None] = mapped_column(String(11))
    status: Mapped[str] = mapped_column(String(16), default=RecordStatus.ACTIVE.value)
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    first_seen: Mapped[datetime | None] = mapped_column(DateTime, default=utcnow)
    last_updated: Mapped[datetime | None] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    parent: Mapped[Institution | None] = relationship(
        remote_side="Institution.id", back_populates="subsidiaries"
    )
    subsidiaries: Mapped[list[Institution]] = relationship(back_populates="parent")
    country: Mapped[Country | None] = relationship(back_populates="institutions")
    aliases: Mapped[list[InstitutionAlias]] = relationship(
        back_populates="institution", cascade="all, delete-orphan"
    )
    addresses: Mapped[list[Address]] = relationship(
        back_populates="institution", cascade="all, delete-orphan"
    )
    bin_links: Mapped[list[BinInstitution]] = relationship(
        back_populates="institution", cascade="all, delete-orphan"
    )
    outgoing_relationships: Mapped[list[InstitutionRelationship]] = relationship(
        back_populates="institution",
        foreign_keys="InstitutionRelationship.institution_id",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        Index("ix_institutions_normalized_name", "normalized_name"),
        Index("ix_institutions_normalized_legal_name", "normalized_legal_name"),
        Index("ix_institutions_display_name", "display_name"),
        Index("ix_institutions_country", "country_id"),
        Index("ix_institutions_parent", "parent_id"),
        Index("ix_institutions_country_name", "country_id", "normalized_name"),
        CheckConstraint("confidence >= 0 AND confidence <= 1", name="ck_institutions_confidence"),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Institution {self.id} {self.display_name!r}>"


class InstitutionAlias(Base):
    __tablename__ = "institution_aliases"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    institution_id: Mapped[int] = mapped_column(
        ForeignKey("institutions.id", ondelete="CASCADE"), nullable=False
    )
    alias: Mapped[str] = mapped_column(String(256), nullable=False)
    normalized_alias: Mapped[str] = mapped_column(String(256), nullable=False)
    alias_type: Mapped[str] = mapped_column(String(32), default=AliasType.VARIANT.value)
    language: Mapped[str | None] = mapped_column(String(8))
    confidence: Mapped[float] = mapped_column(Float, default=0.8)
    source_id: Mapped[int | None] = mapped_column(ForeignKey("sources.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    institution: Mapped[Institution] = relationship(back_populates="aliases")

    __table_args__ = (
        UniqueConstraint("institution_id", "normalized_alias", name="uq_alias_per_institution"),
        Index("ix_alias_normalized", "normalized_alias"),
        Index("ix_alias_institution", "institution_id"),
    )


class Address(Base):
    __tablename__ = "addresses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    institution_id: Mapped[int | None] = mapped_column(
        ForeignKey("institutions.id", ondelete="CASCADE")
    )
    line1: Mapped[str | None] = mapped_column(String(256))
    line2: Mapped[str | None] = mapped_column(String(256))
    city: Mapped[str | None] = mapped_column(String(128))
    normalized_city: Mapped[str | None] = mapped_column(String(128))
    region: Mapped[str | None] = mapped_column(String(128))
    region_code: Mapped[str | None] = mapped_column(String(8))
    postal_code: Mapped[str | None] = mapped_column(String(24))
    normalized_postal_code: Mapped[str | None] = mapped_column(String(24))
    country_id: Mapped[int | None] = mapped_column(ForeignKey("countries.id", ondelete="SET NULL"))
    latitude: Mapped[float | None] = mapped_column(Float)
    longitude: Mapped[float | None] = mapped_column(Float)
    address_type: Mapped[str] = mapped_column(String(32), default="headquarters")
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False)
    fingerprint: Mapped[str] = mapped_column(String(40), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, default=0.8)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    institution: Mapped[Institution | None] = relationship(back_populates="addresses")
    country: Mapped[Country | None] = relationship()

    __table_args__ = (
        UniqueConstraint("institution_id", "fingerprint", name="uq_address_fingerprint"),
        Index("ix_addresses_city", "normalized_city"),
        Index("ix_addresses_region", "region_code"),
        Index("ix_addresses_postal", "normalized_postal_code"),
        Index("ix_addresses_country", "country_id"),
        Index("ix_addresses_institution", "institution_id"),
    )


# ---------------------------------------------------------------------------
# BINs
# ---------------------------------------------------------------------------


class Bin(Base):
    __tablename__ = "bins"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    bin: Mapped[str] = mapped_column(String(11), nullable=False, unique=True)
    iin: Mapped[str | None] = mapped_column(String(11))
    iin_length: Mapped[int | None] = mapped_column(Integer)
    #: The prefix exactly as assigned, and how long that assignment is. A
    #: six-digit root and an eight-digit assignment beneath it are separate
    #: allocations; ``prefix_length`` is what keeps them apart.
    prefix: Mapped[str] = mapped_column(
        String(11), nullable=False, default="", server_default=""
    )
    prefix_length: Mapped[int] = mapped_column(
        Integer, nullable=False, default=6, server_default="6"
    )
    prefix_type: Mapped[str] = mapped_column(
        String(16), default=PrefixType.ROOT.value, server_default=PrefixType.ROOT.value
    )
    #: Numeric form padded to 8 digits, so range containment is a plain integer
    #: comparison that SQLite can serve straight from an index.
    bin_int: Mapped[int] = mapped_column(Integer, nullable=False)
    #: The inclusive numeric span this prefix covers at the padding width, so
    #: "is this eight-digit query inside that six-digit root" is one comparison.
    span_low: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    span_high: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    prefix6: Mapped[str] = mapped_column(String(6), nullable=False)
    prefix8: Mapped[str | None] = mapped_column(String(8))
    network_id: Mapped[int | None] = mapped_column(ForeignKey("networks.id", ondelete="SET NULL"))
    brand: Mapped[str | None] = mapped_column(String(64))
    card_type: Mapped[str] = mapped_column(String(24), default=CardType.UNKNOWN.value)
    funding_type: Mapped[str] = mapped_column(String(24), default=FundingType.UNKNOWN.value)
    is_prepaid: Mapped[bool | None] = mapped_column(Boolean)
    is_commercial: Mapped[bool | None] = mapped_column(Boolean)
    country_id: Mapped[int | None] = mapped_column(ForeignKey("countries.id", ondelete="SET NULL"))
    currency_code: Mapped[str | None] = mapped_column(String(3))
    status: Mapped[str] = mapped_column(String(16), default=RecordStatus.ACTIVE.value)
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    first_seen: Mapped[datetime | None] = mapped_column(DateTime, default=utcnow)
    last_updated: Mapped[datetime | None] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    network: Mapped[Network | None] = relationship(back_populates="bins")
    country: Mapped[Country | None] = relationship(back_populates="bins")
    institution_links: Mapped[list[BinInstitution]] = relationship(
        back_populates="bin_record", cascade="all, delete-orphan"
    )
    claims: Mapped[list[BinClaim]] = relationship(
        back_populates="bin_record", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("ix_bins_bin", "bin"),
        Index("ix_bins_bin_int", "bin_int"),
        Index("ix_bins_prefix6", "prefix6"),
        Index("ix_bins_prefix8", "prefix8"),
        Index("ix_bins_prefix_length", "prefix", "prefix_length"),
        Index("ix_bins_length_int", "prefix_length", "bin_int"),
        Index("ix_bins_span", "span_low", "span_high"),
        Index("ix_bins_type_length", "prefix_type", "prefix_length"),
        Index("ix_bins_iin", "iin"),
        Index("ix_bins_network", "network_id"),
        Index("ix_bins_country", "country_id"),
        Index("ix_bins_card_type", "card_type"),
        Index("ix_bins_funding_type", "funding_type"),
        Index("ix_bins_country_network", "country_id", "network_id"),
        CheckConstraint("length(bin) >= 4", name="ck_bins_min_length"),
        CheckConstraint("confidence >= 0 AND confidence <= 1", name="ck_bins_confidence"),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Bin {self.bin}>"


class BinRange(Base):
    """A contiguous issuer range, used when a scheme allocates blocks."""

    __tablename__ = "bin_ranges"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    range_low: Mapped[str] = mapped_column(String(11), nullable=False)
    range_high: Mapped[str] = mapped_column(String(11), nullable=False)
    range_low_int: Mapped[int] = mapped_column(Integer, nullable=False)
    range_high_int: Mapped[int] = mapped_column(Integer, nullable=False)
    width: Mapped[int] = mapped_column(Integer, nullable=False, default=8)
    #: An account range is the most specific authoritative allocation there is;
    #: a root block is the least. Specificity ranking reads this.
    range_type: Mapped[str] = mapped_column(
        String(24),
        default=RangeType.ISSUER_RANGE.value,
        server_default=RangeType.ISSUER_RANGE.value,
    )
    #: The span, precomputed, so "narrowest containing range" is an ORDER BY
    #: over a stored column rather than an expression SQLite cannot index.
    span: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    effective_from: Mapped[datetime | None] = mapped_column(DateTime)
    effective_to: Mapped[datetime | None] = mapped_column(DateTime)
    is_current: Mapped[bool] = mapped_column(Boolean, default=True, server_default="1")
    institution_id: Mapped[int | None] = mapped_column(
        ForeignKey("institutions.id", ondelete="SET NULL")
    )
    network_id: Mapped[int | None] = mapped_column(ForeignKey("networks.id", ondelete="SET NULL"))
    country_id: Mapped[int | None] = mapped_column(ForeignKey("countries.id", ondelete="SET NULL"))
    brand: Mapped[str | None] = mapped_column(String(64))
    card_type: Mapped[str] = mapped_column(String(24), default=CardType.UNKNOWN.value)
    funding_type: Mapped[str] = mapped_column(String(24), default=FundingType.UNKNOWN.value)
    is_prepaid: Mapped[bool | None] = mapped_column(Boolean)
    is_commercial: Mapped[bool | None] = mapped_column(Boolean)
    currency_code: Mapped[str | None] = mapped_column(String(3))
    status: Mapped[str] = mapped_column(String(16), default=RecordStatus.ACTIVE.value)
    confidence: Mapped[float] = mapped_column(Float, default=0.8)
    first_seen: Mapped[datetime | None] = mapped_column(DateTime, default=utcnow)
    last_updated: Mapped[datetime | None] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    institution: Mapped[Institution | None] = relationship()
    network: Mapped[Network | None] = relationship()
    country: Mapped[Country | None] = relationship()

    __table_args__ = (
        UniqueConstraint("range_low", "range_high", "institution_id", name="uq_bin_range"),
        Index("ix_bin_ranges_low", "range_low_int"),
        Index("ix_bin_ranges_high", "range_high_int"),
        Index("ix_bin_ranges_span", "range_low_int", "range_high_int"),
        Index("ix_bin_ranges_institution", "institution_id"),
        Index("ix_bin_ranges_current", "is_current", "range_low_int", "range_high_int"),
        Index("ix_bin_ranges_type_span", "range_type", "span"),
        CheckConstraint("range_low_int <= range_high_int", name="ck_bin_range_order"),
    )


class BinInstitution(Base):
    """Many-to-many link: a BIN may legitimately name several institutions."""

    __tablename__ = "bin_institutions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    bin_id: Mapped[int] = mapped_column(
        ForeignKey("bins.id", ondelete="CASCADE"), nullable=False
    )
    institution_id: Mapped[int] = mapped_column(
        ForeignKey("institutions.id", ondelete="CASCADE"), nullable=False
    )
    relationship_type: Mapped[str] = mapped_column(
        String(32), default=RelationshipType.ISSUER.value
    )
    is_primary: Mapped[bool] = mapped_column(Boolean, default=True)
    #: A relationship is a fact with a lifetime. An issuer that transferred a
    #: portfolio in 2024 was still the issuer before then, and a lookup that
    #: forgets that cannot answer a question about a card issued in 2022.
    status: Mapped[str] = mapped_column(
        String(16),
        default=RecordStatus.ACTIVE.value,
        server_default=RecordStatus.ACTIVE.value,
    )
    effective_from: Mapped[datetime | None] = mapped_column(DateTime)
    effective_to: Mapped[datetime | None] = mapped_column(DateTime)
    is_current: Mapped[bool] = mapped_column(Boolean, default=True, server_default="1")
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    #: The evidence band behind ``confidence`` — see app.normalizers.confidence.
    confidence_level: Mapped[str | None] = mapped_column(String(16))
    #: Why, in short human-readable clauses. Never source names.
    confidence_reasons: Mapped[str | None] = mapped_column(Text)
    first_seen: Mapped[datetime | None] = mapped_column(DateTime, default=utcnow)
    last_updated: Mapped[datetime | None] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    bin_record: Mapped[Bin] = relationship(back_populates="institution_links")
    institution: Mapped[Institution] = relationship(back_populates="bin_links")

    __table_args__ = (
        UniqueConstraint(
            "bin_id", "institution_id", "relationship_type", name="uq_bin_institution_role"
        ),
        Index("ix_bin_institutions_bin", "bin_id"),
        Index("ix_bin_institutions_institution", "institution_id"),
        Index("ix_bin_institutions_primary", "institution_id", "is_primary"),
        Index("ix_bin_institutions_current", "bin_id", "is_current", "relationship_type"),
        Index("ix_bin_institutions_history", "institution_id", "is_current"),
    )


class BinClaim(Base):
    """A single source's raw assertion about a BIN. Internal use only."""

    __tablename__ = "bin_claims"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    bin_id: Mapped[int] = mapped_column(ForeignKey("bins.id", ondelete="CASCADE"), nullable=False)
    source_id: Mapped[int | None] = mapped_column(ForeignKey("sources.id", ondelete="SET NULL"))
    field: Mapped[str] = mapped_column(String(64), nullable=False)
    value: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[float] = mapped_column(Float, default=0.5)
    observed_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    bin_record: Mapped[Bin] = relationship(back_populates="claims")

    __table_args__ = (
        Index("ix_bin_claims_bin_field", "bin_id", "field"),
    )


# ---------------------------------------------------------------------------
# Operational tables
# ---------------------------------------------------------------------------


class DatabaseMetadata(Base):
    """Key/value description of the installed database package."""

    __tablename__ = "database_metadata"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    # Well-known keys.
    VERSION = "version"
    SCHEMA_VERSION = "schema_version"
    RELEASE_DATE = "release_date"
    RECORD_COUNT = "record_count"
    BUILD_ID = "build_id"
    PUBLISHER = "publisher"
    CHECKSUM = "checksum"
    INSTALLED_AT = "installed_at"
    LAST_VERIFIED = "last_verified"
    NOTES = "notes"


class UpdateHistory(Base):
    __tablename__ = "update_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    from_version: Mapped[str | None] = mapped_column(String(32))
    to_version: Mapped[str | None] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(24), default=UpdateStatus.STARTED.value)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    bytes_downloaded: Mapped[int] = mapped_column(Integer, default=0)
    backup_path: Mapped[str | None] = mapped_column(String(1024))
    message: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (Index("ix_update_history_started", "started_at"),)


class Conflict(Base):
    """Two defensible claims about the same field. Never silently overwritten."""

    __tablename__ = "conflicts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    entity_type: Mapped[str] = mapped_column(String(32), nullable=False)
    entity_key: Mapped[str] = mapped_column(String(128), nullable=False)
    field: Mapped[str] = mapped_column(String(64), nullable=False)
    value_a: Mapped[str | None] = mapped_column(Text)
    value_b: Mapped[str | None] = mapped_column(Text)
    source_a_id: Mapped[int | None] = mapped_column(ForeignKey("sources.id", ondelete="SET NULL"))
    source_b_id: Mapped[int | None] = mapped_column(ForeignKey("sources.id", ondelete="SET NULL"))
    confidence_a: Mapped[float] = mapped_column(Float, default=0.5)
    confidence_b: Mapped[float] = mapped_column(Float, default=0.5)
    status: Mapped[str] = mapped_column(String(16), default=ConflictStatus.OPEN.value)
    resolution: Mapped[str | None] = mapped_column(String(32))
    resolved_value: Mapped[str | None] = mapped_column(Text)
    detected_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime)

    __table_args__ = (
        UniqueConstraint(
            "entity_type", "entity_key", "field", "value_a", "value_b", name="uq_conflict_claim"
        ),
        Index("ix_conflicts_entity", "entity_type", "entity_key"),
        Index("ix_conflicts_status", "status"),
    )


class NormalizationEvent(Base):
    """Audit trail of every value a normalizer rewrote, with its confidence."""

    __tablename__ = "normalization_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    entity_type: Mapped[str] = mapped_column(String(32), nullable=False)
    entity_key: Mapped[str | None] = mapped_column(String(128))
    field: Mapped[str] = mapped_column(String(64), nullable=False)
    raw_value: Mapped[str | None] = mapped_column(Text)
    normalized_value: Mapped[str | None] = mapped_column(Text)
    rule: Mapped[str | None] = mapped_column(String(64))
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    __table_args__ = (
        Index("ix_normalization_entity", "entity_type", "entity_key"),
        Index("ix_normalization_rule", "rule"),
    )

# ---------------------------------------------------------------------------
# Change history
# ---------------------------------------------------------------------------


class ChangeAction(StrEnum):
    """What happened to a record between two database releases."""

    CREATED = "created"
    UPDATED = "updated"
    RETIRED = "retired"
    REASSIGNED = "reassigned"
    MERGED = "merged"

    @property
    def label(self) -> str:
        return self.value.capitalize()


class BinHistory(Base):
    """A superseded state of a BIN record.

    Database releases append here rather than overwriting, so "BIN History"
    can show how an assignment changed over time. The current state always
    lives in :class:`Bin`; this table holds what came before it.
    """

    __tablename__ = "bin_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    bin: Mapped[str] = mapped_column(String(11), nullable=False)
    bin_id: Mapped[int | None] = mapped_column(ForeignKey("bins.id", ondelete="SET NULL"))
    action: Mapped[str] = mapped_column(String(24), default=ChangeAction.UPDATED.value)
    field: Mapped[str | None] = mapped_column(String(64))
    previous_value: Mapped[str | None] = mapped_column(Text)
    current_value: Mapped[str | None] = mapped_column(Text)
    institution_uid: Mapped[str | None] = mapped_column(String(40))
    database_version: Mapped[str | None] = mapped_column(String(32))
    changed_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    __table_args__ = (
        Index("ix_bin_history_bin", "bin", "changed_at"),
        Index("ix_bin_history_version", "database_version"),
    )


class InstitutionHistory(Base):
    """A superseded state of an institution record."""

    __tablename__ = "institution_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    institution_uid: Mapped[str] = mapped_column(String(40), nullable=False)
    institution_id: Mapped[int | None] = mapped_column(
        ForeignKey("institutions.id", ondelete="SET NULL")
    )
    action: Mapped[str] = mapped_column(String(24), default=ChangeAction.UPDATED.value)
    field: Mapped[str | None] = mapped_column(String(64))
    previous_value: Mapped[str | None] = mapped_column(Text)
    current_value: Mapped[str | None] = mapped_column(Text)
    database_version: Mapped[str | None] = mapped_column(String(32))
    changed_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    __table_args__ = (
        Index("ix_institution_history_uid", "institution_uid", "changed_at"),
        Index("ix_institution_history_version", "database_version"),
    )


class InstitutionRelationship(Base):
    """How two institutions relate to each other.

    ``institutions.parent_id`` models one parent, which is not enough: a bank
    can be the successor of one entity, a brand of another and a subsidiary of
    a third. Those are different facts with different lifetimes, so they get
    rows rather than columns.

    Following these is what makes an institution lookup complete — searching a
    parent group should reach the BINs its subsidiaries issue.
    """

    __tablename__ = "institution_relationships"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    institution_id: Mapped[int] = mapped_column(
        ForeignKey("institutions.id", ondelete="CASCADE"), nullable=False
    )
    related_institution_id: Mapped[int] = mapped_column(
        ForeignKey("institutions.id", ondelete="CASCADE"), nullable=False
    )
    relationship_type: Mapped[str] = mapped_column(
        String(32), default=InstitutionLinkType.SUBSIDIARY.value
    )
    effective_from: Mapped[datetime | None] = mapped_column(DateTime)
    effective_to: Mapped[datetime | None] = mapped_column(DateTime)
    is_current: Mapped[bool] = mapped_column(Boolean, default=True)
    confidence: Mapped[float] = mapped_column(Float, default=0.8)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    institution: Mapped[Institution] = relationship(
        foreign_keys=[institution_id], back_populates="outgoing_relationships"
    )
    related: Mapped[Institution] = relationship(foreign_keys=[related_institution_id])

    __table_args__ = (
        UniqueConstraint(
            "institution_id",
            "related_institution_id",
            "relationship_type",
            name="uq_institution_relationship",
        ),
        CheckConstraint(
            "institution_id != related_institution_id", name="ck_institution_not_self"
        ),
        Index("ix_institution_rel_from", "institution_id", "is_current"),
        Index("ix_institution_rel_to", "related_institution_id", "is_current"),
    )


class StagingRecord(Base):
    """One imported record, held before it is allowed near production tables.

    Raw data is never written straight into ``bins`` and ``institutions``. It
    lands here, is normalized, validated, resolved and conflict-checked, and is
    promoted only once it passes. A bad feed then spoils the staging table
    instead of the database people are looking things up in.
    """

    __tablename__ = "staging_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    batch_id: Mapped[str] = mapped_column(String(64), nullable=False)
    source_id: Mapped[int | None] = mapped_column(ForeignKey("sources.id", ondelete="SET NULL"))
    #: The record exactly as it arrived, as JSON. Issuer metadata only.
    raw_payload: Mapped[str | None] = mapped_column(Text)
    #: The record after normalization, as JSON.
    normalized_payload: Mapped[str | None] = mapped_column(Text)
    prefix: Mapped[str | None] = mapped_column(String(11))
    prefix_length: Mapped[int | None] = mapped_column(Integer)
    prefix_type: Mapped[str | None] = mapped_column(String(16))
    range_low: Mapped[str | None] = mapped_column(String(11))
    range_high: Mapped[str | None] = mapped_column(String(11))
    institution_name: Mapped[str | None] = mapped_column(String(256))
    resolved_institution_id: Mapped[int | None] = mapped_column(
        ForeignKey("institutions.id", ondelete="SET NULL")
    )
    resolution_match_type: Mapped[str | None] = mapped_column(String(24))
    status: Mapped[str] = mapped_column(String(16), default=StagingStatus.RECEIVED.value)
    confidence: Mapped[float | None] = mapped_column(Float)
    confidence_level: Mapped[str | None] = mapped_column(String(16))
    #: Why a record was rejected, or what could not be resolved.
    issues: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    promoted_at: Mapped[datetime | None] = mapped_column(DateTime)

    __table_args__ = (
        Index("ix_staging_batch", "batch_id", "status"),
        Index("ix_staging_prefix", "prefix", "prefix_length"),
        Index("ix_staging_status", "status"),
    )


class DataQualityMetric(Base):
    """A measured quality figure for one database release.

    Every value here is counted from the database it describes. Nothing in this
    table is estimated, and nothing is written unless it was computed.
    """

    __tablename__ = "data_quality_metrics"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(64), nullable=False)
    label: Mapped[str] = mapped_column(String(160), nullable=False)
    #: The counted numerator and denominator, kept so a ratio can be explained.
    numerator: Mapped[int] = mapped_column(Integer, default=0)
    denominator: Mapped[int] = mapped_column(Integer, default=0)
    ratio: Mapped[float | None] = mapped_column(Float)
    database_version: Mapped[str | None] = mapped_column(String(32))
    computed_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    __table_args__ = (
        UniqueConstraint("key", "database_version", name="uq_quality_metric"),
        Index("ix_quality_metrics_key", "key"),
    )


class DatabaseVersion(Base):
    """One published release of the intelligence database.

    The build pipeline appends a row per release, so a package carries its own
    lineage and the application can show what changed between two versions.
    """

    __tablename__ = "database_versions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    version: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    edition: Mapped[str] = mapped_column(String(24), default="community")
    release_date: Mapped[datetime | None] = mapped_column(DateTime)
    record_count: Mapped[int] = mapped_column(Integer, default=0)
    institution_count: Mapped[int] = mapped_column(Integer, default=0)
    checksum: Mapped[str | None] = mapped_column(String(160))
    bins_added: Mapped[int] = mapped_column(Integer, default=0)
    bins_removed: Mapped[int] = mapped_column(Integer, default=0)
    bins_changed: Mapped[int] = mapped_column(Integer, default=0)
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    __table_args__ = (Index("ix_database_versions_release", "release_date"),)


class DatabaseStatistic(Base):
    """A precomputed aggregate, so analytics does not rescan on every visit.

    The build pipeline fills this in; the application recomputes any entry it
    finds missing so an older package still produces correct analytics.
    """

    __tablename__ = "database_statistics"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    scope: Mapped[str] = mapped_column(String(32), nullable=False)
    key: Mapped[str] = mapped_column(String(128), nullable=False)
    label: Mapped[str | None] = mapped_column(String(160))
    value: Mapped[int] = mapped_column(Integer, default=0)
    ratio: Mapped[float | None] = mapped_column(Float)
    database_version: Mapped[str | None] = mapped_column(String(32))
    computed_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    __table_args__ = (
        UniqueConstraint("scope", "key", name="uq_statistic_scope_key"),
        Index("ix_database_statistics_scope", "scope", "value"),
    )
