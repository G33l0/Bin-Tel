"""Analytics over the local intelligence database.

Every number is computed from the database in front of the user — nothing is
estimated, extrapolated or invented. Where a package ships precomputed
aggregates in ``database_statistics`` those are used; where it does not, the
aggregate is computed with a grouped query, so an older package still yields
correct analytics rather than an empty page.

Results are cached against the installed database version and invalidated the
moment that version changes.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import Select, and_, func, select

from app.core.logging_config import get_logger
from app.database.engine import DatabaseManager
from app.models.entities import (
    Address,
    Bin,
    BinInstitution,
    Country,
    DatabaseStatistic,
    DatabaseVersion,
    Institution,
    Network,
)
from app.models.schemas import AdvancedQuery

logger = get_logger(__name__)

#: How many bars a distribution chart shows before the rest is grouped.
TOP_N = 10


@dataclass(frozen=True, slots=True)
class Slice:
    """One category in a distribution."""

    key: str
    label: str
    value: int

    def share(self, total: int) -> float:
        return self.value / total if total else 0.0


@dataclass(slots=True)
class Distribution:
    """A named breakdown, ready to chart or tabulate."""

    name: str
    title: str
    slices: list[Slice] = field(default_factory=list)
    total: int = 0
    unit: str = "BINs"

    @property
    def is_empty(self) -> bool:
        return not self.slices or self.total == 0

    @property
    def largest(self) -> Slice | None:
        return max(self.slices, key=lambda item: item.value) if self.slices else None

    def top(self, count: int = TOP_N) -> list[Slice]:
        """The largest *count* categories, with the remainder grouped."""
        ordered = sorted(self.slices, key=lambda item: item.value, reverse=True)
        if len(ordered) <= count:
            return ordered
        head = ordered[:count]
        rest = sum(item.value for item in ordered[count:])
        if rest:
            head.append(Slice(key="__other__", label=f"Other ({len(ordered) - count})", value=rest))
        return head

    def share_of(self, key: str) -> float:
        for item in self.slices:
            if item.key == key:
                return item.share(self.total)
        return 0.0


@dataclass(slots=True)
class GrowthPoint:
    """One period in the database-growth series."""

    period: str
    added: int
    cumulative: int


@dataclass(slots=True)
class AnalyticsSnapshot:
    """Everything the Analytics page shows, computed in one pass."""

    database_version: str | None = None
    computed_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    total_bins: int = 0
    total_institutions: int = 0
    total_countries: int = 0
    total_networks: int = 0
    credit_bins: int = 0
    debit_bins: int = 0
    prepaid_bins: int = 0
    commercial_bins: int = 0
    charge_bins: int = 0
    ranges: int = 0
    distributions: dict[str, Distribution] = field(default_factory=dict)
    growth: list[GrowthPoint] = field(default_factory=list)
    recently_added: int = 0
    recently_changed: int = 0
    top_institutions: list[tuple[str, int]] = field(default_factory=list)
    releases: list[tuple[str, datetime | None, int]] = field(default_factory=list)
    elapsed_ms: float = 0.0

    def distribution(self, name: str) -> Distribution:
        return self.distributions.get(name, Distribution(name=name, title=name.title()))

    @property
    def headline(self) -> list[tuple[str, int]]:
        return [
            ("Total BINs", self.total_bins),
            ("Total Institutions", self.total_institutions),
            ("Total Countries", self.total_countries),
            ("Total Networks", self.total_networks),
            ("Credit BINs", self.credit_bins),
            ("Debit BINs", self.debit_bins),
            ("Prepaid BINs", self.prepaid_bins),
            ("Commercial BINs", self.commercial_bins),
        ]


class AnalyticsService:
    """Computes distributions, growth and institution analytics."""

    def __init__(self, manager: DatabaseManager) -> None:
        self._manager = manager
        self._cache: dict[str, tuple[str | None, AnalyticsSnapshot]] = {}

    # -- cache ------------------------------------------------------------
    def invalidate(self) -> None:
        """Drop cached analytics — called whenever the database changes."""
        self._cache.clear()

    def _cached(self, key: str, version: str | None) -> AnalyticsSnapshot | None:
        entry = self._cache.get(key)
        if entry is None:
            return None
        cached_version, snapshot = entry
        return snapshot if cached_version == version else None

    # -- main entry point --------------------------------------------------
    def snapshot(
        self,
        *,
        version: str | None = None,
        query: AdvancedQuery | None = None,
        since: datetime | None = None,
    ) -> AnalyticsSnapshot:
        """Compute the analytics picture, optionally scoped to a filter."""
        started = time.perf_counter()
        cache_key = "all" if query is None or query.is_empty else query.describe()
        if since is None and (cached := self._cached(cache_key, version)) is not None:
            return cached

        snapshot = AnalyticsSnapshot(database_version=version)
        if not self._manager.is_open:
            return snapshot

        scope = self._scope(query)
        with self._manager.session() as session:
            snapshot.total_bins = self._count(session, Bin, scope)
            snapshot.total_institutions = self._count(session, Institution, None)
            snapshot.total_countries = int(
                session.execute(
                    self._apply(
                        select(func.count(func.distinct(Bin.country_id))).where(
                            Bin.country_id.is_not(None)
                        ),
                        scope,
                    )
                ).scalar()
                or 0
            )
            snapshot.total_networks = int(
                session.execute(
                    self._apply(
                        select(func.count(func.distinct(Bin.network_id))).where(
                            Bin.network_id.is_not(None)
                        ),
                        scope,
                    )
                ).scalar()
                or 0
            )
            snapshot.ranges = self._count_ranges(session)

            card_types = self._group(
                session, Bin.card_type, scope, label=lambda value: str(value).replace("_", " ").title()
            )
            snapshot.distributions["card_type"] = Distribution(
                name="card_type",
                title="BINs by card type",
                slices=card_types,
                total=sum(item.value for item in card_types),
            )
            counts = {item.key: item.value for item in card_types}
            snapshot.credit_bins = counts.get("credit", 0)
            snapshot.debit_bins = counts.get("debit", 0)
            snapshot.charge_bins = counts.get("charge", 0)

            funding = self._group(
                session, Bin.funding_type, scope, label=lambda value: str(value).title()
            )
            snapshot.distributions["funding_type"] = Distribution(
                name="funding_type",
                title="BINs by funding type",
                slices=funding,
                total=sum(item.value for item in funding),
            )

            snapshot.prepaid_bins = int(
                session.execute(
                    self._apply(
                        select(func.count()).select_from(Bin).where(Bin.is_prepaid.is_(True)), scope
                    )
                ).scalar()
                or 0
            )
            snapshot.commercial_bins = int(
                session.execute(
                    self._apply(
                        select(func.count()).select_from(Bin).where(Bin.is_commercial.is_(True)),
                        scope,
                    )
                ).scalar()
                or 0
            )

            snapshot.distributions["country"] = self._country_distribution(session, scope)
            snapshot.distributions["network"] = self._network_distribution(session, scope)
            snapshot.distributions["region"] = self._region_distribution(session, scope)
            snapshot.distributions["status"] = Distribution(
                name="status",
                title="BINs by status",
                slices=self._group(
                    session, Bin.status, scope, label=lambda value: str(value).title()
                ),
                total=snapshot.total_bins,
            )

            snapshot.top_institutions = self._top_institutions(session)
            snapshot.growth = self._growth(session, scope)
            window = since or (datetime.now(UTC) - timedelta(days=90))
            snapshot.recently_added = int(
                session.execute(
                    self._apply(
                        select(func.count()).select_from(Bin).where(Bin.first_seen >= window), scope
                    )
                ).scalar()
                or 0
            )
            snapshot.recently_changed = int(
                session.execute(
                    self._apply(
                        select(func.count())
                        .select_from(Bin)
                        .where(and_(Bin.last_updated >= window, Bin.last_updated > Bin.first_seen)),
                        scope,
                    )
                ).scalar()
                or 0
            )
            snapshot.releases = self._releases(session)

        snapshot.elapsed_ms = (time.perf_counter() - started) * 1000
        if since is None:
            self._cache[cache_key] = (version, snapshot)
        logger.debug(
            "Analytics computed",
            extra={
                "context": {
                    "bins": snapshot.total_bins,
                    "elapsed_ms": round(snapshot.elapsed_ms, 1),
                    "scoped": cache_key != "all",
                }
            },
        )
        return snapshot

    # -- institution analytics --------------------------------------------
    def institution_analytics(self, institution_id: int) -> dict[str, Distribution]:
        """Per-institution breakdowns, computed from its own BIN set."""
        if not self._manager.is_open:
            return {}
        scope = Bin.id.in_(
            select(BinInstitution.bin_id).where(BinInstitution.institution_id == institution_id)
        )
        with self._manager.session() as session:
            total = self._count(session, Bin, scope)
            return {
                "network": self._network_distribution(session, scope),
                "country": self._country_distribution(session, scope),
                "card_type": Distribution(
                    name="card_type",
                    title="Card types",
                    slices=self._group(
                        session,
                        Bin.card_type,
                        scope,
                        label=lambda value: str(value).replace("_", " ").title(),
                    ),
                    total=total,
                ),
                "funding_type": Distribution(
                    name="funding_type",
                    title="Funding types",
                    slices=self._group(
                        session, Bin.funding_type, scope, label=lambda value: str(value).title()
                    ),
                    total=total,
                ),
                "region": self._region_distribution(session, scope),
            }

    # -- helpers -----------------------------------------------------------
    @staticmethod
    def _scope(query: AdvancedQuery | None):
        """Reduce an advanced query to a BIN-id subquery, or ``None``."""
        if query is None or query.is_empty:
            return None
        from app.repositories.search_repository import SearchRepository

        # Reuse the search builder so analytics and search always agree.
        return SearchRepository.scope_condition(query)

    @staticmethod
    def _apply(statement: Select[Any], scope: Any) -> Select[Any]:
        return statement if scope is None else statement.where(scope)

    @classmethod
    def _count(cls, session: Any, entity: type, scope: Any) -> int:
        statement = select(func.count()).select_from(entity)
        if scope is not None and entity is Bin:
            statement = statement.where(scope)
        return int(session.execute(statement).scalar() or 0)

    @staticmethod
    def _count_ranges(session: Any) -> int:
        from app.models.entities import BinRange

        return int(session.execute(select(func.count()).select_from(BinRange)).scalar() or 0)

    @classmethod
    def _group(
        cls,
        session: Any,
        column: Any,
        scope: Any,
        *,
        label: Callable[[Any], str] = str,
        skip_unknown: bool = True,
    ) -> list[Slice]:
        statement = select(column, func.count(Bin.id)).select_from(Bin).group_by(column)
        rows = session.execute(cls._apply(statement, scope)).all()
        slices: list[Slice] = []
        for value, count in rows:
            key = str(value) if value is not None else "unknown"
            if skip_unknown and key == "unknown":
                continue
            slices.append(Slice(key=key, label=label(key), value=int(count)))
        return sorted(slices, key=lambda item: item.value, reverse=True)

    @classmethod
    def _country_distribution(cls, session: Any, scope: Any) -> Distribution:
        statement = (
            select(Country.iso2, Country.name, func.count(Bin.id))
            .select_from(Bin)
            .join(Country, Bin.country_id == Country.id)
            .group_by(Country.iso2, Country.name)
        )
        rows = session.execute(cls._apply(statement, scope)).all()
        slices = [
            Slice(key=str(iso2), label=str(name), value=int(count)) for iso2, name, count in rows
        ]
        return Distribution(
            name="country",
            title="BINs by country",
            slices=sorted(slices, key=lambda item: item.value, reverse=True),
            total=sum(item.value for item in slices),
        )

    @classmethod
    def _network_distribution(cls, session: Any, scope: Any) -> Distribution:
        statement = (
            select(Network.code, Network.display_name, func.count(Bin.id))
            .select_from(Bin)
            .join(Network, Bin.network_id == Network.id)
            .group_by(Network.code, Network.display_name)
        )
        rows = session.execute(cls._apply(statement, scope)).all()
        slices = [
            Slice(key=str(code), label=str(name), value=int(count)) for code, name, count in rows
        ]
        return Distribution(
            name="network",
            title="BINs by network",
            slices=sorted(slices, key=lambda item: item.value, reverse=True),
            total=sum(item.value for item in slices),
        )

    @classmethod
    def _region_distribution(cls, session: Any, scope: Any) -> Distribution:
        primary_address = (
            select(Address.institution_id.label("inst_id"), func.min(Address.id).label("address_id"))
            .group_by(Address.institution_id)
            .subquery()
        )
        statement = (
            select(Address.region, func.count(Bin.id))
            .select_from(Bin)
            .join(BinInstitution, and_(BinInstitution.bin_id == Bin.id, BinInstitution.is_primary.is_(True)))
            .join(primary_address, primary_address.c.inst_id == BinInstitution.institution_id)
            .join(Address, Address.id == primary_address.c.address_id)
            .where(Address.region.is_not(None))
            .group_by(Address.region)
        )
        rows = session.execute(cls._apply(statement, scope)).all()
        slices = [
            Slice(key=str(region), label=str(region), value=int(count)) for region, count in rows
        ]
        return Distribution(
            name="region",
            title="BINs by state or province",
            slices=sorted(slices, key=lambda item: item.value, reverse=True),
            total=sum(item.value for item in slices),
        )

    @staticmethod
    def _top_institutions(session: Any, limit: int = 12) -> list[tuple[str, int]]:
        rows = session.execute(
            select(Institution.display_name, func.count(BinInstitution.bin_id))
            .select_from(BinInstitution)
            .join(Institution, Institution.id == BinInstitution.institution_id)
            .group_by(Institution.id, Institution.display_name)
            .order_by(func.count(BinInstitution.bin_id).desc())
            .limit(limit)
        ).all()
        return [(str(name), int(count)) for name, count in rows]

    @classmethod
    def _growth(cls, session: Any, scope: Any, months: int = 12) -> list[GrowthPoint]:
        """Records first seen per month, and the running total."""
        statement = (
            select(func.strftime("%Y-%m", Bin.first_seen), func.count(Bin.id))
            .select_from(Bin)
            .where(Bin.first_seen.is_not(None))
            .group_by(func.strftime("%Y-%m", Bin.first_seen))
            .order_by(func.strftime("%Y-%m", Bin.first_seen))
        )
        rows = session.execute(cls._apply(statement, scope)).all()
        points: list[GrowthPoint] = []
        running = 0
        for period, count in rows[-months:]:
            running += int(count)
            points.append(GrowthPoint(period=str(period), added=int(count), cumulative=running))
        return points

    @staticmethod
    def _releases(session: Any, limit: int = 12) -> list[tuple[str, datetime | None, int]]:
        """The package's own release lineage, when it carries one."""
        try:
            rows = session.execute(
                select(
                    DatabaseVersion.version,
                    DatabaseVersion.release_date,
                    DatabaseVersion.record_count,
                )
                .order_by(DatabaseVersion.release_date.desc())
                .limit(limit)
            ).all()
        except Exception:  # noqa: BLE001 - older packages have no lineage table
            return []
        return [(str(version), date, int(count or 0)) for version, date, count in rows]

    # -- precomputed statistics -------------------------------------------
    def precomputed(self, scope: str) -> list[Slice]:
        """Read a precomputed aggregate shipped with the package, if any."""
        if not self._manager.is_open:
            return []
        try:
            with self._manager.session() as session:
                rows = session.execute(
                    select(DatabaseStatistic.key, DatabaseStatistic.label, DatabaseStatistic.value)
                    .where(DatabaseStatistic.scope == scope)
                    .order_by(DatabaseStatistic.value.desc())
                ).all()
        except Exception:  # noqa: BLE001 - table absent in an older package
            return []
        return [
            Slice(key=str(key), label=str(label or key), value=int(value or 0))
            for key, label, value in rows
        ]
