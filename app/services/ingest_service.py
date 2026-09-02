"""Writing normalized BIN intelligence into the database.

Every importer and the sample-package builder funnel through
:class:`IngestService`, so normalization, deduplication and conflict handling
happen in exactly one place regardless of the source format.

Conflict policy: when an incoming value disagrees with a stored value for the
same field, the stored value is **not** silently overwritten. The higher-
confidence claim wins the user-facing field and the losing claim is preserved
in ``conflicts`` (and in ``bin_claims``) so the disagreement is recoverable.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.logging_config import get_logger
from app.lookup.evidence import score_relationship
from app.lookup.strategy import LookupStrategy
from app.models.entities import (
    Address,
    AliasType,
    Bin,
    BinClaim,
    BinInstitution,
    BinRange,
    CardType,
    Conflict,
    ConflictStatus,
    Country,
    FundingType,
    Institution,
    InstitutionAlias,
    Network,
    NormalizationEvent,
    RangeType,
    RecordStatus,
    RelationshipType,
    Source,
    SourceRow,
)
from app.normalizers.bin_normalizer import bin_normalizer
from app.normalizers.card_normalizer import card_normalizer
from app.normalizers.geo_normalizer import geo_normalizer
from app.normalizers.name_normalizer import name_normalizer
from app.normalizers.network_normalizer import NETWORKS, network_normalizer
from app.normalizers.reference import BY_ISO2
from app.normalizers.text import sanitise_text, squash

logger = get_logger(__name__)

#: Fields whose disagreement is worth recording as a conflict rather than a
#: silent overwrite. Timestamps and confidence are excluded by design.
CONFLICT_FIELDS = (
    "network",
    "brand",
    "card_level",
    "card_type",
    "funding_type",
    "country",
    "currency_code",
    "issuer",
    "status",
)


def _brand_label(brand: str | None, network: Network | None) -> str | None:
    """The brand to store, tidied but never invented.

    Sources shout their scheme in the brand column — ``VISA``, ``MASTERCARD``.
    Where the brand names the very scheme already resolved for the record, the
    catalogue's spelling of that scheme is used, so one result does not read
    "Visa" and the next "VISA". Anything the catalogue does not recognise
    (``PAGOBANCOMAT``, ``LOCAL BRAND``) is kept exactly as the source wrote
    it — the words are evidence, and rewriting them would be a guess.
    """
    text = sanitise_text(brand, limit=64)
    if text is None:
        return network.display_name if network else None
    if network is not None and network_normalizer.normalize(text).code == network.code:
        return network.display_name
    return text


class RawBinRecord(BaseModel):
    """A source's un-normalized assertion about one BIN.

    Every field is optional and free-text: importers hand over whatever the
    source provided and the ingest service decides what it means.
    """

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    bin: str
    bin_high: str | None = None
    iin_length: int | None = None
    network: str | None = None
    brand: str | None = None
    card_type: str | None = None
    #: The product tier a source names — STANDARD, GOLD, PLATINUM, WORLD,
    #: TITANIUM, BUSINESS. Kept as its own field rather than folded into the
    #: brand, because "Visa" and "Gold" are two different facts about a card.
    card_level: str | None = None
    funding_type: str | None = None
    prepaid: str | bool | None = None
    commercial: str | bool | None = None
    issuer: str | None = None
    issuer_legal_name: str | None = None
    parent_institution: str | None = None
    institution_type: str | None = None
    website: str | None = None
    swift_bic: str | None = None
    country: str | None = None
    currency: str | None = None
    state: str | None = None
    city: str | None = None
    postal_code: str | None = None
    address_line1: str | None = None
    address_line2: str | None = None
    phone: str | None = None
    status: str | None = None
    aliases: list[str] = []
    confidence: float = 0.8
    #: What kind of allocation a range row describes. An account range is the
    #: most specific authoritative allocation there is, and ranking depends on
    #: knowing which kind arrived.
    range_type: str | None = None
    #: When the relationship this record asserts began and ended. A record with
    #: an end date describes a former issuer, not a current one.
    effective_from: datetime | None = None
    effective_to: datetime | None = None
    #: How this record relates the institution to the BIN. Defaults to issuer.
    relationship: str | None = None
    #: The row this record was read from, under the source's own column names.
    #: Carried so nothing a source said is lost to the curated fields, which
    #: are narrower on purpose. Never interpreted here — it is kept, not read.
    source_row: dict[str, str] = {}
    #: Where that row came from: file name and line, for checking against.
    source_file: str | None = None
    source_line: int | None = None


@dataclass(slots=True)
class IngestResult:
    """Counters describing what an import run did."""

    processed: int = 0
    created: int = 0
    updated: int = 0
    unchanged: int = 0
    skipped: int = 0
    institutions_created: int = 0
    institutions_matched: int = 0
    ranges_created: int = 0
    conflicts: int = 0
    errors: list[str] = field(default_factory=list)

    def merge(self, other: IngestResult) -> IngestResult:
        self.processed += other.processed
        self.created += other.created
        self.updated += other.updated
        self.unchanged += other.unchanged
        self.skipped += other.skipped
        self.institutions_created += other.institutions_created
        self.institutions_matched += other.institutions_matched
        self.ranges_created += other.ranges_created
        self.conflicts += other.conflicts
        self.errors.extend(other.errors)
        return self

    @property
    def summary(self) -> str:
        return (
            f"{self.processed:,} processed · {self.created:,} created · "
            f"{self.updated:,} updated · {self.skipped:,} skipped · "
            f"{self.conflicts:,} conflicts"
        )


class IngestService:
    """Normalizes and writes records, caching reference lookups per session."""

    def __init__(
        self,
        session: Session,
        *,
        source_code: str = "manual",
        source_name: str = "Manual import",
        dry_run: bool = False,
        record_normalization: bool = False,
    ) -> None:
        self._session = session
        self._dry_run = dry_run
        self._record_normalization = record_normalization
        self._country_cache: dict[str, Country] = {}
        self._network_cache: dict[str, Network] = {}
        self._institution_cache: dict[tuple[str, str], Institution] = {}
        self._source = None if dry_run else self._ensure_source(source_code, source_name)

    # -- reference data ---------------------------------------------------
    def _ensure_source(self, code: str, name: str) -> Source:
        source = self._session.execute(
            select(Source).where(Source.code == code)
        ).scalar_one_or_none()
        if source is None:
            source = Source(code=code, name=name, trust_score=0.7)
            self._session.add(source)
            self._session.flush()
        return source

    def ensure_country(self, value: str | None) -> Country | None:
        record = geo_normalizer.country(value)
        if record is None:
            return None
        if record.iso2 in self._country_cache:
            return self._country_cache[record.iso2]
        country = self._session.execute(
            select(Country).where(Country.iso2 == record.iso2)
        ).scalar_one_or_none()
        if country is None:
            country = Country(
                iso2=record.iso2,
                iso3=record.iso3,
                numeric_code=record.numeric,
                name=record.name,
                normalized_name=squash(record.name),
                currency_code=record.currency or None,
                region=record.region,
            )
            self._session.add(country)
            self._session.flush()
        self._country_cache[record.iso2] = country
        return country

    def ensure_network(self, value: str | None) -> Network | None:
        definition = network_normalizer.normalize(value)
        if definition.code == "unknown":
            return None
        if definition.code in self._network_cache:
            return self._network_cache[definition.code]
        network = self._session.execute(
            select(Network).where(Network.code == definition.code)
        ).scalar_one_or_none()
        if network is None:
            network = Network(
                code=definition.code,
                name=definition.name,
                display_name=definition.display_name,
                scheme_type=definition.scheme_type,
                is_global=definition.is_global,
                accent_color=definition.accent_color,
            )
            self._session.add(network)
            self._session.flush()
        self._network_cache[definition.code] = network
        return network

    def seed_reference_data(self) -> None:
        """Pre-populate the network catalogue and the ISO country table."""
        for definition in NETWORKS:
            if definition.code == "unknown":
                continue
            self.ensure_network(definition.code)
        for record in BY_ISO2.values():
            self.ensure_country(record.iso2)
        self._session.flush()

    # -- institutions -----------------------------------------------------
    def ensure_institution(
        self,
        name: str | None,
        *,
        legal_name: str | None = None,
        country: Country | None = None,
        website: str | None = None,
        swift_bic: str | None = None,
        institution_type: str | None = None,
        aliases: Iterable[str] = (),
        result: IngestResult | None = None,
    ) -> Institution | None:
        """Find or create an institution, matching on evidence, not string luck."""
        display = name_normalizer.clean_display(name or "")
        if not display:
            return None
        normalized = name_normalizer.normalize(display)
        cache_key = (normalized.normalized, country.iso2 if country else "")
        cached = self._institution_cache.get(cache_key)
        if cached is not None:
            if result is not None:
                result.institutions_matched += 1
            # A later row may carry details the first one lacked (a legal name,
            # a website), so a cache hit still enriches and re-indexes aliases.
            self._enrich_institution(
                cached, legal_name, website, swift_bic, country, display=display
            )
            self._add_aliases(cached, [*aliases, *self._legal_aliases(cached, legal_name)])
            return cached

        candidates = list(
            self._session.execute(
                select(Institution).where(Institution.normalized_name == normalized.normalized)
            )
            .scalars()
            .all()
        )
        # Aliases are curated identity, so they belong in the candidate set: a
        # record that arrives as "MTB" carrying "Meridian Trust Bank" as an
        # alias is describing an institution that is probably already here, and
        # creating a second record for it would split its BIN portfolio in two.
        candidates.extend(
            item
            for item in self._alias_candidates(display, aliases)
            if all(item.id != existing.id for existing in candidates)
        )
        match = self._pick_institution(candidates, country, website, swift_bic, display)
        if match is not None:
            self._institution_cache[cache_key] = match
            if result is not None:
                result.institutions_matched += 1
            self._enrich_institution(
                match, legal_name, website, swift_bic, country, display=display
            )
            self._add_aliases(match, aliases)
            return match

        institution = Institution(
            uid=_uid("inst", normalized.normalized, country.iso2 if country else ""),
            display_name=display,
            legal_name=name_normalizer.clean_display(legal_name or "") or None,
            normalized_name=normalized.normalized,
            normalized_legal_name=name_normalizer.normalized_form(legal_name) or None,
            short_name=normalized.acronym if len(normalized.acronym) >= 3 else None,
            institution_type=(institution_type or "bank"),
            country_id=country.id if country else None,
            website=website or None,
            swift_bic=(swift_bic or "").upper()[:11] or None,
            status=RecordStatus.ACTIVE.value,
            confidence=1.0,
        )
        self._session.add(institution)
        self._session.flush()
        self._institution_cache[cache_key] = institution
        if result is not None:
            result.institutions_created += 1

        self._add_aliases(
            institution,
            [
                *aliases,
                *name_normalizer.candidate_aliases(display),
                *self._legal_aliases(institution, legal_name),
            ],
        )
        return institution

    @staticmethod
    def _is_fuller_name(incoming: str, existing: str) -> bool:
        """Whether *incoming* is the same name, spelled out more completely.

        Only true when the two normalise to the same thing — so this can never
        rename an institution to a different one — and the incoming spelling
        uses more words, which is what expanding an abbreviation does.
        """
        if not incoming or not existing or squash(incoming) == squash(existing):
            return False
        if name_normalizer.core_form(incoming) != name_normalizer.core_form(existing):
            return False
        return len(incoming.split()) > len(existing.split())

    @staticmethod
    def _legal_aliases(institution: Institution, legal_name: str | None) -> list[str]:
        """Index the legal name as an alias when it differs from the display name."""
        name = legal_name or institution.legal_name
        if not name or squash(name) == squash(institution.display_name):
            return []
        return [name]

    def _alias_candidates(
        self, display: str, aliases: Iterable[str]
    ) -> list[Institution]:
        """Institutions reachable through a recorded alias, either direction.

        Either the incoming name is already an alias of an existing record, or
        one of the incoming aliases *is* an existing record's name.
        """
        keys = {name_normalizer.normalized_form(display)}
        keys.update(name_normalizer.normalized_form(alias) for alias in aliases)
        keys.discard("")
        if not keys:
            return []
        by_alias = (
            self._session.execute(
                select(Institution)
                .join(InstitutionAlias)
                .where(InstitutionAlias.normalized_alias.in_(keys))
            )
            .unique()
            .scalars()
            .all()
        )
        by_name = (
            self._session.execute(
                select(Institution).where(Institution.normalized_name.in_(keys))
            )
            .scalars()
            .all()
        )
        found: dict[int, Institution] = {}
        for item in (*by_alias, *by_name):
            found.setdefault(item.id, item)
        return list(found.values())

    def _pick_institution(
        self,
        candidates: list[Institution],
        country: Country | None,
        website: str | None,
        swift_bic: str | None,
        display: str,
    ) -> Institution | None:
        """Choose an existing institution only when the evidence supports it."""
        best: tuple[float, Institution] | None = None
        for candidate in candidates:
            score = name_normalizer.match(
                display,
                candidate.display_name,
                left_country=country.iso2 if country else None,
                right_country=candidate.country.iso2 if candidate.country else None,
                left_website=website,
                right_website=candidate.website,
                left_swift=swift_bic,
                right_swift=candidate.swift_bic,
            )
            # An exact normalized-name hit is sufficient when the countries
            # agree, when either side's country is unknown, or when something
            # independent corroborates it.
            #
            # The last case matters more than it looks. The country arriving
            # with a record is the country the *BIN* is issued in, which is not
            # the institution's country of domicile: one bank issues in several
            # markets. Treating issuance country as institution identity would
            # split a single bank into one record per market — so it is used as
            # evidence, never as a discriminator on its own.
            same_country = bool(
                country and candidate.country_id and candidate.country_id == country.id
            )
            country_unknown = country is None or candidate.country_id is None
            corroborated = (
                score.evidence.same_website_host or score.evidence.same_swift_bic
            )
            exact_name = squash(candidate.display_name) == squash(display)
            alias_hit = self._is_alias_of(candidate, display)
            if (
                (exact_name or alias_hit)
                and (same_country or country_unknown or corroborated)
            ) or score.can_merge:
                if best is None or score.score > best[0]:
                    best = (score.score, candidate)
        return best[1] if best else None

    def _is_alias_of(self, candidate: Institution, display: str) -> bool:
        """Whether *display* is a recorded alias of *candidate*."""
        normalized = name_normalizer.normalized_form(display)
        if not normalized:
            return False
        return any(alias.normalized_alias == normalized for alias in candidate.aliases)

    def _enrich_institution(
        self,
        institution: Institution,
        legal_name: str | None,
        website: str | None,
        swift_bic: str | None,
        country: Country | None,
        *,
        display: str | None = None,
    ) -> None:
        """Fill in blanks only — never overwrite an existing value.

        The one exception is the display name: when a later row spells out a
        name the first row abbreviated ("Northshore CU" then "Northshore Credit
        Union"), the fuller spelling becomes the name shown and the shorter one
        is kept as an alias. Naming institutions is what this application is
        for, so the better name wins.
        """
        if display and self._is_fuller_name(display, institution.display_name):
            previous = institution.display_name
            institution.display_name = display
            self._add_aliases(institution, [previous], AliasType.ABBREVIATION)
        if legal_name and not institution.legal_name:
            institution.legal_name = name_normalizer.clean_display(legal_name) or None
            institution.normalized_legal_name = (
                name_normalizer.normalized_form(legal_name) or None
            )
        if website and not institution.website:
            institution.website = website
        if swift_bic and not institution.swift_bic:
            institution.swift_bic = swift_bic.upper()[:11]
        if country and not institution.country_id:
            institution.country_id = country.id

    def _add_aliases(
        self,
        institution: Institution,
        aliases: Iterable[str],
        alias_type: AliasType = AliasType.VARIANT,
    ) -> None:
        existing = {
            alias.normalized_alias
            for alias in self._session.execute(
                select(InstitutionAlias).where(
                    InstitutionAlias.institution_id == institution.id
                )
            )
            .scalars()
            .all()
        }
        for alias in aliases:
            cleaned = name_normalizer.clean_display(alias or "")
            normalized = name_normalizer.normalized_form(cleaned)
            if not normalized or normalized in existing:
                continue
            existing.add(normalized)
            self._session.add(
                InstitutionAlias(
                    institution_id=institution.id,
                    alias=cleaned,
                    normalized_alias=normalized,
                    alias_type=alias_type.value,
                    confidence=0.8,
                    source_id=self._source.id if self._source else None,
                )
            )

    # -- addresses --------------------------------------------------------
    def ensure_address(self, institution: Institution, raw: RawBinRecord, country: Country | None) -> None:
        region = geo_normalizer.region(raw.state, raw.country)
        city = geo_normalizer.city(raw.city)
        postal = geo_normalizer.postal_code(raw.postal_code, raw.country)
        if not any((raw.address_line1, city, region.name, postal)):
            return
        fingerprint = _fingerprint(
            raw.address_line1, city, region.code or region.name, postal, country.iso2 if country else ""
        )
        existing = self._session.execute(
            select(Address).where(
                Address.institution_id == institution.id, Address.fingerprint == fingerprint
            )
        ).scalar_one_or_none()
        if existing is not None:
            return
        has_primary = (
            self._session.execute(
                select(Address.id).where(
                    Address.institution_id == institution.id, Address.is_primary.is_(True)
                )
            ).scalar()
            is not None
        )
        self._session.add(
            Address(
                institution_id=institution.id,
                line1=raw.address_line1 or None,
                line2=raw.address_line2 or None,
                city=city,
                normalized_city=geo_normalizer.normalized_city(raw.city),
                region=region.name,
                region_code=region.code,
                postal_code=postal,
                normalized_postal_code=geo_normalizer.normalized_postal_code(raw.postal_code),
                country_id=country.id if country else None,
                fingerprint=fingerprint,
                is_primary=not has_primary,
                confidence=raw.confidence,
            )
        )

    def _archive_source_row(self, record: Bin, raw: RawBinRecord) -> None:
        """Keep the row this record was read from, under its own headers.

        The curated columns are an interpretation and a narrow one: a country
        spelled three ways becomes one code, a coordinate pair Bin-Tel will
        not assert as an address is not stored as one, and a column it has no
        field for has nowhere to go. All of that still happened, and the row
        is what says so.

        Written once per row, not once per field, and skipped entirely when a
        record arrived without one — an external reading has no file behind it.
        """
        if not raw.source_row:
            return
        self._session.add(
            SourceRow(
                bin_id=record.id,
                # The name only. A full path would put a home directory into a
                # database that is meant to be portable.
                source_file=(raw.source_file or "")[:128] or None,
                line_number=raw.source_line,
                payload=json.dumps(raw.source_row, ensure_ascii=False),
            )
        )

    # -- main entry point -------------------------------------------------
    def ingest(self, raw: RawBinRecord, result: IngestResult | None = None) -> str:
        """Normalize and write one record. Returns the action taken."""
        result = result if result is not None else IngestResult()
        result.processed += 1
        try:
            normalized = bin_normalizer.normalize(raw.bin)
        except Exception as exc:
            result.skipped += 1
            result.errors.append(f"{raw.bin!r}: {exc}")
            return "skipped"

        country = self.ensure_country(raw.country)
        network = self.ensure_network(raw.network)
        card_type = card_normalizer.card_type(raw.card_type)
        funding = card_normalizer.funding_type(raw.funding_type, card_type)
        status = card_normalizer.status(raw.status)

        institution = self.ensure_institution(
            raw.issuer,
            legal_name=raw.issuer_legal_name,
            country=country,
            website=raw.website,
            swift_bic=raw.swift_bic,
            institution_type=raw.institution_type,
            aliases=raw.aliases,
            result=result,
        )
        if institution is not None:
            self.ensure_address(institution, raw, country)

        parent = None
        if raw.parent_institution:
            parent = self.ensure_institution(
                raw.parent_institution, country=country, result=result
            )
            if parent is not None and institution is not None and parent.id != institution.id:
                institution.parent_id = parent.id

        if self._dry_run:
            result.unchanged += 1
            return "dry-run"

        existing = self._session.execute(
            select(Bin).where(Bin.bin == normalized.bin)
        ).scalar_one_or_none()

        values: dict[str, Any] = {
            "network_id": network.id if network else None,
            "brand": _brand_label(raw.brand, network),
            "card_level": card_normalizer.card_level(raw.card_level),
            "card_type": card_type.value,
            "funding_type": funding.value,
            "is_prepaid": card_normalizer.is_prepaid(raw.prepaid, card_type),
            "is_commercial": card_normalizer.is_commercial(raw.commercial, raw.brand),
            "country_id": country.id if country else None,
            "currency_code": card_normalizer.currency(raw.currency)
            or geo_normalizer.default_currency(raw.country),
            "status": status.value if status is not RecordStatus.UNKNOWN else RecordStatus.ACTIVE.value,
        }

        if existing is None:
            record = Bin(
                bin=normalized.bin,
                iin=normalized.iin,
                iin_length=raw.iin_length or normalized.iin_length,
                # The published digits *are* the assignment: its length is
                # recorded rather than inferred, so a six-digit root and an
                # eight-digit assignment under it stay distinct records.
                prefix=normalized.prefix,
                prefix_length=normalized.prefix_length,
                prefix_type=normalized.prefix_type,
                bin_int=normalized.bin_int,
                span_low=normalized.range_low,
                span_high=normalized.range_high,
                prefix6=normalized.prefix6,
                prefix8=normalized.prefix8,
                confidence=raw.confidence,
                **values,
            )
            self._session.add(record)
            self._session.flush()
            action = "created"
            result.created += 1
        else:
            record = existing
            changed = self._merge_bin(
                record,
                values,
                raw,
                result,
                claim_is_current=(
                    raw.effective_to is None
                    and _relationship_for(raw) is not RelationshipType.FORMER_ISSUER
                ),
            )
            action = "updated" if changed else "unchanged"
            if changed:
                result.updated += 1
            else:
                result.unchanged += 1

        self._record_claims(record, raw, values)
        self._archive_source_row(record, raw)

        if institution is not None:
            self._link(
                record,
                institution,
                _relationship_for(raw),
                raw.confidence,
                primary=True,
                # A relationship recorded as a *former* issuer is never
                # current, whether or not an end date came with it.
                is_current=(
                    raw.effective_to is None
                    and _relationship_for(raw) is not RelationshipType.FORMER_ISSUER
                ),
                effective_from=raw.effective_from,
                effective_to=raw.effective_to,
            )
        if parent is not None and (institution is None or parent.id != institution.id):
            self._link(
                record,
                parent,
                RelationshipType.PARENT,
                raw.confidence * 0.9,
                primary=False,
                effective_from=raw.effective_from,
            )

        if raw.bin_high:
            self._ensure_range(raw, normalized.bin, institution, network, country, values, result)

        return action

    def _merge_bin(
        self,
        record: Bin,
        values: dict[str, Any],
        raw: RawBinRecord,
        result: IngestResult,
        *,
        claim_is_current: bool = True,
    ) -> bool:
        """Fill blanks; record disagreements instead of overwriting.

        Standing is weighed before confidence. A row describing a relationship
        that has *ended* may fill a gap, but it never displaces a value a
        present-tense row supplied — a BIN labelled with the country of the
        bank that stopped using it in 2024 is a false positive, however well
        that row was evidenced. Conversely a current claim takes a tie against
        a historical one, rather than the tie going to whichever row happened
        to be read first.
        """
        changed = False
        for key, incoming in values.items():
            if incoming in (None, "", CardType.UNKNOWN.value, FundingType.UNKNOWN.value):
                continue
            current = getattr(record, key)
            if current in (None, "", CardType.UNKNOWN.value, FundingType.UNKNOWN.value):
                setattr(record, key, incoming)
                changed = True
                continue
            if current == incoming:
                continue
            # A genuine disagreement between two defensible claims.
            field_name = key.removesuffix("_id")
            if field_name in CONFLICT_FIELDS or key in CONFLICT_FIELDS:
                self._record_conflict(record.bin, field_name, current, incoming, raw.confidence)
                result.conflicts += 1
                if not claim_is_current:
                    # Recorded as a conflict, never promoted: the past does not
                    # get to overrule the present.
                    continue
                supersedes = (
                    raw.confidence > record.confidence
                    or (raw.confidence == record.confidence and not record.has_current_issuer)
                )
                if supersedes:
                    setattr(record, key, incoming)
                    record.confidence = raw.confidence
                    changed = True
        if changed:
            record.last_updated = datetime.now(UTC)
        return changed

    def _record_conflict(
        self, entity_key: str, field_name: str, current: Any, incoming: Any, confidence: float
    ) -> None:
        value_a, value_b = str(current), str(incoming)
        existing = self._session.execute(
            select(Conflict).where(
                Conflict.entity_type == "bin",
                Conflict.entity_key == entity_key,
                Conflict.field == field_name,
                Conflict.value_a == value_a,
                Conflict.value_b == value_b,
            )
        ).scalar_one_or_none()
        if existing is not None:
            return
        self._session.add(
            Conflict(
                entity_type="bin",
                entity_key=entity_key,
                field=field_name,
                value_a=value_a,
                value_b=value_b,
                source_b_id=self._source.id if self._source else None,
                confidence_b=confidence,
                status=ConflictStatus.OPEN.value,
            )
        )

    def _record_claims(self, record: Bin, raw: RawBinRecord, values: dict[str, Any]) -> None:
        """Preserve lineage so a merge decision can always be re-examined."""
        if self._source is None:
            return
        interesting = {
            "network": raw.network,
            "card_type": raw.card_type,
            "funding_type": raw.funding_type,
            "issuer": raw.issuer,
            "country": raw.country,
        }
        for field_name, value in interesting.items():
            if not value:
                continue
            exists = self._session.execute(
                select(BinClaim.id).where(
                    BinClaim.bin_id == record.id,
                    BinClaim.source_id == self._source.id,
                    BinClaim.field == field_name,
                    BinClaim.value == str(value),
                )
            ).scalar()
            if exists is None:
                self._session.add(
                    BinClaim(
                        bin_id=record.id,
                        source_id=self._source.id,
                        field=field_name,
                        value=str(value),
                        confidence=raw.confidence,
                    )
                )
        if self._record_normalization and raw.issuer:
            self._session.add(
                NormalizationEvent(
                    entity_type="bin",
                    entity_key=record.bin,
                    field="issuer",
                    raw_value=raw.issuer,
                    normalized_value=name_normalizer.normalized_form(raw.issuer),
                    rule="name_normalizer",
                    confidence=raw.confidence,
                )
            )

    def _link(
        self,
        record: Bin,
        institution: Institution,
        relationship: RelationshipType,
        confidence: float,
        *,
        primary: bool,
        is_current: bool = True,
        effective_from: datetime | None = None,
        effective_to: datetime | None = None,
    ) -> None:
        existing = self._session.execute(
            select(BinInstitution).where(
                BinInstitution.bin_id == record.id,
                BinInstitution.institution_id == institution.id,
                BinInstitution.relationship_type == relationship.value,
            )
        ).scalar_one_or_none()
        if existing is not None:
            existing.last_updated = datetime.now(UTC)
            return
        scored = score_relationship(
            LookupStrategy.EXACT_ASSIGNED
            if (record.prefix_length or len(record.bin)) >= 8
            else LookupStrategy.EXACT_6,
            stored_confidence=confidence,
            relationship_is_issuing=relationship.is_issuing,
            is_current=is_current,
        )
        self._session.add(
            BinInstitution(
                bin_id=record.id,
                institution_id=institution.id,
                relationship_type=relationship.value,
                is_primary=primary,
                status=RecordStatus.ACTIVE.value
                if is_current
                else RecordStatus.RETIRED.value,
                effective_from=effective_from,
                effective_to=effective_to,
                is_current=is_current,
                confidence=confidence,
                confidence_level=scored.level.value,
                confidence_reasons="; ".join(scored.reasons) or None,
            )
        )

    def _ensure_range(
        self,
        raw: RawBinRecord,
        low: str,
        institution: Institution | None,
        network: Network | None,
        country: Country | None,
        values: dict[str, Any],
        result: IngestResult,
    ) -> None:
        try:
            normalized = bin_normalizer.normalize_range(low, raw.bin_high)
        except Exception:
            return
        existing = self._session.execute(
            select(BinRange).where(
                BinRange.range_low == normalized.low,
                BinRange.range_high == normalized.high,
                BinRange.institution_id == (institution.id if institution else None),
            )
        ).scalar_one_or_none()
        if existing is not None:
            return
        self._session.add(
            BinRange(
                range_low=normalized.low,
                range_high=normalized.high,
                range_low_int=normalized.low_int,
                range_high_int=normalized.high_int,
                width=normalized.width,
                institution_id=institution.id if institution else None,
                network_id=network.id if network else None,
                country_id=country.id if country else None,
                brand=values.get("brand"),
                card_type=values.get("card_type") or CardType.UNKNOWN.value,
                funding_type=values.get("funding_type") or FundingType.UNKNOWN.value,
                is_prepaid=values.get("is_prepaid"),
                is_commercial=values.get("is_commercial"),
                currency_code=values.get("currency_code"),
                status=values.get("status") or RecordStatus.ACTIVE.value,
                range_type=(raw.range_type or RangeType.ISSUER_RANGE.value),
                # Stored rather than computed, so "narrowest containing range"
                # is an indexed ORDER BY instead of a scan-and-subtract.
                span=normalized.high_int - normalized.low_int,
                effective_from=raw.effective_from,
                effective_to=raw.effective_to,
                is_current=raw.effective_to is None,
                confidence=raw.confidence,
            )
        )
        result.ranges_created += 1


def _relationship_for(raw: RawBinRecord) -> RelationshipType:
    """What kind of relationship a record asserts.

    A record carrying an end date describes an issuer that no longer issues,
    which is a different fact from a current issuer and must not be presented
    as one.
    """
    if raw.relationship:
        try:
            return RelationshipType(raw.relationship.strip().lower())
        except ValueError:
            logger.debug("Unknown relationship type in source data; treating as issuer")
    if raw.effective_to is not None:
        return RelationshipType.FORMER_ISSUER
    return RelationshipType.ISSUER


def _uid(prefix: str, *parts: str) -> str:
    digest = hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:24]
    return f"{prefix}_{digest}"


def _fingerprint(*parts: str | None) -> str:
    joined = "|".join(squash(part) for part in parts)
    return hashlib.sha1(joined.encode("utf-8")).hexdigest()[:40]
