"""An institution's complete BIN portfolio.

Answering "which BINs does this bank have?" with the rows directly linked to
one institution row is not an answer — it is the first step of one. A bank
reaches its portfolio through several routes, and stopping at the first one
under-reports it:

* the institution's own current relationships;
* its **historical** relationships — BINs it used to issue are part of its
  record, not something to quietly omit;
* the relationships of its **subsidiaries**, where a parent group is being
  asked about;
* **allocated ranges** assigned to it, which are not discrete BIN rows at all;
* assignments reached through a **predecessor** it absorbed.

Everything is deduplicated by value and grouped, so the caller gets one
coherent portfolio rather than the same BIN three times.
"""

from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass, field

from sqlalchemy import func, select

from app.core.logging_config import get_logger
from app.database.engine import DatabaseManager
from app.models.entities import (
    Bin,
    BinInstitution,
    BinRange,
    Institution,
    InstitutionLinkType,
    InstitutionRelationship,
)
from app.models.schemas import BinFilters, BinRow, Page, PageRequest
from app.repositories.bin_repository import BinRepository

logger = get_logger(__name__)

#: Relationship types that mean "this institution's BINs are also ours".
#: A processor association deliberately is not one of them: processing a
#: portfolio is not owning it.
INHERITING_LINKS: frozenset[str] = frozenset(
    {
        InstitutionLinkType.SUBSIDIARY.value,
        InstitutionLinkType.PREDECESSOR.value,
        InstitutionLinkType.BRAND_OF.value,
    }
)

#: How deep a group structure is followed. Deep enough for a real banking
#: group, shallow enough that a cycle in the data cannot hang a lookup.
MAX_DEPTH = 4


@dataclass(slots=True)
class PortfolioGroup:
    """One grouping of a portfolio — by network, country or prefix length."""

    key: str
    label: str
    count: int = 0

    @property
    def display(self) -> str:
        return f"{self.label} ({self.count:,})"


@dataclass(slots=True)
class InstitutionPortfolio:
    """Everything an institution is associated with, and how it was reached."""

    institution_id: int
    display_name: str = ""
    #: Every institution id whose BINs count towards this portfolio, including
    #: the institution itself.
    contributing_ids: tuple[int, ...] = ()
    total_bins: int = 0
    current_bins: int = 0
    historical_bins: int = 0
    root_bins: int = 0
    extended_bins: int = 0
    ranges: int = 0
    by_network: list[PortfolioGroup] = field(default_factory=list)
    by_country: list[PortfolioGroup] = field(default_factory=list)
    by_length: list[PortfolioGroup] = field(default_factory=list)
    elapsed_ms: float = 0.0

    @property
    def is_empty(self) -> bool:
        return self.total_bins == 0 and self.ranges == 0

    @property
    def includes_related(self) -> bool:
        return len(self.contributing_ids) > 1

    @property
    def summary(self) -> str:
        parts = [f"{self.total_bins:,} BIN(s)"]
        if self.extended_bins:
            parts.append(f"{self.extended_bins:,} eight-digit")
        if self.ranges:
            parts.append(f"{self.ranges:,} range(s)")
        if self.historical_bins:
            parts.append(f"{self.historical_bins:,} historical")
        if self.includes_related:
            parts.append(f"across {len(self.contributing_ids)} related institutions")
        return " · ".join(parts)


