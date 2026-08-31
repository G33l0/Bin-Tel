"""BIN/IIN queries.

Lookup strategy, in order, stopping at the first strategy that finds records:

1. **exact** — ``bins.bin = <query>``;
2. **prefix** — every stored BIN that the query extends (the user typed 8
   digits, the database holds the 6-digit IIN) *and* every stored BIN that
   falls under the query prefix (the user typed 4 digits);
3. **range** — an allocated ``bin_ranges`` block that contains the query.

All three are index-served: (1) and (2) by ``ix_bins_bin`` / ``ix_bins_bin_int``,
(3) by ``ix_bin_ranges_span``. Related rows are eagerly loaded, so building a
result never issues a query per institution.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from sqlalchemy import Select, and_, func, or_, select
from sqlalchemy.orm import Session, joinedload, selectinload

from app.models.entities import (
    Address,
    Bin,
    BinInstitution,
    BinRange,
    Country,
    Institution,
    Network,
    PrefixType,
    RangeType,
    RelationshipType,
)
from app.models.schemas import (
    AddressDTO,
    BinFilters,
    BinRecord,
    BinRow,
    CountryDTO,
    InstitutionSummary,
    NetworkDTO,
    Page,
    PageRequest,
    SortDirection,
)
from app.lookup.resolution import Candidate
from app.lookup.strategy import LookupStrategy
from app.normalizers.bin_normalizer import bin_normalizer
from app.repositories.base import BaseRepository

#: Columns the bank-result table may sort by, mapped to ORM expressions.
SORTABLE_COLUMNS: dict[str, Any] = {
    "bin": Bin.bin,
    "network": Network.name,
    "card_type": Bin.card_type,
    "funding_type": Bin.funding_type,
    "country": Country.name,
    "status": Bin.status,
}


class BinRepository(BaseRepository):
    """Read access to ``bins``, ``bin_ranges`` and their relationships."""

    # -- eager-loading options -------------------------------------------
    @staticmethod
    def _full_options() -> tuple[Any, ...]:
        return (
            joinedload(Bin.network),
            joinedload(Bin.country),
            selectinload(Bin.institution_links)
            .joinedload(BinInstitution.institution)
            .joinedload(Institution.country),
            selectinload(Bin.institution_links)
            .joinedload(BinInstitution.institution)
            .selectinload(Institution.addresses)
            .joinedload(Address.country),
        )

    # -- lookups ----------------------------------------------------------
    def find_exact(self, digits: str) -> BinRecord | None:
        with self.session() as session:
            record = self._select_full(session, Bin.bin == digits).unique().scalars().first()
            return self._to_record(record) if record else None

    def find_by_prefix(self, digits: str) -> list[BinRecord]:
        """Stored BINs that either extend the query or are extended by it.

        Kept for callers that want the raw list. The lookup engine uses
        :meth:`candidates` instead, which keeps the length of each assignment
        attached so specificity can be judged.
        """
        return [candidate.record for candidate in self.candidates(digits)]

    # -- candidate gathering ----------------------------------------------
    def candidates(self, digits: str, *, limit: int = 60) -> list[Candidate]:
        """Every allocation that legitimately contains *digits*.

        Three sources, each tagged with how it was found so the resolver can
        rank them:

        * an assignment of exactly this length and value — the strongest thing
          there is, and the only one that can be called an exact match;
        * a *shorter* assignment whose span contains the query — a six-digit
          root when an eight-digit value was typed. Real, but broader;
        * an allocated range containing the query, account ranges included.

        Longer assignments *under* a shorter query are deliberately excluded.
        Typing ``414720`` must not drag in ``41472012``: that is a different,
        more specific allocation which may well belong to somebody else, and
        including it would let a child record answer for its parent.
        """
        normalized = bin_normalizer.normalize(digits)
        length = len(normalized.bin)

        candidates: list[Candidate] = []
        with self.session() as session:
            rows: Sequence[Bin] = (
                self._select_full(
                    session,
                    and_(
                        # Contains the query: the stored span covers it and the
                        # assignment is no longer than what was asked for.
                        Bin.span_low <= normalized.range_low,
                        Bin.span_high >= normalized.range_low,
                        Bin.prefix_length <= length,
                    ),
                    limit=limit,
                )
                .unique()
                .scalars()
                .all()
            )
            ranges: Sequence[BinRange] = (
                session.execute(self._range_statement(normalized.range_low, limit))
                .unique()
                .scalars()
                .all()
            )

            # DTOs are built while the session is open; nothing ORM-bound
            # escapes this block.
            for row in rows:
                stored_length = row.prefix_length or len(row.bin)
                candidates.append(
                    Candidate(
                        record=self._to_record(row),
                        strategy=self._strategy_for(stored_length, length),
                        span=(row.span_high or 0) - (row.span_low or 0) + 1,
                        is_current=any(link.is_current for link in row.institution_links)
                        or not row.institution_links,
                    )
                )
            for row in ranges:
                candidates.append(
                    Candidate(
                        record=self._range_to_record(row, digits),
                        strategy=(
                            LookupStrategy.ACCOUNT_RANGE
                            if row.range_type == RangeType.ACCOUNT_RANGE.value
                            else LookupStrategy.BROADER_RANGE
                        ),
                        span=row.range_high_int - row.range_low_int + 1,
                        is_current=bool(row.is_current),
                    )
                )
        return candidates

    @staticmethod
    def _strategy_for(stored_length: int, query_length: int) -> LookupStrategy:
        """How a stored assignment relates to the query that found it."""
        if stored_length == query_length:
            if stored_length >= 8:
                return LookupStrategy.EXACT_8
            if stored_length == 6:
                return LookupStrategy.EXACT_6
            return LookupStrategy.EXACT_ASSIGNED
        # Shorter than the query: a root that contains it, never an exact match.
        return LookupStrategy.ROOT_PREFIX

    @staticmethod
    def _range_statement(value: int, limit: int) -> Select[Any]:
        return (
            select(BinRange)
            .options(
                joinedload(BinRange.network),
                joinedload(BinRange.country),
                joinedload(BinRange.institution).joinedload(Institution.country),
                joinedload(BinRange.institution)
                .selectinload(Institution.addresses)
                .joinedload(Address.country),
            )
            .where(
                BinRange.range_low_int <= value,
                BinRange.range_high_int >= value,
            )
            # Narrowest first: the most specific allocation is the one that
            # should answer, and the resolver confirms that ranking.
            .order_by(BinRange.span.asc(), BinRange.range_low_int.asc())
            .limit(limit)
        )

    def find_by_range(self, digits: str) -> list[BinRecord]:
        """Allocated ranges containing the query, materialised as BIN records."""
        normalized = bin_normalizer.normalize(digits)
        with self.session() as session:
            ranges: Sequence[BinRange] = (
                session.execute(self._range_statement(normalized.range_low, 10))
                .unique()
                .scalars()
                .all()
            )
            return [self._range_to_record(row, digits) for row in ranges]

    def children_of(self, digits: str, *, limit: int = 200) -> list[BinRecord]:
        """More specific assignments recorded *beneath* this prefix.

        A six-digit root may have eight-digit assignments under it, and those
        can belong to different issuers. They are never used to answer a
        six-digit query, but a caller can legitimately ask what exists so the
        interface can say "more specific assignments exist under this root".
        """
        normalized = bin_normalizer.normalize(digits)
        length = len(normalized.bin)
        with self.session() as session:
            rows: Sequence[Bin] = (
                self._select_full(
                    session,
                    and_(
                        Bin.span_low >= normalized.range_low,
                        Bin.span_high <= normalized.range_high,
                        Bin.prefix_length > length,
                    ),
                    limit=limit,
                )
                .unique()
                .scalars()
                .all()
            )
            return [self._to_record(row) for row in rows]

    def exists(self, digits: str) -> bool:
        with self.session() as session:
            return (
                session.execute(select(Bin.id).where(Bin.bin == digits).limit(1)).scalar()
                is not None
            )

    # -- institution-scoped listing ---------------------------------------
    def page_for_institution(
        self,
        institution_id: int,
        request: PageRequest,
        filters: BinFilters | None = None,
    ) -> Page[BinRow]:
        """One page of the BINs linked to an institution, with filters."""
        filters = filters or BinFilters()
        with self.session() as session:
            statement = self._row_statement(filters).where(
                Bin.id.in_(
                    select(BinInstitution.bin_id).where(
                        BinInstitution.institution_id == institution_id
                    )
                )
            )
            statement = self._apply_sort(statement, request)
            total = self.count_of(session, statement)
            rows = session.execute(
                statement.limit(request.page_size).offset(request.offset)
            ).all()
            return Page(
                items=[self._to_row(row) for row in rows],
                total=total,
                page=request.page,
                page_size=request.page_size,
            )

    def all_bins_for_institution(self, institution_id: int, limit: int = 100_000) -> list[BinRow]:
        """Every BIN for an institution — used by CSV/JSON export only."""
        with self.session() as session:
            statement = (
                self._row_statement(BinFilters())
                .where(
                    Bin.id.in_(
                        select(BinInstitution.bin_id).where(
                            BinInstitution.institution_id == institution_id
                        )
                    )
                )
                .order_by(Bin.bin.asc())
                .limit(limit)
            )
            return [self._to_row(row) for row in session.execute(statement).all()]

    def filter_options(self, institution_id: int | None = None) -> dict[str, list[tuple[str, str]]]:
        """Distinct filter values (code, label) for the table filter bar."""
        with self.session() as session:
            scope = None
            if institution_id is not None:
                scope = Bin.id.in_(
                    select(BinInstitution.bin_id).where(
                        BinInstitution.institution_id == institution_id
                    )
                )

            def distinct(column: Any, join_country: bool = False, join_network: bool = False):
                statement = select(column).distinct()
                if join_country:
                    statement = statement.select_from(Bin).join(
                        Country, Bin.country_id == Country.id
                    )
                elif join_network:
                    statement = statement.select_from(Bin).join(
                        Network, Bin.network_id == Network.id
                    )
                if scope is not None:
                    statement = statement.where(scope)
                return [value for value in session.execute(statement).scalars().all() if value]

            countries = select(Country.iso2, Country.name).select_from(Bin).join(
                Country, Bin.country_id == Country.id
            ).distinct()
            networks = select(Network.code, Network.display_name).select_from(Bin).join(
                Network, Bin.network_id == Network.id
            ).distinct()
            if scope is not None:
                countries = countries.where(scope)
                networks = networks.where(scope)

            regions = select(Address.region).distinct().where(Address.region.is_not(None))
            if institution_id is not None:
                regions = regions.where(Address.institution_id == institution_id)

            return {
                "country": sorted(
                    ((code, name) for code, name in session.execute(countries).all() if code),
                    key=lambda item: item[1],
                ),
                "network": sorted(
                    ((code, name) for code, name in session.execute(networks).all() if code),
                    key=lambda item: item[1],
                ),
                "card_type": sorted(
                    (value, value.replace("_", " ").title()) for value in distinct(Bin.card_type)
                ),
                "funding_type": sorted(
                    (value, value.replace("_", " ").title())
                    for value in distinct(Bin.funding_type)
                ),
                "region": sorted(
                    (value, value) for value in session.execute(regions).scalars().all() if value
                ),
            }

    def institution_bin_stats(self, institution_id: int) -> dict[str, Any]:
        """Aggregate counters for one institution, in four grouped queries."""
        scope = Bin.id.in_(
            select(BinInstitution.bin_id).where(BinInstitution.institution_id == institution_id)
        )
        with self.session() as session:
            total = int(
                session.execute(select(func.count()).select_from(Bin).where(scope)).scalar() or 0
            )
            by_network = {
                str(code): int(count)
                for code, count in session.execute(
                    select(Network.code, func.count(Bin.id))
                    .select_from(Bin)
                    .join(Network, Bin.network_id == Network.id)
                    .where(scope)
                    .group_by(Network.code)
                ).all()
            }
            by_card_type = {
                str(value): int(count)
                for value, count in session.execute(
                    select(Bin.card_type, func.count(Bin.id)).where(scope).group_by(Bin.card_type)
                ).all()
            }
            by_funding = {
                str(value): int(count)
                for value, count in session.execute(
                    select(Bin.funding_type, func.count(Bin.id))
                    .where(scope)
                    .group_by(Bin.funding_type)
                ).all()
            }
            prepaid = int(
                session.execute(
                    select(func.count()).select_from(Bin).where(scope, Bin.is_prepaid.is_(True))
                ).scalar()
                or 0
            )
            commercial = int(
                session.execute(
                    select(func.count()).select_from(Bin).where(scope, Bin.is_commercial.is_(True))
                ).scalar()
                or 0
            )
            countries = int(
                session.execute(
                    select(func.count(func.distinct(Bin.country_id))).where(scope)
                ).scalar()
                or 0
            )
        return {
            "total_bins": total,
            "by_network": by_network,
            "by_card_type": by_card_type,
            "by_funding_type": by_funding,
            "prepaid": prepaid,
            "commercial": commercial,
            "countries": countries,
        }

    # -- statement builders ------------------------------------------------
    def _select_full(self, session: Session, condition: Any, limit: int = 25) -> Any:
        statement = (
            select(Bin).options(*self._full_options()).where(condition).limit(limit)
        )
        return session.execute(statement)

    @staticmethod
    def _row_statement(filters: BinFilters) -> Select[Any]:
        """Flat projection for table rows — no ORM objects, no lazy loads."""
        primary_link = (
            select(BinInstitution.bin_id, func.min(BinInstitution.institution_id).label("inst_id"))
            .where(BinInstitution.is_primary.is_(True))
            .group_by(BinInstitution.bin_id)
            .subquery()
        )
        primary_address = (
            select(
                Address.institution_id.label("inst_id"),
                func.min(Address.id).label("address_id"),
            )
            .group_by(Address.institution_id)
            .subquery()
        )
        statement = (
            select(
                Bin.id,
                Bin.bin,
                Network.display_name,
                Bin.brand,
                Bin.card_type,
                Bin.funding_type,
                Country.name,
                Country.iso2,
                Address.region,
                Address.city,
                Address.postal_code,
                Institution.display_name,
                Bin.status,
            )
            .select_from(Bin)
            .outerjoin(Network, Bin.network_id == Network.id)
            .outerjoin(Country, Bin.country_id == Country.id)
            .outerjoin(primary_link, primary_link.c.bin_id == Bin.id)
            .outerjoin(Institution, Institution.id == primary_link.c.inst_id)
            .outerjoin(primary_address, primary_address.c.inst_id == Institution.id)
            .outerjoin(Address, Address.id == primary_address.c.address_id)
        )
        if filters.country_code:
            statement = statement.where(Country.iso2 == filters.country_code)
        if filters.network_code:
            statement = statement.where(Network.code == filters.network_code)
        if filters.card_type:
            statement = statement.where(Bin.card_type == filters.card_type)
        if filters.funding_type:
            statement = statement.where(Bin.funding_type == filters.funding_type)
        if filters.region:
            statement = statement.where(Address.region == filters.region)
        if filters.text:
            needle = f"%{filters.text.strip()}%"
            statement = statement.where(
                or_(
                    Bin.bin.like(f"{filters.text.strip()}%"),
                    Institution.display_name.like(needle),
                    Address.city.like(needle),
                )
            )
        return statement

    @staticmethod
    def _apply_sort(statement: Select[Any], request: PageRequest) -> Select[Any]:
        column = SORTABLE_COLUMNS.get(request.sort_by, Bin.bin)
        ordering = column.desc() if request.direction is SortDirection.DESC else column.asc()
        return statement.order_by(ordering, Bin.bin.asc())

    # -- mappers ----------------------------------------------------------
    @staticmethod
    def _country_dto(country: Country | None) -> CountryDTO | None:
        if country is None:
            return None
        return CountryDTO(
            id=country.id,
            iso2=country.iso2,
            iso3=country.iso3,
            name=country.name,
            currency_code=country.currency_code,
            flag_emoji=country.flag_emoji,
        )

    @staticmethod
    def _network_dto(network: Network | None) -> NetworkDTO | None:
        if network is None:
            return None
        return NetworkDTO(
            id=network.id,
            code=network.code,
            name=network.name,
            display_name=network.display_name,
            accent_color=network.accent_color,
        )

    @classmethod
    def _address_dto(cls, address: Address | None) -> AddressDTO | None:
        if address is None:
            return None
        return AddressDTO(
            line1=address.line1,
            line2=address.line2,
            city=address.city,
            region=address.region,
            region_code=address.region_code,
            postal_code=address.postal_code,
            country=cls._country_dto(address.country),
            is_primary=address.is_primary,
        )

    @staticmethod
    def _pick_address(institution: Institution | None) -> Address | None:
        if institution is None or not institution.addresses:
            return None
        for address in institution.addresses:
            if address.is_primary:
                return address
        return institution.addresses[0]

    @classmethod
    def _to_record(cls, row: Bin) -> BinRecord:
        links = sorted(
            row.institution_links,
            key=lambda link: (
                not link.is_current,
                not link.is_primary,
                link.relationship_type,
                link.institution_id,
            ),
        )
        institutions = tuple(
            InstitutionSummary(
                id=link.institution.id,
                uid=link.institution.uid,
                display_name=link.institution.display_name,
                legal_name=link.institution.legal_name,
                country=cls._country_dto(link.institution.country),
                relationship_type=link.relationship_type,
                is_primary=link.is_primary,
                website=link.institution.website,
                status=link.status,
                is_current=bool(link.is_current),
                effective_from=link.effective_from,
                effective_to=link.effective_to,
                confidence=link.confidence,
                confidence_level=link.confidence_level,
            )
            for link in links
            if link.institution is not None
        )
        # The address shown belongs to whichever institution the record is
        # actually attributed to, which is not necessarily the first link.
        primary_link = next(
            (
                link
                for link in links
                if link.institution is not None and link.is_current and link.is_primary
            ),
            links[0] if links else None,
        )
        primary = primary_link.institution if primary_link else None
        address = cls._pick_address(primary)
        country = cls._country_dto(row.country) or (
            cls._country_dto(primary.country) if primary else None
        )
        return BinRecord(
            id=row.id,
            bin=row.bin,
            iin=row.iin or row.bin,
            iin_length=row.iin_length,
            prefix_length=row.prefix_length or len(row.bin),
            prefix_type=row.prefix_type,
            bin_range=None,
            network=cls._network_dto(row.network),
            brand=row.brand,
            card_type=_label(row.card_type),
            funding_type=_label(row.funding_type),
            is_prepaid=row.is_prepaid,
            is_commercial=row.is_commercial,
            country=country,
            currency_code=row.currency_code or (country.currency_code if country else None),
            status=_label(row.status),
            first_seen=row.first_seen,
            last_updated=row.last_updated,
            institutions=institutions,
            address=cls._address_dto(address),
        )

    @classmethod
    def _range_to_record(cls, row: BinRange, query: str) -> BinRecord:
        institution = row.institution
        address = cls._pick_address(institution)
        country = cls._country_dto(row.country) or (
            cls._country_dto(institution.country) if institution else None
        )
        institutions = (
            (
                InstitutionSummary(
                    id=institution.id,
                    uid=institution.uid,
                    display_name=institution.display_name,
                    legal_name=institution.legal_name,
                    country=cls._country_dto(institution.country),
                    relationship_type=RelationshipType.ISSUER.value,
                    is_primary=True,
                    website=institution.website,
                    status=row.status,
                    is_current=bool(row.is_current),
                    effective_from=row.effective_from,
                    effective_to=row.effective_to,
                    confidence=row.confidence,
                ),
            )
            if institution is not None
            else ()
        )
        return BinRecord(
            id=-row.id,  # negative ids mark a range-derived, synthetic record
            bin=query,
            iin=query,
            iin_length=len(row.range_low),
            prefix_length=len(query),
            prefix_type=PrefixType.RANGE.value,
            bin_range=bin_normalizer.format_range(row.range_low, row.range_high),
            network=cls._network_dto(row.network),
            brand=row.brand,
            card_type=_label(row.card_type),
            funding_type=_label(row.funding_type),
            is_prepaid=row.is_prepaid,
            is_commercial=row.is_commercial,
            country=country,
            currency_code=row.currency_code or (country.currency_code if country else None),
            status=_label(row.status),
            first_seen=row.first_seen,
            last_updated=row.last_updated,
            institutions=institutions,
            address=cls._address_dto(address),
        )

    @staticmethod
    def _to_row(row: Any) -> BinRow:
        return BinRow(
            id=row[0],
            bin=row[1],
            network=row[2],
            brand=row[3],
            card_type=_label(row[4]),
            funding_type=_label(row[5]),
            country=row[6],
            country_code=row[7],
            region=row[8],
            city=row[9],
            postal_code=row[10],
            institution=row[11],
            status=_label(row[12]),
        )


def _label(value: str | None) -> str | None:
    """``deferred_debit`` → ``Deferred Debit``; ``unknown`` → ``None``."""
    if not value or value == "unknown":
        return None
    return value.replace("_", " ").title()
