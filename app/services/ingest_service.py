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
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.logging_config import get_logger
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
    RecordStatus,
    RelationshipType,
    Source,
)
from app.normalizers.bin_normalizer import bin_normalizer
from app.normalizers.card_normalizer import card_normalizer
from app.normalizers.geo_normalizer import geo_normalizer
from app.normalizers.name_normalizer import name_normalizer
from app.normalizers.network_normalizer import NETWORKS, network_normalizer
from app.normalizers.reference import BY_ISO2
from app.normalizers.text import squash

logger = get_logger(__name__)

#: Fields whose disagreement is worth recording as a conflict rather than a
#: silent overwrite. Timestamps and confidence are excluded by design.
CONFLICT_FIELDS = (
    "network",
    "brand",
    "card_type",
    "funding_type",
    "country",
    "currency_code",
    "issuer",
    "status",
)


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
            self._enrich_institution(cached, legal_name, website, swift_bic, country)
            self._add_aliases(cached, [*aliases, *self._legal_aliases(cached, legal_name)])
            return cached

        candidates = (
            self._session.execute(
                select(Institution).where(Institution.normalized_name == normalized.normalized)
            )
            .scalars()
            .all()
        )
        match = self._pick_institution(candidates, country, website, swift_bic, display)
        if match is not None:
            self._institution_cache[cache_key] = match
            if result is not None:
                result.institutions_matched += 1
            self._enrich_institution(match, legal_name, website, swift_bic, country)
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
    def _legal_aliases(institution: Institution, legal_name: str | None) -> list[str]:
        """Index the legal name as an alias when it differs from the display name."""
        name = legal_name or institution.legal_name
        if not name or squash(name) == squash(institution.display_name):
            return []
        return [name]

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
            # An exact normalized-name hit inside the same country is itself
            # sufficient; otherwise corroboration is required.
            same_country = bool(
                country and candidate.country_id and candidate.country_id == country.id
            )
            exact_name = squash(candidate.display_name) == squash(display)
            if (exact_name and (same_country or country is None)) or score.can_merge:
                if best is None or score.score > best[0]:
                    best = (score.score, candidate)
        return best[1] if best else None

    def _enrich_institution(
        self,
        institution: Institution,
        legal_name: str | None,
        website: str | None,
        swift_bic: str | None,
        country: Country | None,
    ) -> None:
        """Fill in blanks only — never overwrite an existing value."""
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

    # -- main entry point -------------------------------------------------
    def ingest(self, raw: RawBinRecord, result: IngestResult | None = None) -> str:
        """Normalize and write one record. Returns the action taken."""
        result = result if result is not None else IngestResult()
        result.processed += 1
        try:
            normalized = bin_normalizer.normalize(raw.bin)
        except Exception as exc:  # noqa: BLE001 - a bad row must not abort a run
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
            "brand": raw.brand or (network.display_name if network else None),
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
                bin_int=normalized.bin_int,
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
            changed = self._merge_bin(record, values, raw, result)
            action = "updated" if changed else "unchanged"
            if changed:
                result.updated += 1
            else:
                result.unchanged += 1

        self._record_claims(record, raw, values)

        if institution is not None:
            self._link(record, institution, RelationshipType.ISSUER, raw.confidence, primary=True)
        if parent is not None and (institution is None or parent.id != institution.id):
            self._link(record, parent, RelationshipType.PARENT, raw.confidence * 0.9, primary=False)

        if raw.bin_high:
            self._ensure_range(raw, normalized.bin, institution, network, country, values, result)

        return action

    def _merge_bin(
        self, record: Bin, values: dict[str, Any], raw: RawBinRecord, result: IngestResult
    ) -> bool:
        """Fill blanks; record disagreements instead of overwriting."""
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
                if raw.confidence > record.confidence:
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
        self._session.add(
            BinInstitution(
                bin_id=record.id,
                institution_id=institution.id,
                relationship_type=relationship.value,
                is_primary=primary,
                confidence=confidence,
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
        except Exception:  # noqa: BLE001 - malformed range is not fatal
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
                confidence=raw.confidence,
            )
        )
        result.ranges_created += 1


def _uid(prefix: str, *parts: str) -> str:
    digest = hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:24]
    return f"{prefix}_{digest}"


def _fingerprint(*parts: str | None) -> str:
    joined = "|".join(squash(part) for part in parts)
    return hashlib.sha1(joined.encode("utf-8")).hexdigest()[:40]