class PortfolioService:
    """Assembles an institution's complete BIN portfolio."""

    def __init__(self, manager: DatabaseManager, bins: BinRepository) -> None:
        self._manager = manager
        self._bins = bins

    @property
    def available(self) -> bool:
        return self._manager.is_open

    # -- identity expansion ------------------------------------------------
    def contributing_ids(self, institution_id: int) -> list[int]:
        """The institution plus every related one whose BINs belong to it.

        Walks subsidiaries, predecessors and brands, breadth-first and depth-
        capped. Cycles in the data are survivable rather than fatal, because
        reference data is not guaranteed to be acyclic.
        """
        if not self._manager.is_open:
            return [institution_id]
        found = {institution_id}
        frontier = [institution_id]
        with self._manager.session() as session:
            for _ in range(MAX_DEPTH):
                if not frontier:
                    break
                rows = (
                    session.execute(
                        select(InstitutionRelationship.related_institution_id).where(
                            InstitutionRelationship.institution_id.in_(frontier),
                            InstitutionRelationship.relationship_type.in_(
                                INHERITING_LINKS
                            ),
                        )
                    )
                    .scalars()
                    .all()
                )
                # The legacy single-parent column expresses the same idea, so a
                # package that only fills that in is not left out.
                legacy = (
                    session.execute(
                        select(Institution.id).where(
                            Institution.parent_id.in_(frontier)
                        )
                    )
                    .scalars()
                    .all()
                )
                frontier = [
                    identifier
                    for identifier in {*rows, *legacy}
                    if identifier not in found
                ]
                found.update(frontier)
        return sorted(found)

    # -- the portfolio -----------------------------------------------------
    def build(self, institution_id: int) -> InstitutionPortfolio:
        """Count and group everything the institution is associated with."""
        started = time.perf_counter()
        portfolio = InstitutionPortfolio(institution_id=institution_id)
        if not self._manager.is_open:
            return portfolio

        ids = self.contributing_ids(institution_id)
        portfolio.contributing_ids = tuple(ids)

        # A BIN reached through two related institutions is one BIN, so the set
        # is keyed by the value. Declared here because the groupings below read
        # it after the session closes.
        seen: dict[str, tuple[int, bool, int | None, int | None]] = {}

        with self._manager.session() as session:
            institution = session.get(Institution, institution_id)
            portfolio.display_name = institution.display_name if institution else ""

            rows = session.execute(
                select(
                    Bin.bin,
                    Bin.prefix_length,
                    BinInstitution.is_current,
                    Bin.network_id,
                    Bin.country_id,
                )
                .join(BinInstitution, BinInstitution.bin_id == Bin.id)
                .where(BinInstitution.institution_id.in_(ids))
            ).all()

            for value, length, is_current, network_id, country_id in rows:
                previous = seen.get(value)
                current = bool(is_current)
                if previous is None:
                    seen[value] = (length or len(value), current, network_id, country_id)
                elif current and not previous[1]:
                    # A current relationship supersedes a historical one for
                    # the purpose of describing the portfolio today.
                    seen[value] = (previous[0], True, previous[2], previous[3])

            portfolio.total_bins = len(seen)
            portfolio.current_bins = sum(1 for item in seen.values() if item[1])
            portfolio.historical_bins = portfolio.total_bins - portfolio.current_bins
            portfolio.extended_bins = sum(1 for item in seen.values() if item[0] >= 8)
            portfolio.root_bins = portfolio.total_bins - portfolio.extended_bins

            portfolio.ranges = int(
                session.execute(
                    select(func.count())
                    .select_from(BinRange)
                    .where(BinRange.institution_id.in_(ids))
                ).scalar()
                or 0
            )

            portfolio.by_network = self._group(
                session, ids, "network", seen
            )
            portfolio.by_country = self._group(
                session, ids, "country", seen
            )

        lengths: dict[int, int] = defaultdict(int)
        for length, _, _, _ in seen.values():
            lengths[length] += 1
        portfolio.by_length = [
            PortfolioGroup(key=str(length), label=f"{length}-digit", count=count)
            for length, count in sorted(lengths.items())
        ]

        portfolio.elapsed_ms = (time.perf_counter() - started) * 1000
        logger.debug(
            "Portfolio assembled",
            extra={
                "context": {
                    "institutions": len(ids),
                    "bins": portfolio.total_bins,
                    "elapsed_ms": round(portfolio.elapsed_ms, 1),
                }
            },
        )
        return portfolio

    @staticmethod
    def _group(
        session,
        ids: list[int],
        dimension: str,
        seen: dict[str, tuple[int, bool, int | None, int | None]],
    ) -> list[PortfolioGroup]:
        """Group the deduplicated BIN set by network or country."""
        from app.models.entities import Country, Network

        index = 2 if dimension == "network" else 3
        counts: dict[int | None, int] = defaultdict(int)
        for item in seen.values():
            counts[item[index]] += 1
        if not counts:
            return []

        identifiers = [key for key in counts if key is not None]
        labels: dict[int, str] = {}
        if identifiers:
            model = Network if dimension == "network" else Country
            column = model.display_name if dimension == "network" else model.name
            labels = {
                identifier: str(label)
                for identifier, label in session.execute(
                    select(model.id, column).where(model.id.in_(identifiers))
                ).all()
            }

        groups = [
            PortfolioGroup(
                key=str(key) if key is not None else "unknown",
                label=labels.get(key, "Unknown") if key is not None else "Unknown",
                count=count,
            )
            for key, count in counts.items()
        ]
        groups.sort(key=lambda group: (-group.count, group.label))
        return groups

    # -- paging ------------------------------------------------------------
    def page(
        self,
        institution_id: int,
        request: PageRequest,
        filters: BinFilters | None = None,
        *,
        include_related: bool = True,
    ) -> Page[BinRow]:
        """One page of the portfolio, optionally including related institutions."""
        ids = (
            self.contributing_ids(institution_id)
            if include_related
            else [institution_id]
        )
        return self._bins.page_for_institutions(ids, request, filters)

    def all_rows(
        self,
        institution_id: int,
        filters: BinFilters | None = None,
        *,
        include_related: bool = True,
        limit: int = 100_000,
    ) -> list[BinRow]:
        ids = (
            self.contributing_ids(institution_id)
            if include_related
            else [institution_id]
        )
        return self._bins.all_bins_for_institutions(ids, filters, limit=limit)
