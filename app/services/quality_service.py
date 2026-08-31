"""Database quality metrics.

Every figure here is counted from the database it describes. Nothing is
estimated, sampled or assumed, and a metric whose denominator is zero reports
*no measurement* rather than a misleading 100%.

The metrics answer questions a person actually has about a release:

* how much of it resolves to an institution at all;
* how much of it is eight-digit, which is what modern issuance needs;
* how much of it is current rather than historical;
* how much of it disagrees with itself;
* where the gaps are.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import func, select

from app.core.logging_config import get_logger
from app.database.engine import DatabaseManager
from app.models.entities import (
    Bin,
    BinInstitution,
    BinRange,
    Conflict,
    ConflictStatus,
    DataQualityMetric,
    Institution,
    PrefixType,
)

logger = get_logger(__name__)


@dataclass(slots=True)
class QualityMetric:
    """One measured figure, with the counts that produced it."""

    key: str
    label: str
    numerator: int
    denominator: int
    #: What a *good* value looks like, so a reader knows which way is up.
    higher_is_better: bool = True
    description: str = ""

    @property
    def measured(self) -> bool:
        """Whether there was anything to measure."""
        return self.denominator > 0

    @property
    def ratio(self) -> float | None:
        if not self.measured:
            return None
        return self.numerator / self.denominator

    @property
    def percent(self) -> float | None:
        ratio = self.ratio
        return None if ratio is None else round(ratio * 100, 2)

    @property
    def display(self) -> str:
        percent = self.percent
        if percent is None:
            return "Not measured"
        return f"{percent:.2f}%"

    @property
    def detail(self) -> str:
        if not self.measured:
            return "Nothing to measure"
        return f"{self.numerator:,} of {self.denominator:,}"


@dataclass(slots=True)
class QualityReport:
    """Every metric for one database."""

    database_version: str | None = None
    computed_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    metrics: list[QualityMetric] = field(default_factory=list)
    elapsed_ms: float = 0.0
    error: str | None = None

    def get(self, key: str) -> QualityMetric | None:
        return next((item for item in self.metrics if item.key == key), None)

    @property
    def measured(self) -> list[QualityMetric]:
        return [item for item in self.metrics if item.measured]

    @property
    def summary(self) -> str:
        if self.error:
            return self.error
        resolution = self.get("institution_resolution")
        if resolution is None or not resolution.measured:
            return "No BIN records to measure."
        return f"{resolution.display} of BINs resolve to an institution."


class DataQualityService:
    """Computes, stores and reads back database quality metrics."""

    def __init__(self, manager: DatabaseManager) -> None:
        self._manager = manager

    @property
    def available(self) -> bool:
        return self._manager.is_open

    def evaluate(self, *, database_version: str | None = None) -> QualityReport:
        """Count every metric. Safe to call from a worker thread."""
        started = time.perf_counter()
        report = QualityReport(database_version=database_version)
        if not self._manager.is_open:
            report.error = "The database is not open."
            return report

        with self._manager.session() as session:

            def count(entity, *conditions) -> int:
                statement = select(func.count()).select_from(entity)
                for condition in conditions:
                    statement = statement.where(condition)
                return int(session.execute(statement).scalar() or 0)

            bins = count(Bin)
            institutions = count(Institution)
            ranges = count(BinRange)

            # A BIN "resolves" when at least one current issuing relationship
            # names an institution. A parent or processor link alone does not
            # answer "who issued this", so it does not count here.
            resolved = int(
                session.execute(
                    select(func.count(func.distinct(BinInstitution.bin_id))).where(
                        BinInstitution.is_current.is_(True)
                    )
                ).scalar()
                or 0
            )
            extended = count(Bin, Bin.prefix_length >= 8)
            roots = count(Bin, Bin.prefix_length == 6)
            with_country = count(Bin, Bin.country_id.is_not(None))
            with_network = count(Bin, Bin.network_id.is_not(None))
            current_links = int(
                session.execute(
                    select(func.count(func.distinct(BinInstitution.bin_id))).where(
                        BinInstitution.is_current.is_(True)
                    )
                ).scalar()
                or 0
            )
            all_linked = int(
                session.execute(
                    select(func.count(func.distinct(BinInstitution.bin_id)))
                ).scalar()
                or 0
            )
            open_conflicts = count(
                Conflict, Conflict.status == ConflictStatus.OPEN.value
            )

            # Duplicates are counted on identity, not on display text: two rows
            # describing the same allocation of the same length.
            duplicate_bins = int(
                session.execute(
                    select(func.count()).select_from(
                        select(Bin.prefix, Bin.prefix_length)
                        .group_by(Bin.prefix, Bin.prefix_length)
                        .having(func.count() > 1)
                        .subquery()
                    )
                ).scalar()
                or 0
            )
            duplicate_institutions = int(
                session.execute(
                    select(func.count()).select_from(
                        select(Institution.normalized_name, Institution.country_id)
                        .group_by(Institution.normalized_name, Institution.country_id)
                        .having(func.count() > 1)
                        .subquery()
                    )
                ).scalar()
                or 0
            )
            account_ranges = count(
                BinRange, BinRange.range_type == "account_range"
            )

        report.metrics = [
            QualityMetric(
                key="institution_resolution",
                label="Institution resolution",
                numerator=resolved,
                denominator=bins,
                description="BINs with a current institution relationship",
            ),
            QualityMetric(
                key="extended_coverage",
                label="8-digit coverage",
                numerator=extended,
                denominator=bins,
                description="Assignments recorded at eight digits",
            ),
            QualityMetric(
                key="root_coverage",
                label="6-digit legacy coverage",
                numerator=roots,
                denominator=bins,
                description="Assignments recorded at six digits",
            ),
            QualityMetric(
                key="country_coverage",
                label="Country coverage",
                numerator=with_country,
                denominator=bins,
                description="BINs with a country of issuance",
            ),
            QualityMetric(
                key="network_coverage",
                label="Network coverage",
                numerator=with_network,
                denominator=bins,
                description="BINs attributed to a card scheme",
            ),
            QualityMetric(
                key="current_coverage",
                label="Current-record coverage",
                numerator=current_links,
                denominator=all_linked,
                description="Linked BINs whose relationship is current",
            ),
            QualityMetric(
                key="range_coverage",
                label="Range coverage",
                numerator=account_ranges,
                denominator=ranges,
                description="Allocated ranges that are account ranges",
            ),
            QualityMetric(
                key="duplicate_rate",
                label="Duplicate rate",
                numerator=duplicate_bins,
                denominator=bins,
                higher_is_better=False,
                description="Prefixes recorded more than once at the same length",
            ),
            QualityMetric(
                key="duplicate_institution_rate",
                label="Duplicate institutions",
                numerator=duplicate_institutions,
                denominator=institutions,
                higher_is_better=False,
                description="Institutions sharing a canonical name in one country",
            ),
            QualityMetric(
                key="conflict_rate",
                label="Conflict rate",
                numerator=open_conflicts,
                denominator=bins,
                higher_is_better=False,
                description="Unresolved conflicting claims",
            ),
            QualityMetric(
                key="missing_institution_rate",
                label="Missing institution",
                numerator=max(0, bins - resolved),
                denominator=bins,
                higher_is_better=False,
                description="BINs that name no current institution",
            ),
            QualityMetric(
                key="missing_country_rate",
                label="Missing country",
                numerator=max(0, bins - with_country),
                denominator=bins,
                higher_is_better=False,
                description="BINs with no country of issuance",
            ),
        ]
        report.elapsed_ms = (time.perf_counter() - started) * 1000
        logger.debug(
            "Quality metrics computed",
            extra={
                "context": {
                    "metrics": len(report.metrics),
                    "elapsed_ms": round(report.elapsed_ms, 1),
                }
            },
        )
        return report

    # -- persistence -------------------------------------------------------
    def store(self, report: QualityReport) -> int:
        """Write the report into ``data_quality_metrics``.

        Used by the release pipeline so a published package carries the figures
        it was measured at, rather than making every client recount.
        """
        if not self._manager.is_open or report.error:
            return 0
        written = 0
        with self._manager.transaction() as session:
            for metric in report.metrics:
                existing = session.execute(
                    select(DataQualityMetric).where(
                        DataQualityMetric.key == metric.key,
                        DataQualityMetric.database_version == report.database_version,
                    )
                ).scalar_one_or_none()
                row = existing or DataQualityMetric(
                    key=metric.key, database_version=report.database_version
                )
                row.label = metric.label
                row.numerator = metric.numerator
                row.denominator = metric.denominator
                row.ratio = metric.ratio
                row.computed_at = report.computed_at
                if existing is None:
                    session.add(row)
                written += 1
        return written

    def stored(self, database_version: str | None = None) -> list[QualityMetric]:
        """Metrics a package was published with, if it carries any."""
        if not self._manager.is_open:
            return []
        with self._manager.session() as session:
            statement = select(DataQualityMetric)
            if database_version:
                statement = statement.where(
                    DataQualityMetric.database_version == database_version
                )
            rows = session.execute(statement.order_by(DataQualityMetric.key)).scalars().all()
            return [
                QualityMetric(
                    key=row.key,
                    label=row.label,
                    numerator=row.numerator,
                    denominator=row.denominator,
                )
                for row in rows
            ]


__all__ = ["DataQualityService", "QualityMetric", "QualityReport"]
