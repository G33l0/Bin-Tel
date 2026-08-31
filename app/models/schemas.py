"""Pydantic data-transfer objects.

The UI layer only ever sees these. ORM instances (and the sessions that own
them) never leave the repository/service layer, which keeps database logic out
of the widgets and makes every result object safe to hand to a worker thread.

Deliberately absent from every user-facing DTO: data sources, source URLs,
source names, provenance and internal notes. That information stays in the
database for data-quality work and is never rendered in a lookup result.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from enum import StrEnum
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field, computed_field

from app.core.constants import UNKNOWN_DISPLAY
from app.utils.formatting import display, display_optional_bool, format_location

T = TypeVar("T")


class _DTO(BaseModel):
    model_config = ConfigDict(from_attributes=True, frozen=True)


# ---------------------------------------------------------------------------
# Reference DTOs
# ---------------------------------------------------------------------------


class CountryDTO(_DTO):
    id: int | None = None
    iso2: str | None = None
    iso3: str | None = None
    name: str | None = None
    currency_code: str | None = None
    flag_emoji: str | None = None

    @property
    def display_name(self) -> str:
        return display(self.name)

    @property
    def label(self) -> str:
        """``United States (US)`` — or ``Unknown``."""
        if not self.name:
            return UNKNOWN_DISPLAY
        return f"{self.name} ({self.iso2})" if self.iso2 else self.name


class NetworkDTO(_DTO):
    id: int | None = None
    code: str | None = None
    name: str | None = None
    display_name: str | None = None
    accent_color: str | None = None

    @property
    def label(self) -> str:
        return display(self.display_name or self.name)


class AddressDTO(_DTO):
    line1: str | None = None
    line2: str | None = None
    city: str | None = None
    region: str | None = None
    region_code: str | None = None
    postal_code: str | None = None
    country: CountryDTO | None = None
    is_primary: bool = False

    @property
    def is_empty(self) -> bool:
        return not any((self.line1, self.line2, self.city, self.region, self.postal_code))

    @property
    def one_line(self) -> str:
        # Some markets name the state after its largest city; printing both
        # ("New York, New York") reads as a data error, so the repeat is dropped.
        region = None if (self.region or "").strip().lower() == (self.city or "").strip().lower() else self.region
        parts = [
            part
            for part in (self.line1, self.line2, self.city, region, self.postal_code)
            if part
        ]
        if self.country and self.country.name:
            parts.append(self.country.name)
        return ", ".join(parts) if parts else UNKNOWN_DISPLAY

    @property
    def block(self) -> str:
        """Multi-line postal block for the result card."""
        lines = [line for line in (self.line1, self.line2) if line]
        region = self.region or self.region_code
        if (region or "").strip().lower() == (self.city or "").strip().lower():
            region = None
        locality = format_location(
            self.city,
            region,
            self.postal_code,
            self.country.name if self.country else None,
        )
        if locality != UNKNOWN_DISPLAY:
            lines.extend(locality.splitlines())
        return "\n".join(lines) if lines else UNKNOWN_DISPLAY


# ---------------------------------------------------------------------------
# Institutions
# ---------------------------------------------------------------------------


class InstitutionSummary(_DTO):
    """One institution's relationship to a BIN, with its lifetime and standing.

    A BIN result carries *every* relationship the data supports, not a single
    winner. Each one says what kind of relationship it is, whether it is
    current, when it applied, and how well it is evidenced — so a caller can
    tell a present-day issuer from a predecessor at a glance.
    """

    id: int
    display_name: str
    legal_name: str | None = None
    country: CountryDTO | None = None
    relationship_type: str = "issuer"
    is_primary: bool = True
    website: str | None = None
    uid: str | None = None
    status: str | None = None
    is_current: bool = True
    effective_from: datetime | None = None
    effective_to: datetime | None = None
    confidence: float | None = None
    confidence_level: str | None = None

    @property
    def relationship_label(self) -> str:
        from app.models.entities import RelationshipType

        try:
            return RelationshipType(self.relationship_type).label
        except ValueError:
            return self.relationship_type.replace("_", " ").title()

    @property
    def is_issuing(self) -> bool:
        """Whether this relationship asserts the institution issues the card."""
        from app.models.entities import RelationshipType

        try:
            return RelationshipType(self.relationship_type).is_issuing
        except ValueError:
            return False

    @property
    def effective_period(self) -> str:
        """``2020–2024``, ``Since 2024`` or ``—`` when no dates are recorded."""
        start = self.effective_from.year if self.effective_from else None
        end = self.effective_to.year if self.effective_to else None
        if start and end:
            return f"{start}–{end}"
        if start:
            return f"Since {start}"
        if end:
            return f"Until {end}"
        return UNKNOWN_DISPLAY

    @property
    def standing_label(self) -> str:
        return "Current" if self.is_current else "Historical"


class InstitutionDetail(_DTO):
    """The header of a bank-lookup result."""

    id: int
    display_name: str
    legal_name: str | None = None
    short_name: str | None = None
    institution_type: str | None = None
    status: str | None = None
    website: str | None = None
    country: CountryDTO | None = None
    parent_name: str | None = None
    aliases: tuple[str, ...] = ()
    address: AddressDTO | None = None
    bin_count: int = 0

    @property
    def has_address(self) -> bool:
        return self.address is not None and not self.address.is_empty


# ---------------------------------------------------------------------------
# BINs
# ---------------------------------------------------------------------------


class BinRecord(_DTO):
    """A complete BIN/IIN record as presented to the user."""

    id: int
    bin: str
    iin: str | None = None
    iin_length: int | None = None
    #: The prefix as assigned, and how long that assignment is. A six-digit
    #: root and an eight-digit assignment under it are different allocations,
    #: and this is what keeps them apart in a result.
    prefix_length: int | None = None
    prefix_type: str | None = None
    bin_range: str | None = None
    network: NetworkDTO | None = None
    brand: str | None = None
    card_type: str | None = None
    funding_type: str | None = None
    is_prepaid: bool | None = None
    is_commercial: bool | None = None
    country: CountryDTO | None = None
    currency_code: str | None = None
    status: str | None = None
    first_seen: datetime | None = None
    last_updated: datetime | None = None
    institutions: tuple[InstitutionSummary, ...] = ()
    address: AddressDTO | None = None

    # -- derived, presentation-only ---------------------------------------
    @computed_field  # type: ignore[prop-decorator]
    @property
    def issuer_name(self) -> str:
        primary = self.primary_institution
        return primary.display_name if primary else UNKNOWN_DISPLAY

    @property
    def primary_institution(self) -> InstitutionSummary | None:
        """The best-supported *current issuing* relationship, if there is one.

        A former issuer is never promoted over a current one, and an
        associative relationship (a parent, a processor) is never promoted over
        an issuing one — naming either as "the issuer" would be wrong.
        """
        current_issuers = [
            item for item in self.institutions if item.is_issuing and item.is_current
        ]
        for institution in current_issuers:
            if institution.is_primary:
                return institution
        if current_issuers:
            return current_issuers[0]
        issuers = [item for item in self.institutions if item.is_issuing]
        if issuers:
            return issuers[0]
        return self.institutions[0] if self.institutions else None

    @property
    def has_multiple_institutions(self) -> bool:
        return len(self.institutions) > 1

    @property
    def current_institutions(self) -> tuple[InstitutionSummary, ...]:
        return tuple(item for item in self.institutions if item.is_current)

    @property
    def historical_institutions(self) -> tuple[InstitutionSummary, ...]:
        return tuple(item for item in self.institutions if not item.is_current)

    @property
    def issuing_institutions(self) -> tuple[InstitutionSummary, ...]:
        """Relationships that actually assert issuance, current ones first."""
        return tuple(
            item for item in self.institutions if item.is_issuing and item.is_current
        )

    @property
    def bin_length_label(self) -> str:
        """``8 digits`` — the assigned length, not the length of the query."""
        length = self.prefix_length or len(self.bin)
        return f"{length} digits"

    @property
    def issuer_legal_name(self) -> str:
        primary = self.primary_institution
        return display(primary.legal_name) if primary else UNKNOWN_DISPLAY

    @property
    def parent_name(self) -> str:
        for institution in self.institutions:
            if institution.relationship_type == "parent":
                return institution.display_name
        return UNKNOWN_DISPLAY

    def to_field_pairs(self) -> list[tuple[str, str]]:
        """Ordered ``(label, value)`` pairs for the result card and exports.

        Contains BIN/IIN and issuer metadata only.
        """
        address = self.address
        pairs: list[tuple[str, str]] = [
            ("BIN", display(self.bin)),
            ("IIN", display(self.iin or self.bin)),
            # The assigned length, which is what distinguishes a six-digit root
            # from an eight-digit assignment beneath it.
            ("BIN Length", self.bin_length_label),
            ("IIN Length", display(self.iin_length)),
            ("BIN Range", display(self.bin_range)),
            ("Network", self.network.label if self.network else UNKNOWN_DISPLAY),
            ("Card Brand", display(self.brand)),
            ("Card Type", display(self.card_type)),
            ("Funding Type", display(self.funding_type)),
            ("Prepaid", display_optional_bool(self.is_prepaid)),
            ("Commercial", display_optional_bool(self.is_commercial)),
            ("Issuer", self.issuer_name),
            ("Issuer Legal Name", self.issuer_legal_name),
            ("Parent Institution", self.parent_name),
            ("Country", self.country.display_name if self.country else UNKNOWN_DISPLAY),
            ("ISO Country Code", display(self.country.iso2 if self.country else None)),
            ("Currency", display(self.currency_code)),
            ("State / Province", display(address.region if address else None)),
            ("City", display(address.city if address else None)),
            ("Postal / ZIP Code", display(address.postal_code if address else None)),
            ("Address", address.one_line if address else UNKNOWN_DISPLAY),
            ("Website", display(self.primary_institution.website if self.primary_institution else None)),
            ("Status", display(self.status)),
        ]
        return pairs

    def to_export_dict(self) -> dict[str, Any]:
        """Flat mapping used by the JSON/CSV/TXT exporters."""
        return {label: value for label, value in self.to_field_pairs()}


class BinRow(_DTO):
    """One row of a paginated BIN table (bank results, filtered browsing)."""

    id: int
    bin: str
    network: str | None = None
    brand: str | None = None
    card_type: str | None = None
    funding_type: str | None = None
    country: str | None = None
    country_code: str | None = None
    region: str | None = None
    city: str | None = None
    postal_code: str | None = None
    institution: str | None = None
    status: str | None = None
    #: The assigned length, so a table can distinguish a six-digit root from
    #: an eight-digit assignment rather than showing them as the same kind of
    #: thing.
    prefix_length: int | None = None
    prefix_type: str | None = None
    #: Whether the relationship that puts this BIN in this table is current.
    is_current: bool = True
    relationship_type: str | None = None
    #: Set when the row came from a range rather than a discrete assignment.
    bin_range: str | None = None

    @property
    def length_label(self) -> str:
        length = self.prefix_length or len(self.bin)
        return f"{length}-digit"

    @property
    def standing(self) -> str:
        return "Current" if self.is_current else "Historical"

    def cell(self, key: str) -> str:
        if key == "length":
            return self.length_label
        if key == "standing":
            return self.standing
        return display(getattr(self, key, None))


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------


class SortDirection(StrEnum):
    ASC = "asc"
    DESC = "desc"


class BinFilters(BaseModel):
    """Filters shared by the bank-result table and BIN browsing."""

    model_config = ConfigDict(frozen=True)

    country_code: str | None = None
    network_code: str | None = None
    card_type: str | None = None
    funding_type: str | None = None
    region: str | None = None
    text: str | None = None
    #: ``None`` shows current and historical together, which is the honest
    #: default for a portfolio view: a BIN an issuer used to hold is part of
    #: its history, and hiding it silently would misrepresent the record.
    is_current: bool | None = None
    #: Restrict to a prefix length — 6 for roots, 8 for extended assignments.
    prefix_length: int | None = None

    @property
    def is_active(self) -> bool:
        return any(
            value is not None and value != ""
            for value in (
                self.country_code,
                self.network_code,
                self.card_type,
                self.funding_type,
                self.region,
                self.text,
                self.is_current,
                self.prefix_length,
            )
        )

    def cleared(self) -> BinFilters:
        return BinFilters()


class PageRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=50, ge=1, le=1000)
    sort_by: str = "bin"
    direction: SortDirection = SortDirection.ASC

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size

    def at(self, page: int) -> PageRequest:
        return self.model_copy(update={"page": max(1, page)})


class Page(BaseModel, Generic[T]):
    """A slice of a result set — the UI never holds the whole table."""

    model_config = ConfigDict(frozen=True)

    items: Sequence[T]
    total: int
    page: int
    page_size: int

    @property
    def page_count(self) -> int:
        if self.page_size <= 0:
            return 1
        return max(1, -(-self.total // self.page_size))

    @property
    def has_previous(self) -> bool:
        return self.page > 1

    @property
    def has_next(self) -> bool:
        return self.page < self.page_count

    @property
    def first_index(self) -> int:
        return 0 if self.total == 0 else (self.page - 1) * self.page_size + 1

    @property
    def last_index(self) -> int:
        return min(self.total, self.page * self.page_size)

    @property
    def summary(self) -> str:
        if self.total == 0:
            return "No results"
        return f"{self.first_index:,}–{self.last_index:,} of {self.total:,}"

    @classmethod
    def empty(cls, request: PageRequest | None = None) -> Page[T]:
        request = request or PageRequest()
        return cls(items=[], total=0, page=request.page, page_size=request.page_size)


class BinLookupResult(BaseModel):
    """Outcome of a BIN/IIN search.

    ``records`` is ordered by how specific the matching allocation was, so
    ``best`` is the most specific one — never merely the first one found.
    """

    model_config = ConfigDict(frozen=True)

    query: str
    records: tuple[BinRecord, ...] = ()
    matched_by: str = "exact"
    elapsed_ms: float = 0.0
    #: How the winning allocation was found, and how narrow it is. Internal
    #: vocabulary — the interface shows the label, never a data-source name.
    strategy: str = "none"
    match_label: str = ""
    #: The overall confidence in the answer, and the reasons behind it.
    confidence_score: float = 0.0
    confidence_level: str = "unknown"
    confidence_reasons: tuple[str, ...] = ()
    #: Institutions named by an equally specific record that disagrees.
    conflicting_institutions: tuple[InstitutionSummary, ...] = ()
    #: More specific assignments recorded beneath the value searched for.
    more_specific_count: int = 0

    @property
    def found(self) -> bool:
        return bool(self.records)

    @property
    def best(self) -> BinRecord | None:
        return self.records[0] if self.records else None

    @property
    def resolved(self) -> bool:
        """Whether an institution was actually named.

        A prefix can be present in the database with no institution attached.
        That is a found record but an unresolved lookup, and saying so is the
        point — the alternative is inventing an issuer.
        """
        best = self.best
        return bool(best and best.institutions)

    @property
    def is_conflicted(self) -> bool:
        return self.confidence_level == "conflicted"

    @property
    def relationships(self) -> tuple[InstitutionSummary, ...]:
        best = self.best
        return best.institutions if best else ()

    @property
    def institution_count(self) -> int:
        return len({item.id for item in self.relationships})

    @property
    def confidence_percent(self) -> int:
        return int(round(max(0.0, min(1.0, self.confidence_score)) * 100))


class InstitutionStats(BaseModel):
    """Summary counters shown above a bank result table."""

    model_config = ConfigDict(frozen=True)

    total_bins: int = 0
    by_network: dict[str, int] = Field(default_factory=dict)
    by_card_type: dict[str, int] = Field(default_factory=dict)
    by_funding_type: dict[str, int] = Field(default_factory=dict)
    prepaid: int = 0
    commercial: int = 0
    countries: int = 0

    def network_count(self, code: str) -> int:
        return self.by_network.get(code, 0)

    def card_type_count(self, code: str) -> int:
        return self.by_card_type.get(code, 0)


class BankLookupResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    query: str
    matches: tuple[InstitutionDetail, ...] = ()
    elapsed_ms: float = 0.0

    @property
    def found(self) -> bool:
        return bool(self.matches)

    @property
    def best(self) -> InstitutionDetail | None:
        return self.matches[0] if self.matches else None


# ---------------------------------------------------------------------------
# Database status
# ---------------------------------------------------------------------------


class DatabaseStats(BaseModel):
    model_config = ConfigDict(frozen=True)

    bins: int = 0
    institutions: int = 0
    countries: int = 0
    networks: int = 0
    bin_ranges: int = 0
    aliases: int = 0
    addresses: int = 0
    conflicts_open: int = 0


class DatabaseInfo(BaseModel):
    """Everything the Dashboard and Database pages need in one object."""

    model_config = ConfigDict(frozen=True)

    installed: bool = False
    path: str = ""
    version: str | None = None
    schema_version: int | None = None
    release_date: datetime | None = None
    installed_at: datetime | None = None
    last_verified: datetime | None = None
    size_bytes: int | None = None
    record_count: int | None = None
    publisher: str | None = None
    stats: DatabaseStats = Field(default_factory=DatabaseStats)
    healthy: bool = True
    status_message: str = "Ready"

    @property
    def version_display(self) -> str:
        return display(self.version)

# ---------------------------------------------------------------------------
# Advanced search
# ---------------------------------------------------------------------------


class MatchMode(StrEnum):
    """How a text criterion is matched."""

    EXACT = "exact"
    PREFIX = "prefix"
    CONTAINS = "contains"
    FUZZY = "fuzzy"

    @property
    def label(self) -> str:
        return {
            MatchMode.EXACT: "Exact match",
            MatchMode.PREFIX: "Starts with",
            MatchMode.CONTAINS: "Contains",
            MatchMode.FUZZY: "Similar to",
        }[self]


class AdvancedQuery(BaseModel):
    """A multi-criteria search across BIN, issuer and geography.

    Every field is optional; an empty query matches everything, which is what
    makes this usable as a browse surface as well as a search.
    """

    model_config = ConfigDict(frozen=True)

    bin_prefix: str | None = None
    bin_from: str | None = None
    bin_to: str | None = None
    institution: str | None = None
    institution_match: MatchMode = MatchMode.CONTAINS
    country_code: str | None = None
    region: str | None = None
    city: str | None = None
    postal_code: str | None = None
    network_code: str | None = None
    brand: str | None = None
    card_type: str | None = None
    funding_type: str | None = None
    currency: str | None = None
    status: str | None = None
    prepaid: bool | None = None
    commercial: bool | None = None
    updated_after: datetime | None = None
    updated_before: datetime | None = None

    @property
    def is_empty(self) -> bool:
        return not any(
            value not in (None, "")
            for key, value in self.model_dump().items()
            if key != "institution_match"
        )

    @property
    def active_criteria(self) -> list[tuple[str, str]]:
        """Label/value pairs describing what was searched for."""
        labels = {
            "bin_prefix": "BIN starts with",
            "bin_from": "BIN from",
            "bin_to": "BIN to",
            "institution": "Institution",
            "country_code": "Country",
            "region": "State / province",
            "city": "City",
            "postal_code": "Postal code",
            "network_code": "Network",
            "brand": "Card brand",
            "card_type": "Card type",
            "funding_type": "Funding type",
            "currency": "Currency",
            "status": "Status",
            "prepaid": "Prepaid",
            "commercial": "Commercial",
            "updated_after": "Updated after",
            "updated_before": "Updated before",
        }
        rows: list[tuple[str, str]] = []
        for key, label in labels.items():
            value = getattr(self, key, None)
            if value in (None, ""):
                continue
            if isinstance(value, bool):
                rows.append((label, "Yes" if value else "No"))
            elif isinstance(value, datetime):
                rows.append((label, value.strftime("%d %b %Y")))
            else:
                rows.append((label, str(value)))
        if self.institution:
            rows.append(("Name matching", self.institution_match.label))
        return rows

    def describe(self) -> str:
        criteria = self.active_criteria
        if not criteria:
            return "All records"
        return " · ".join(f"{label}: {value}" for label, value in criteria)


class AdvancedSearchResult(BaseModel):
    """A page of advanced-search results plus its criteria."""

    model_config = ConfigDict(frozen=True)

    query: AdvancedQuery
    page: Page[BinRow]
    elapsed_ms: float = 0.0

    @property
    def found(self) -> bool:
        return self.page.total > 0
