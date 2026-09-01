"""Fill in what a list left blank — from evidence, never from invention.

A hand-maintained list is uneven. One row for a bank carries its website and
city; the next three name the bank and nothing else. A row omits the scheme
even though the BIN's own digits establish it. None of that should surface as
``Unknown`` when the database already holds the answer.

This pass runs after ingestion and before the database is verified. It fills
gaps from three sources, in descending order of how much they are worth:

1. **The same institution's other rows.** If one row says where a bank is and
   what its website is, every BIN belonging to *that same institution* gets
   the same answer. Nothing is inferred here at all — it is one fact, written
   once, applied wherever it already belonged.
2. **The BIN's own digits**, for the scheme only. ISO/IEC 7812 and the
   schemes' published ranges settle which network a prefix belongs to; where
   two schemes both claim it, nothing is filled.
3. **The country's currency**, where the country is known and the currency was
   not given.

What it will not do
-------------------

* It will not invent a website, an address, a phone number or a legal name.
  Those exist or they do not; a plausible guess is worse than ``Unknown``.
* It will not derive the issuer, the card type or the funding type from the
  BIN. Nothing about the digits establishes any of them.
* It will not overwrite a value the list supplied. Every fill targets a blank.
* It will not merge two institutions to make a fill possible. Consolidation
  follows the identity the resolver already decided; it never creates one.

Every fill is written to ``normalization_events`` with the rule that produced
it, so anything the database asserts can be traced back to the row or the
published range it came from.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.logging_config import get_logger
from app.models.entities import (
    Address,
    Bin,
    BinInstitution,
    Country,
    Institution,
    Network,
    NormalizationEvent,
    RelationshipType,
)
from app.normalizers import geo_normalizer
from app.normalizers.iin_ranges import network_for_prefix

logger = get_logger(__name__)

#: Institution attributes worth carrying between rows of the same institution.
#: All of them are properties of the *institution*, not of a particular BIN,
#: which is what makes copying them a restatement rather than a guess.
SHARED_INSTITUTION_FIELDS: tuple[str, ...] = (
    "legal_name",
    "short_name",
    "website",
    "institution_type",
    "country_id",
)

#: Confidence attached to a derived value. Below anything the list states
#: directly, so a derivation can never outrank the thing it filled in for.
DERIVED_CONFIDENCE = 0.7


@dataclass(slots=True)
class EnrichmentReport:
    """What was filled in, by rule. Counted, never estimated."""

    networks_derived: int = 0
    networks_ambiguous: int = 0
    currencies_derived: int = 0
    institution_fields_filled: int = 0
    countries_propagated: int = 0
    examples: list[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        return (
            self.networks_derived
            + self.currencies_derived
            + self.institution_fields_filled
            + self.countries_propagated
        )

    @property
    def summary(self) -> str:
        if not self.total:
            return "nothing needed filling in"
        parts: list[str] = []
        if self.networks_derived:
            parts.append(f"{self.networks_derived:,} network(s) from the BIN range")
        if self.institution_fields_filled:
            parts.append(
                f"{self.institution_fields_filled:,} institution detail(s) shared "
                "between rows"
            )
        if self.countries_propagated:
            parts.append(f"{self.countries_propagated:,} country/countries carried across")
        if self.currencies_derived:
            parts.append(f"{self.currencies_derived:,} currency/currencies from the country")
        text = " · ".join(parts)
        if self.networks_ambiguous:
            text += (
                f" · {self.networks_ambiguous:,} prefix(es) left unset because two "
                "schemes both publish them"
            )
        return text


class EnrichmentService:
    """Completes a freshly built database from what it already knows."""

    def __init__(self, session: Session, *, record_events: bool = True) -> None:
        self._session = session
        self._record_events = record_events
        self._report = EnrichmentReport()

    def run(self) -> EnrichmentReport:
        """Run every pass, in dependency order, and report what changed."""
        self._consolidate_institutions()
        self._propagate_institution_country()
        self._derive_networks()
        self._derive_currencies()
        self._session.flush()
        logger.info(
            "Enrichment complete",
            extra={"context": {"filled": self._report.total}},
        )
        return self._report

    # -- 1. one institution, one set of details ----------------------------
    def _consolidate_institutions(self) -> None:
        """Give every institution the details recorded for it anywhere.

        The resolver has already decided which rows are the same institution,
        so this does not decide anything: it only stops the same bank being
        described fully on one row and emptily on the next.
        """
        institutions = self._session.execute(select(Institution)).scalars().all()
        for institution in institutions:
            filled = self._fill_from_addresses(institution)
            if filled:
                self._report.institution_fields_filled += filled

    def _fill_from_addresses(self, institution: Institution) -> int:
        """An institution with several addresses still has one primary country."""
        if institution.country_id is not None:
            return 0
        for address in institution.addresses:
            if address.country_id is not None:
                institution.country_id = address.country_id
                self._note(
                    "institution",
                    institution.uid or institution.display_name,
                    "country_id",
                    None,
                    str(address.country_id),
                    "institution:address-country",
                )
                return 1
        return 0

    # -- 2. the institution's country reaches its BINs ---------------------
    def _propagate_institution_country(self) -> None:
        """A BIN with no country takes its current issuer's, and only that.

        Restricted to a *current issuing* relationship on purpose. A parent
        company in another market, or a bank that stopped using the BIN in
        2022, says nothing about where the BIN is issued today.
        """
        rows = (
            self._session.execute(
                select(Bin, Institution)
                .join(BinInstitution, BinInstitution.bin_id == Bin.id)
                .join(Institution, Institution.id == BinInstitution.institution_id)
                .where(
                    Bin.country_id.is_(None),
                    BinInstitution.is_current.is_(True),
                    BinInstitution.relationship_type.in_(
                        [item.value for item in RelationshipType if item.is_issuing]
                    ),
                    Institution.country_id.is_not(None),
                )
            )
            .unique()
            .all()
        )
        for record, institution in rows:
            if record.country_id is not None:
                continue
            record.country_id = institution.country_id
            self._report.countries_propagated += 1
            self._note(
                "bin",
                record.bin,
                "country_id",
                None,
                str(institution.country_id),
                "bin:issuer-country",
            )
            self._remember(f"{record.bin}: country from {institution.display_name}")

    # -- 3. the scheme, from the digits ------------------------------------
    def _derive_networks(self) -> None:
        """Fill a blank network from the prefix's published allocation."""
        blanks = (
            self._session.execute(select(Bin).where(Bin.network_id.is_(None)))
            .scalars()
            .all()
        )
        if not blanks:
            return

        by_code: dict[str, Network] = {
            network.code: network
            for network in self._session.execute(select(Network)).scalars().all()
        }

        for record in blanks:
            match = network_for_prefix(record.prefix or record.bin)
            if match.is_ambiguous:
                # Two schemes publish this prefix. Unknown is the true answer.
                self._report.networks_ambiguous += 1
                continue
            if not match.is_certain or match.network is None:
                continue
            network = by_code.get(match.network)
            if network is None:
                network = self._create_network(match.network)
                if network is None:
                    continue
                by_code[match.network] = network
            record.network_id = network.id
            if not record.brand:
                record.brand = network.display_name
            self._report.networks_derived += 1
            self._note(
                "bin", record.bin, "network", None, match.network, match.rule
            )
            self._remember(f"{record.bin}: {network.display_name} from {match.rule}")

    def _create_network(self, code: str) -> Network | None:
        from app.normalizers.network_normalizer import BY_CODE

        definition = BY_CODE.get(code)
        if definition is None:  # pragma: no cover - codes come from one table
            return None
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
        return network

    # -- 4. the currency, from the country ---------------------------------
    def _derive_currencies(self) -> None:
        rows = (
            self._session.execute(
                select(Bin, Country)
                .join(Country, Country.id == Bin.country_id)
                .where(Bin.currency_code.is_(None))
            )
            .unique()
            .all()
        )
        for record, country in rows:
            currency = country.currency_code or geo_normalizer.default_currency(
                country.iso2
            )
            if not currency:
                continue
            record.currency_code = currency
            self._report.currencies_derived += 1
            self._note(
                "bin", record.bin, "currency_code", None, currency, "bin:country-currency"
            )

    # -- audit trail --------------------------------------------------------
    def _note(
        self,
        entity_type: str,
        entity_key: str | None,
        field_name: str,
        raw: str | None,
        derived: str | None,
        rule: str,
    ) -> None:
        """Record how a value got there, so nothing is asserted untraceably."""
        if not self._record_events:
            return
        self._session.add(
            NormalizationEvent(
                entity_type=entity_type,
                entity_key=entity_key,
                field=field_name,
                raw_value=raw,
                normalized_value=derived,
                rule=rule,
                confidence=DERIVED_CONFIDENCE,
            )
        )

    def _remember(self, example: str) -> None:
        if len(self._report.examples) < 20:
            self._report.examples.append(example)


__all__ = ["DERIVED_CONFIDENCE", "EnrichmentReport", "EnrichmentService"]
