"""Advanced, multi-criteria search.

One statement builder serves the whole advanced-search surface. Every
criterion is pushed down to SQLite and served from an index, and results are
always paginated — the application never materialises a large result set to
filter it in Python.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import Select, and_, func, or_, select

from app.models.entities import (
    Address,
    Bin,
    BinInstitution,
    Country,
    Institution,
    InstitutionAlias,
    Network,
)
from app.models.schemas import (
    AdvancedQuery,
    BinRow,
    MatchMode,
    Page,
    PageRequest,
    SortDirection,
)
from app.normalizers.bin_normalizer import bin_normalizer
from app.normalizers.confidence import string_similarity
from app.normalizers.name_normalizer import name_normalizer
from app.repositories.base import BaseRepository
from app.repositories.bin_repository import BinRepository
from app.utils.validators import clean_digits

#: Columns the advanced result table may sort by.
SORTABLE: dict[str, Any] = {
    "bin": Bin.bin,
    "network": Network.display_name,
    "card_type": Bin.card_type,
    "funding_type": Bin.funding_type,
    "country": Country.name,
    "institution": Institution.display_name,
    "status": Bin.status,
    "updated": Bin.last_updated,
}

#: Fuzzy matching is deliberately narrow: a candidate must share a leading
#: token with the query before similarity is even considered, which stops an
#: unrelated institution surfacing because a few letters happen to line up.
FUZZY_MIN_SCORE = 0.82
FUZZY_CANDIDATE_LIMIT = 400
#: How many leading characters of the first token form the blocking key.
FUZZY_BLOCK_LENGTH = 4


class SearchRepository(BaseRepository):
    """Executes :class:`AdvancedQuery` against the intelligence database."""

    def search(self, query: AdvancedQuery, request: PageRequest) -> Page[BinRow]:
        with self.session() as session:
            statement = self._build(query, session)
            statement = self._sort(statement, request)
            total = self.count_of(session, statement)
            rows = session.execute(
                statement.limit(request.page_size).offset(request.offset)
            ).all()
            return Page(
                items=[BinRepository._to_row(row) for row in rows],
                total=total,
                page=request.page,
                page_size=request.page_size,
            )

    def count(self, query: AdvancedQuery) -> int:
        with self.session() as session:
            return self.count_of(session, self._build(query, session))

    def all_rows(self, query: AdvancedQuery, limit: int = 250_000) -> list[BinRow]:
        """Every matching row — export and report paths only."""
        with self.session() as session:
            statement = self._build(query, session).order_by(Bin.bin.asc()).limit(limit)
            return [BinRepository._to_row(row) for row in session.execute(statement).all()]

    @classmethod
    def scope_condition(cls, query: AdvancedQuery) -> Any | None:
        """A standalone ``WHERE`` for ``bins`` matching *query*.

        Analytics reuses this so a filtered chart and a filtered result table
        can never disagree about what the filter means. Criteria that need a
        join (institution name, geography) are expressed as subqueries.
        """
        if query is None or query.is_empty:
            return None
        conditions = list(cls._bin_conditions(query))
        conditions.extend(cls._attribute_scope(query))
        geography = cls._geography_scope(query)
        if geography is not None:
            conditions.append(geography)
        institution = cls._institution_scope(query)
        if institution is not None:
            conditions.append(institution)
        return and_(*conditions) if conditions else None

    @staticmethod
    def _attribute_scope(query: AdvancedQuery):
        """Attribute criteria expressed without joining the network table."""
        if query.network_code:
            yield Bin.network_id.in_(
                select(Network.id).where(Network.code == query.network_code)
            )
        if query.card_type:
            yield Bin.card_type == query.card_type
        if query.funding_type:
            yield Bin.funding_type == query.funding_type
        if query.brand:
            yield Bin.brand.like(f"%{query.brand.strip()}%")
        if query.currency:
            yield Bin.currency_code == query.currency.upper()
        if query.status:
            yield Bin.status == query.status
        if query.prepaid is not None:
            yield Bin.is_prepaid.is_(query.prepaid)
        if query.commercial is not None:
            yield Bin.is_commercial.is_(query.commercial)
        if query.updated_after:
            yield Bin.last_updated >= query.updated_after
        if query.updated_before:
            yield Bin.last_updated <= query.updated_before
        if query.country_code:
            yield Bin.country_id.in_(
                select(Country.id).where(Country.iso2 == query.country_code.upper())
            )

    @staticmethod
    def _geography_scope(query: AdvancedQuery) -> Any | None:
        clauses = []
        if query.region:
            needle = query.region.strip()
            clauses.append(
                or_(Address.region.like(f"%{needle}%"), Address.region_code == needle.upper())
            )
        if query.city:
            from app.normalizers.geo_normalizer import geo_normalizer

            clauses.append(
                or_(
                    Address.normalized_city == geo_normalizer.normalized_city(query.city),
                    Address.city.like(f"%{query.city.strip()}%"),
                )
            )
        if query.postal_code:
            from app.normalizers.geo_normalizer import geo_normalizer

            clauses.append(
                or_(
                    Address.normalized_postal_code
                    == geo_normalizer.normalized_postal_code(query.postal_code),
                    Address.postal_code.like(f"{query.postal_code.strip()}%"),
                )
            )
        if not clauses:
            return None
        institutions = select(Address.institution_id).where(and_(*clauses))
        return Bin.id.in_(
            select(BinInstitution.bin_id).where(BinInstitution.institution_id.in_(institutions))
        )

    @staticmethod
    def _institution_scope(query: AdvancedQuery) -> Any | None:
        term = (query.institution or "").strip()
        if not term:
            return None
        needle = name_normalizer.normalized_form(term)
        if not needle:
            return None
        matching = select(Institution.id).where(
            or_(
                Institution.normalized_name.like(f"%{needle}%"),
                Institution.normalized_legal_name.like(f"%{needle}%"),
            )
        ).union(
            select(InstitutionAlias.institution_id).where(
                InstitutionAlias.normalized_alias.like(f"%{needle}%")
            )
        )
        return Bin.id.in_(
            select(BinInstitution.bin_id).where(BinInstitution.institution_id.in_(matching))
        )

    # -- statement construction -------------------------------------------
    def _build(self, query: AdvancedQuery, session: Any) -> Select[Any]:
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

        conditions = list(self._bin_conditions(query))
        conditions.extend(self._attribute_conditions(query))
        conditions.extend(self._geography_conditions(query))

        institution_condition = self._institution_condition(query, session)
        if institution_condition is not None:
            conditions.append(institution_condition)

        return statement.where(and_(*conditions)) if conditions else statement

    @staticmethod
    def _prefix_bounds(digits: str) -> tuple[int, int]:
        """Numeric bounds a BIN prefix of any length covers.

        Padded to the normalizer's range width so a single leading digit means
        "every BIN starting with it", not a four-digit BIN.
        """
        width = bin_normalizer.width
        trimmed = digits[:width]
        return int(trimmed.ljust(width, "0")), int(trimmed.ljust(width, "9"))

    @classmethod
    def _bin_conditions(cls, query: AdvancedQuery):
        if query.bin_prefix:
            digits = clean_digits(query.bin_prefix)
            if digits:
                low, high = cls._prefix_bounds(digits)
                yield and_(Bin.bin_int >= low, Bin.bin_int <= high)
        if query.bin_from or query.bin_to:
            low_digits = clean_digits(query.bin_from or "")
            high_digits = clean_digits(query.bin_to or "")
            if low_digits:
                yield Bin.bin_int >= cls._prefix_bounds(low_digits)[0]
            if high_digits:
                yield Bin.bin_int <= cls._prefix_bounds(high_digits)[1]

    @staticmethod
    def _attribute_conditions(query: AdvancedQuery):
        if query.network_code:
            yield Network.code == query.network_code
        if query.card_type:
            yield Bin.card_type == query.card_type
        if query.funding_type:
            yield Bin.funding_type == query.funding_type
        if query.brand:
            yield Bin.brand.like(f"%{query.brand.strip()}%")
        if query.currency:
            yield Bin.currency_code == query.currency.upper()
        if query.status:
            yield Bin.status == query.status
        if query.prepaid is not None:
            yield Bin.is_prepaid.is_(query.prepaid)
        if query.commercial is not None:
            yield Bin.is_commercial.is_(query.commercial)
        if query.updated_after:
            yield Bin.last_updated >= query.updated_after
        if query.updated_before:
            yield Bin.last_updated <= query.updated_before

    @staticmethod
    def _geography_conditions(query: AdvancedQuery):
        if query.country_code:
            yield Country.iso2 == query.country_code.upper()
        if query.region:
            needle = query.region.strip()
            yield or_(Address.region.like(f"%{needle}%"), Address.region_code == needle.upper())
        if query.city:
            from app.normalizers.geo_normalizer import geo_normalizer

            normalized = geo_normalizer.normalized_city(query.city)
            yield or_(
                Address.normalized_city == normalized,
                Address.city.like(f"%{query.city.strip()}%"),
            )
        if query.postal_code:
            from app.normalizers.geo_normalizer import geo_normalizer

            normalized = geo_normalizer.normalized_postal_code(query.postal_code)
            yield or_(
                Address.normalized_postal_code == normalized,
                Address.postal_code.like(f"{query.postal_code.strip()}%"),
            )

    def _institution_condition(self, query: AdvancedQuery, session: Any) -> Any | None:
        """Resolve the institution criterion to a set of ids.

        Doing this as a subquery keeps the main statement index-friendly, and
        it is the only way fuzzy matching can be applied without scanning every
        BIN row.
        """
        term = (query.institution or "").strip()
        if not term:
            return None

        normalized = name_normalizer.normalize(term)
        needle = normalized.normalized
        if not needle:
            return None

        if query.institution_match is MatchMode.FUZZY:
            ids = self._fuzzy_institution_ids(session, normalized)
            if not ids:
                return Bin.id.is_(None)  # match nothing, cleanly
            return Bin.id.in_(
                select(BinInstitution.bin_id).where(BinInstitution.institution_id.in_(ids))
            )

        if query.institution_match is MatchMode.EXACT:
            name_condition = or_(
                Institution.normalized_name == needle,
                Institution.normalized_legal_name == needle,
            )
            alias_condition = InstitutionAlias.normalized_alias == needle
        elif query.institution_match is MatchMode.PREFIX:
            name_condition = or_(
                Institution.normalized_name.like(f"{needle}%"),
                Institution.normalized_legal_name.like(f"{needle}%"),
            )
            alias_condition = InstitutionAlias.normalized_alias.like(f"{needle}%")
        else:
            name_condition = or_(
                Institution.normalized_name.like(f"%{needle}%"),
                Institution.normalized_legal_name.like(f"%{needle}%"),
                Institution.display_name.like(f"%{term}%"),
            )
            alias_condition = InstitutionAlias.normalized_alias.like(f"%{needle}%")

        matching = select(Institution.id).where(name_condition).union(
            select(InstitutionAlias.institution_id).where(alias_condition)
        )
        return Bin.id.in_(
            select(BinInstitution.bin_id).where(
                BinInstitution.institution_id.in_(matching)
            )
        )

    @staticmethod
    def _fuzzy_institution_ids(session: Any, normalized: Any) -> list[int]:
        """Candidate ids for a fuzzy name match, scored and thresholded.

        Blocked on the leading token so the candidate set stays small and
        related; similarity alone never promotes an unrelated institution.
        """
        tokens = list(normalized.core_tokens) or normalized.normalized.split()
        if not tokens:
            return []
        # Block on the first few characters of the leading token rather than
        # the whole token, so a typo ("Meridien" for "Meridian") still reaches
        # its candidate. The similarity threshold below does the real work.
        lead = tokens[0][:FUZZY_BLOCK_LENGTH]
        if len(lead) < 3:
            return []
        candidates = session.execute(
            select(Institution.id, Institution.display_name)
            .where(
                or_(
                    Institution.normalized_name.like(f"{lead}%"),
                    Institution.normalized_name.like(f"% {lead}%"),
                )
            )
            .limit(FUZZY_CANDIDATE_LIMIT)
        ).all()
        # Search-fuzziness is about typos in what was typed, which is a
        # different question from "are these two records the same institution"
        # — that one belongs to deduplication and is deliberately stricter.
        # Comparing the canonical forms directly is the right measure here.
        matches: list[tuple[float, int]] = []
        for identifier, display_name in candidates:
            candidate = name_normalizer.normalize(display_name)
            score = max(
                string_similarity(normalized.core, candidate.core),
                string_similarity(normalized.normalized, candidate.normalized),
            )
            if score >= FUZZY_MIN_SCORE:
                matches.append((score, int(identifier)))
        matches.sort(reverse=True)
        return [identifier for _, identifier in matches]

    @staticmethod
    def _sort(statement: Select[Any], request: PageRequest) -> Select[Any]:
        column = SORTABLE.get(request.sort_by, Bin.bin)
        ordering = column.desc() if request.direction is SortDirection.DESC else column.asc()
        return statement.order_by(ordering, Bin.bin.asc())

    # -- suggestions -------------------------------------------------------
    def suggest(self, term: str, limit: int = 8) -> list[tuple[str, str, str]]:
        """Type-ahead suggestions as ``(kind, value, label)`` triples."""
        term = (term or "").strip()
        if len(term) < 2:
            return []
        suggestions: list[tuple[str, str, str]] = []
        digits = clean_digits(term)

        with self.session() as session:
            if digits and len(digits) >= 3:
                rows = session.execute(
                    select(Bin.bin, Institution.display_name)
                    .select_from(Bin)
                    .outerjoin(BinInstitution, and_(
                        BinInstitution.bin_id == Bin.id,
                        BinInstitution.is_primary.is_(True),
                    ))
                    .outerjoin(Institution, Institution.id == BinInstitution.institution_id)
                    .where(Bin.bin.like(f"{digits}%"))
                    .order_by(Bin.bin)
                    .limit(limit)
                ).all()
                suggestions.extend(
                    ("bin", str(value), f"{value} · {name or 'Unknown issuer'}")
                    for value, name in rows
                )

            if not digits:
                needle = name_normalizer.normalized_form(term)
                rows = session.execute(
                    select(Institution.uid, Institution.display_name, Country.iso2)
                    .outerjoin(Country, Institution.country_id == Country.id)
                    .where(Institution.normalized_name.like(f"%{needle}%"))
                    .order_by(Institution.display_name)
                    .limit(limit)
                ).all()
                suggestions.extend(
                    ("institution", str(uid), f"{name}{f' · {iso}' if iso else ''}")
                    for uid, name, iso in rows
                )
        return suggestions[:limit]

    def filter_values(self) -> dict[str, list[tuple[str, str]]]:
        """Distinct values for the advanced-search selectors."""
        with self.session() as session:
            countries = session.execute(
                select(Country.iso2, Country.name)
                .select_from(Bin)
                .join(Country, Bin.country_id == Country.id)
                .distinct()
                .order_by(Country.name)
            ).all()
            networks = session.execute(
                select(Network.code, Network.display_name)
                .select_from(Bin)
                .join(Network, Bin.network_id == Network.id)
                .distinct()
                .order_by(Network.display_name)
            ).all()
            card_types = session.execute(
                select(Bin.card_type).distinct().order_by(Bin.card_type)
            ).scalars().all()
            funding = session.execute(
                select(Bin.funding_type).distinct().order_by(Bin.funding_type)
            ).scalars().all()
            statuses = session.execute(
                select(Bin.status).distinct().order_by(Bin.status)
            ).scalars().all()
            currencies = session.execute(
                select(Bin.currency_code)
                .where(Bin.currency_code.is_not(None))
                .distinct()
                .order_by(Bin.currency_code)
            ).scalars().all()
            regions = session.execute(
                select(Address.region)
                .where(Address.region.is_not(None))
                .distinct()
                .order_by(Address.region)
                .limit(300)
            ).scalars().all()

        def labelled(values) -> list[tuple[str, str]]:
            return [
                (str(value), str(value).replace("_", " ").title())
                for value in values
                if value and value != "unknown"
            ]

        return {
            "country": [(str(code), str(name)) for code, name in countries if code],
            "network": [(str(code), str(name)) for code, name in networks if code],
            "card_type": labelled(card_types),
            "funding_type": labelled(funding),
            "status": labelled(statuses),
            "currency": [(str(value), str(value)) for value in currencies if value],
            "region": [(str(value), str(value)) for value in regions if value],
        }
