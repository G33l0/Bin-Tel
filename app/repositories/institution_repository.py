"""Institution (bank) search and detail queries.

Search covers the display name, the normalized name, the legal name and every
recorded alias in a single indexed query, then ranks the candidates by how the
match was made — exact normalized hit, alias hit, prefix, then substring.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import case, func, or_, select
from sqlalchemy.orm import joinedload, selectinload

from app.models.entities import (
    Address,
    BinInstitution,
    Country,
    Institution,
    InstitutionAlias,
)
from app.models.schemas import (
    AddressDTO,
    CountryDTO,
    InstitutionDetail,
    Page,
    PageRequest,
)
from app.normalizers.name_normalizer import name_normalizer
from app.repositories.base import BaseRepository
from app.repositories.bin_repository import BinRepository

#: Hard ceiling on a name search, so a one-letter query cannot drag the UI down.
SEARCH_LIMIT = 100


class InstitutionRepository(BaseRepository):
    """Read access to ``institutions`` and their aliases and addresses."""

    @staticmethod
    def _options() -> tuple[Any, ...]:
        return (
            joinedload(Institution.country),
            joinedload(Institution.parent),
            selectinload(Institution.aliases),
            selectinload(Institution.addresses).joinedload(Address.country),
        )

    # -- search -----------------------------------------------------------
    def search(self, term: str, *, limit: int = 25, country_code: str | None = None) -> list[InstitutionDetail]:
        """Rank institutions matching *term* across names and aliases."""
        normalized = name_normalizer.normalize(term)
        needle = normalized.normalized
        core = normalized.core
        if not needle:
            return []

        prefix = f"{needle}%"
        contains = f"%{needle}%"
        core_prefix = f"{core}%"

        alias_hits = (
            select(InstitutionAlias.institution_id)
            .where(
                or_(
                    InstitutionAlias.normalized_alias == needle,
                    InstitutionAlias.normalized_alias.like(prefix),
                    InstitutionAlias.normalized_alias.like(contains),
                )
            )
            .scalar_subquery()
        )

        rank = case(
            (Institution.normalized_name == needle, 0),
            (Institution.normalized_legal_name == needle, 1),
            (Institution.id.in_(alias_hits), 2),
            (Institution.normalized_name.like(prefix), 3),
            (Institution.normalized_name.like(core_prefix), 4),
            (Institution.normalized_name.like(contains), 5),
            else_=6,
        ).label("rank")

        statement = (
            select(Institution, rank)
            .options(*self._options())
            .where(
                or_(
                    Institution.normalized_name == needle,
                    Institution.normalized_legal_name == needle,
                    Institution.normalized_name.like(contains),
                    Institution.normalized_legal_name.like(contains),
                    Institution.display_name.like(contains),
                    Institution.id.in_(alias_hits),
                )
            )
        )
        if country_code:
            statement = statement.join(Country, Institution.country_id == Country.id).where(
                Country.iso2 == country_code
            )
        statement = statement.order_by(rank.asc(), Institution.display_name.asc()).limit(
            min(limit, SEARCH_LIMIT)
        )

        with self.session() as session:
            rows = session.execute(statement).unique().all()
            institutions = [row[0] for row in rows]
            counts = self._bin_counts(session, [item.id for item in institutions])
            return [
                self._to_detail(item, counts.get(item.id, 0)) for item in institutions
            ]

    def get(self, institution_id: int) -> InstitutionDetail | None:
        with self.session() as session:
            institution = session.execute(
                select(Institution).options(*self._options()).where(Institution.id == institution_id)
            ).unique().scalar_one_or_none()
            if institution is None:
                return None
            counts = self._bin_counts(session, [institution_id])
            return self._to_detail(institution, counts.get(institution_id, 0))

    def page(self, request: PageRequest, *, country_code: str | None = None) -> Page[InstitutionDetail]:
        """Alphabetical browsing of institutions."""
        statement = select(Institution).options(*self._options())
        if country_code:
            statement = statement.join(Country, Institution.country_id == Country.id).where(
                Country.iso2 == country_code
            )
        statement = statement.order_by(Institution.display_name.asc())
        with self.session() as session:
            total = self.count_of(session, statement)
            rows = (
                session.execute(statement.limit(request.page_size).offset(request.offset))
                .unique()
                .scalars()
                .all()
            )
            counts = self._bin_counts(session, [row.id for row in rows])
            return Page(
                items=[self._to_detail(row, counts.get(row.id, 0)) for row in rows],
                total=total,
                page=request.page,
                page_size=request.page_size,
            )

    def find_by_normalized_name(self, normalized: str) -> list[int]:
        """Candidate ids for the deduplication service."""
        with self.session() as session:
            return list(
                session.execute(
                    select(Institution.id).where(Institution.normalized_name == normalized)
                )
                .scalars()
                .all()
            )

    # -- helpers ----------------------------------------------------------
    @staticmethod
    def _bin_counts(session: Any, institution_ids: list[int]) -> dict[int, int]:
        """One grouped query for every institution on the page — never N+1."""
        if not institution_ids:
            return {}
        rows = session.execute(
            select(BinInstitution.institution_id, func.count(BinInstitution.bin_id))
            .where(BinInstitution.institution_id.in_(institution_ids))
            .group_by(BinInstitution.institution_id)
        ).all()
        return {int(institution_id): int(count) for institution_id, count in rows}

    @classmethod
    def _to_detail(cls, institution: Institution, bin_count: int) -> InstitutionDetail:
        address = BinRepository._pick_address(institution)
        return InstitutionDetail(
            id=institution.id,
            display_name=institution.display_name,
            legal_name=institution.legal_name,
            short_name=institution.short_name,
            institution_type=(institution.institution_type or "").replace("_", " ").title() or None,
            status=(institution.status or "").title() or None,
            website=institution.website,
            country=_country_dto(institution.country),
            parent_name=institution.parent.display_name if institution.parent else None,
            aliases=tuple(
                sorted({alias.alias for alias in institution.aliases if alias.alias})
            ),
            address=_address_dto(address),
            bin_count=bin_count,
        )


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


def _address_dto(address: Address | None) -> AddressDTO | None:
    if address is None:
        return None
    return AddressDTO(
        line1=address.line1,
        line2=address.line2,
        city=address.city,
        region=address.region,
        region_code=address.region_code,
        postal_code=address.postal_code,
        country=_country_dto(address.country),
        is_primary=address.is_primary,
    )
