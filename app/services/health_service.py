"""Database health assessment.

Every number here is measured, not estimated: integrity comes from SQLite's
own checker, orphans and duplicates from counting queries, index coverage from
comparing what exists against what the schema expects, and completeness from
counting how many records are missing each field that matters to a lookup.

The overall score is a weighted mean of those checks, so it moves for a real
reason and can be explained line by line.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import func, select, text

from app.core.logging_config import get_logger, log_event
from app.database.engine import DatabaseManager
from app.database.schema import EXTRA_INDEXES, list_indexes
from app.models.entities import (
    Address,
    Base,
    Bin,
    BinInstitution,
    BinRange,
    Conflict,
    ConflictStatus,
    Institution,
    InstitutionAlias,
)

logger = get_logger(__name__)


class HealthGrade(StrEnum):
    EXCELLENT = "excellent"
    GOOD = "good"
    FAIR = "fair"
    POOR = "poor"
    UNKNOWN = "unknown"

    @property
    def label(self) -> str:
        return self.value.upper()

    @classmethod
    def from_score(cls, score: float) -> HealthGrade:
        if score >= 0.95:
            return cls.EXCELLENT
        if score >= 0.85:
            return cls.GOOD
        if score >= 0.70:
            return cls.FAIR
        return cls.POOR

    @property
    def state(self) -> str:
        """Maps to the UI's status vocabulary."""
        return {
            HealthGrade.EXCELLENT: "success",
            HealthGrade.GOOD: "success",
            HealthGrade.FAIR: "warning",
            HealthGrade.POOR: "danger",
            HealthGrade.UNKNOWN: "info",
        }[self]


@dataclass(slots=True)
class HealthCheck:
    """One measured aspect of database health."""

    key: str
    label: str
    #: 0.0-1.0. The weighted mean of these is the overall score.
    score: float
    #: What was actually measured, rendered for the UI.
    detail: str
    weight: float = 1.0
    count: int = 0

    @property
    def grade(self) -> HealthGrade:
        return HealthGrade.from_score(self.score)

    @property
    def passed(self) -> bool:
        return self.score >= 0.85


@dataclass(slots=True)
class HealthReport:
    """The complete picture presented on the Database Administration page."""

    checks: list[HealthCheck] = field(default_factory=list)
    records: int = 0
    institutions: int = 0
    duplicates: int = 0
    orphans: int = 0
    conflicts: int = 0
    missing_fields: dict[str, int] = field(default_factory=dict)
    computed_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    error: str | None = None

    @property
    def score(self) -> float:
        total_weight = sum(check.weight for check in self.checks)
        if not total_weight:
            return 0.0
        return sum(check.score * check.weight for check in self.checks) / total_weight

    @property
    def percent(self) -> int:
        return int(round(self.score * 100))

    @property
    def grade(self) -> HealthGrade:
        if self.error or not self.checks:
            return HealthGrade.UNKNOWN
        return HealthGrade.from_score(self.score)

    @property
    def summary(self) -> str:
        if self.error:
            return self.error
        failing = [check.label for check in self.checks if not check.passed]
        if not failing:
            return "Every health check passed."
        return "Needs attention: " + ", ".join(failing)

    def check(self, key: str) -> HealthCheck | None:
        return next((item for item in self.checks if item.key == key), None)


