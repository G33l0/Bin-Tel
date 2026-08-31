"""Aggregate counters for the Dashboard and Database pages."""

from __future__ import annotations

from sqlalchemy import func, select

from app.models.entities import (
    Address,
    Bin,
    BinRange,
    Conflict,
    ConflictStatus,
    Country,
    Institution,
    InstitutionAlias,
    Network,
)
from app.models.schemas import DatabaseStats
from app.repositories.base import BaseRepository


class StatsRepository(BaseRepository):
    """Cheap COUNT queries; every one is served by a primary-key index."""

    def stats(self) -> DatabaseStats:
        with self.session() as session:

            def count(entity: type) -> int:
                return int(session.execute(select(func.count()).select_from(entity)).scalar() or 0)

            open_conflicts = int(
                session.execute(
                    select(func.count())
                    .select_from(Conflict)
                    .where(Conflict.status == ConflictStatus.OPEN.value)
                ).scalar()
                or 0
            )
            # "Countries" and "networks" mean coverage — what BIN records
            # actually point at — not the size of the reference tables.
            covered_countries = int(
                session.execute(
                    select(func.count(func.distinct(Bin.country_id))).where(
                        Bin.country_id.is_not(None)
                    )
                ).scalar()
                or 0
            )
            covered_networks = int(
                session.execute(
                    select(func.count(func.distinct(Bin.network_id))).where(
                        Bin.network_id.is_not(None)
                    )
                ).scalar()
                or 0
            )
            return DatabaseStats(
                bins=count(Bin),
                institutions=count(Institution),
                countries=covered_countries,
                networks=covered_networks,
                bin_ranges=count(BinRange),
                aliases=count(InstitutionAlias),
                addresses=count(Address),
                conflicts_open=open_conflicts,
            )

    def top_countries(self, limit: int = 8) -> list[tuple[str, int]]:
        with self.session() as session:
            rows = session.execute(
                select(Country.name, func.count(Bin.id))
                .select_from(Bin)
                .join(Country, Bin.country_id == Country.id)
                .group_by(Country.name)
                .order_by(func.count(Bin.id).desc())
                .limit(limit)
            ).all()
        return [(str(name), int(count)) for name, count in rows]

    def top_networks(self, limit: int = 8) -> list[tuple[str, int]]:
        with self.session() as session:
            rows = session.execute(
                select(Network.display_name, func.count(Bin.id))
                .select_from(Bin)
                .join(Network, Bin.network_id == Network.id)
                .group_by(Network.display_name)
                .order_by(func.count(Bin.id).desc())
                .limit(limit)
            ).all()
        return [(str(name), int(count)) for name, count in rows]

    def card_type_breakdown(self) -> dict[str, int]:
        with self.session() as session:
            rows = session.execute(
                select(Bin.card_type, func.count(Bin.id)).group_by(Bin.card_type)
            ).all()
        return {str(value or "unknown"): int(count) for value, count in rows}