class DatabaseHealthService:
    """Computes a :class:`HealthReport` from the live database."""

    def __init__(self, manager: DatabaseManager) -> None:
        self._manager = manager

    def evaluate(self, *, quick: bool = False) -> HealthReport:
        """Run every health check. Safe to call from a worker thread."""
        report = HealthReport()
        if not self._manager.is_open:
            report.error = "The database is not open."
            return report

        try:
            with self._manager.session() as session:
                report.records = _count(session, Bin)
                report.institutions = _count(session, Institution)

                report.checks.append(self._check_integrity(quick=quick))
                report.checks.append(self._check_indexes())

                duplicates = self._count_duplicates(session)
                report.duplicates = duplicates
                report.checks.append(
                    HealthCheck(
                        key="duplicates",
                        label="Duplicates",
                        score=_ratio_score(duplicates, report.records, tolerance=0.001),
                        detail=f"{duplicates:,} duplicate BIN or alias record(s)",
                        weight=1.0,
                        count=duplicates,
                    )
                )

                orphans = self._count_orphans(session)
                report.orphans = sum(orphans.values())
                report.checks.append(
                    HealthCheck(
                        key="orphans",
                        label="Orphans",
                        score=_ratio_score(report.orphans, max(report.records, 1), tolerance=0.002),
                        detail=(
                            f"{report.orphans:,} orphaned row(s)"
                            + (
                                " — " + ", ".join(f"{name}: {count:,}" for name, count in orphans.items() if count)
                                if report.orphans
                                else ""
                            )
                        ),
                        weight=1.0,
                        count=report.orphans,
                    )
                )

                conflicts = self._count_conflicts(session)
                report.conflicts = conflicts
                report.checks.append(
                    HealthCheck(
                        key="conflicts",
                        label="Conflicts",
                        # Open conflicts are preserved disagreements, not
                        # corruption, so they weigh less than structural faults.
                        score=_ratio_score(conflicts, max(report.records, 1), tolerance=0.02),
                        detail=f"{conflicts:,} unresolved conflicting claim(s)",
                        weight=0.6,
                        count=conflicts,
                    )
                )

                relationships = self._check_relationships(session, report.records)
                report.checks.append(relationships)

                completeness, missing = self._check_completeness(session, report.records)
                report.missing_fields = missing
                report.checks.append(completeness)

        except Exception as exc:
            logger.exception("Database health evaluation failed")
            report.error = "The database could not be assessed."
            report.checks.clear()
            _ = exc
            return report

        log_event(
            logger,
            "Database health evaluated",
            score=report.percent,
            grade=report.grade.value,
            duplicates=report.duplicates,
            orphans=report.orphans,
            conflicts=report.conflicts,
        )
        return report

    # -- individual checks -------------------------------------------------
    def _check_integrity(self, *, quick: bool) -> HealthCheck:
        pragma = "quick_check" if quick else "integrity_check"
        with self._manager.engine.connect() as connection:
            rows = connection.exec_driver_sql(f"PRAGMA {pragma}").fetchall()
        result = str(rows[0][0]) if rows else "unknown"
        ok = result.lower() == "ok"
        return HealthCheck(
            key="integrity",
            label="Integrity",
            score=1.0 if ok else 0.0,
            detail="SQLite reports the file is structurally sound." if ok else result,
            # Structural integrity dominates: a corrupt file is unusable.
            weight=3.0,
        )

    def _check_indexes(self) -> HealthCheck:
        present = set(list_indexes(self._manager.engine))
        expected: set[str] = {name for name, _ in EXTRA_INDEXES}
        for table in Base.metadata.tables.values():
            expected.update(index.name for index in table.indexes if index.name)
        missing = sorted(expected - present)
        score = 1.0 if not expected else max(0.0, 1.0 - len(missing) / len(expected))
        return HealthCheck(
            key="indexes",
            label="Indexes",
            score=score,
            detail=(
                f"{len(present)} index(es) present; all expected indexes exist."
                if not missing
                else f"{len(missing)} expected index(es) missing: {', '.join(missing[:4])}"
            ),
            weight=2.0,
            count=len(missing),
        )

    @staticmethod
    def _count_duplicates(session) -> int:
        """BINs and aliases that appear more than once."""
        duplicate_bins = session.execute(
            select(func.count()).select_from(
                select(Bin.bin).group_by(Bin.bin).having(func.count(Bin.id) > 1).subquery()
            )
        ).scalar()
        duplicate_aliases = session.execute(
            select(func.count()).select_from(
                select(InstitutionAlias.institution_id, InstitutionAlias.normalized_alias)
                .group_by(InstitutionAlias.institution_id, InstitutionAlias.normalized_alias)
                .having(func.count(InstitutionAlias.id) > 1)
                .subquery()
            )
        ).scalar()
        return int(duplicate_bins or 0) + int(duplicate_aliases or 0)

    @staticmethod
    def _count_orphans(session) -> dict[str, int]:
        """Rows pointing at records that no longer exist."""
        institution_ids = select(Institution.id)
        bin_ids = select(Bin.id)
        return {
            "bin_institutions": int(
                session.execute(
                    select(func.count())
                    .select_from(BinInstitution)
                    .where(BinInstitution.bin_id.notin_(bin_ids))
                ).scalar()
                or 0
            )
            + int(
                session.execute(
                    select(func.count())
                    .select_from(BinInstitution)
                    .where(BinInstitution.institution_id.notin_(institution_ids))
                ).scalar()
                or 0
            ),
            "addresses": int(
                session.execute(
                    select(func.count())
                    .select_from(Address)
                    .where(
                        Address.institution_id.is_not(None),
                        Address.institution_id.notin_(institution_ids),
                    )
                ).scalar()
                or 0
            ),
            "aliases": int(
                session.execute(
                    select(func.count())
                    .select_from(InstitutionAlias)
                    .where(InstitutionAlias.institution_id.notin_(institution_ids))
                ).scalar()
                or 0
            ),
            "bin_ranges": int(
                session.execute(
                    select(func.count())
                    .select_from(BinRange)
                    .where(
                        BinRange.institution_id.is_not(None),
                        BinRange.institution_id.notin_(institution_ids),
                    )
                ).scalar()
                or 0
            ),
        }

    @staticmethod
    def _count_conflicts(session) -> int:
        return int(
            session.execute(
                select(func.count())
                .select_from(Conflict)
                .where(Conflict.status == ConflictStatus.OPEN.value)
            ).scalar()
            or 0
        )

    @staticmethod
    def _check_relationships(session, records: int) -> HealthCheck:
        """How many BINs actually resolve to an institution."""
        if not records:
            return HealthCheck(
                key="relationships",
                label="Relationships",
                score=0.0,
                detail="The database contains no BIN records.",
                weight=1.5,
            )
        linked = int(
            session.execute(
                select(func.count(func.distinct(BinInstitution.bin_id)))
            ).scalar()
            or 0
        )
        unlinked = max(0, records - linked)
        score = linked / records
        return HealthCheck(
            key="relationships",
            label="Relationships",
            score=score,
            detail=(
                f"{linked:,} of {records:,} BINs resolve to an institution"
                + (f"; {unlinked:,} unresolved" if unlinked else "")
            ),
            weight=1.5,
            count=unlinked,
        )

    @staticmethod
    def _check_completeness(session, records: int) -> tuple[HealthCheck, dict[str, int]]:
        """How much of the lookup-relevant metadata is actually populated."""
        if not records:
            return (
                HealthCheck(
                    key="completeness",
                    label="Completeness",
                    score=0.0,
                    detail="No records to assess.",
                    weight=1.0,
                ),
                {},
            )
        missing = {
            "network": _count_missing(session, Bin.network_id.is_(None)),
            "country": _count_missing(session, Bin.country_id.is_(None)),
            "card type": _count_missing(session, Bin.card_type == "unknown"),
            "funding type": _count_missing(session, Bin.funding_type == "unknown"),
            "currency": _count_missing(session, Bin.currency_code.is_(None)),
        }
        # Average field coverage across the fields a lookup result shows.
        coverage = sum(1 - (count / records) for count in missing.values()) / len(missing)
        worst = max(missing.items(), key=lambda item: item[1])
        return (
            HealthCheck(
                key="completeness",
                label="Completeness",
                score=coverage,
                detail=(
                    f"{coverage * 100:.1f}% average field coverage"
                    + (f"; {worst[0]} missing on {worst[1]:,} record(s)" if worst[1] else "")
                ),
                weight=1.0,
                count=sum(missing.values()),
            ),
            missing,
        )

    # -- repair -----------------------------------------------------------
    def remove_orphans(self) -> dict[str, int]:
        """Delete rows whose parent no longer exists. Returns what was removed."""
        removed: dict[str, int] = {}
        statements = {
            "bin_institutions": (
                "DELETE FROM bin_institutions WHERE bin_id NOT IN (SELECT id FROM bins) "
                "OR institution_id NOT IN (SELECT id FROM institutions)"
            ),
            "addresses": (
                "DELETE FROM addresses WHERE institution_id IS NOT NULL "
                "AND institution_id NOT IN (SELECT id FROM institutions)"
            ),
            "institution_aliases": (
                "DELETE FROM institution_aliases "
                "WHERE institution_id NOT IN (SELECT id FROM institutions)"
            ),
            "bin_ranges": (
                "DELETE FROM bin_ranges WHERE institution_id IS NOT NULL "
                "AND institution_id NOT IN (SELECT id FROM institutions)"
            ),
        }
        with self._manager.engine.begin() as connection:
            for table, statement in statements.items():
                result = connection.execute(text(statement))
                removed[table] = int(result.rowcount or 0)
        log_event(logger, "Orphan cleanup completed", removed=removed)
        return removed


def _count(session, entity: type) -> int:
    return int(session.execute(select(func.count()).select_from(entity)).scalar() or 0)


def _count_missing(session, condition) -> int:
    return int(session.execute(select(func.count()).select_from(Bin).where(condition)).scalar() or 0)


def _ratio_score(bad: int, total: int, *, tolerance: float) -> float:
    """Score a defect count.

    Zero defects scores 1.0; the score falls to 0.0 as the defect ratio reaches
    *tolerance*, so a handful of problems in a large database is not treated
    the same as a handful in a small one.
    """
    if bad <= 0:
        return 1.0
    if total <= 0:
        return 0.0
    ratio = bad / total
    return max(0.0, 1.0 - (ratio / tolerance))
