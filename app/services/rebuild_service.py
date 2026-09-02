"""Rebuild the whole database from the personal BIN list, atomically.

The workflow this exists for: you edit ``data/bin-list.csv``, you rebuild, and
Bin-Tel is looking at the new data. Nothing is patched in place — a rebuild
constructs a brand-new database from the list, checks it, and only then swaps
it into place.

The order matters, and it is the same order the update installer uses:

    read the list → build in staging → verify the staged file
    → back up the live one → close → swap → reopen → index → stamp

Every failure before the swap leaves the live database untouched, because the
live database is not opened for writing at any point. A failure after the swap
is recoverable from the backup the rebuild just took: :meth:`RebuildService.
rollback` puts it back.

Three copies exist by design, and they are what makes a bad list survivable:

``current``
    the database the application is using;
``previous``
    the one the last rebuild replaced, kept for rollback;
``candidate``
    the staged file, which becomes ``current`` only if it verifies.
"""

from __future__ import annotations

import shutil
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from app.core.constants import APP_VERSION, SCHEMA_VERSION
from app.core.errors import DatabaseCorruptError, DatabaseError, ImportError_
from app.core.logging_config import get_logger, log_event
from app.database.backup import install_database
from app.database.engine import DatabaseManager
from app.database.integrity import verify_database
from app.database.schema import (
    analyze,
    create_schema,
    rebuild_indexes,
    stamp_schema_version,
    vacuum,
    write_metadata,
)
from app.models.entities import DatabaseMetadata
from app.services.bin_list import BinListReport, read_bin_list
from app.services.dedupe_service import DedupeService
from app.services.enrichment_service import EnrichmentReport, EnrichmentService
from app.services.ingest_service import IngestResult, IngestService

logger = get_logger(__name__)

#: Where the previous database waits, in case the new one is wrong.
PREVIOUS_SUFFIX = ".previous"

#: Rows are committed in batches so one long transaction never holds the file.
BATCH_SIZE = 500

#: A rebuild that would throw away most of the database is stopped and asked
#: about. Deleting rows from the list is legitimate; deleting them by accident
#: — a truncated paste, a half-saved file — looks exactly the same from here,
#: and only the person running it can tell the difference.
SHRINK_THRESHOLD = 0.5


@dataclass(slots=True)
class RebuildOutcome:
    """What one rebuild did."""

    version: str
    previous_version: str | None = None
    accepted: int = 0
    distinct_bins: int = 0
    shared_bins: int = 0
    rejected: int = 0
    duplicates: int = 0
    institutions: int = 0
    conflicts: int = 0
    ranges: int = 0
    previous_path: Path | None = None
    #: What the enrichment pass filled in from evidence already in the build.
    enrichment: EnrichmentReport = field(default_factory=EnrichmentReport)
    problems: list[str] = field(default_factory=list)
    elapsed_seconds: float = 0.0

    @property
    def can_roll_back(self) -> bool:
        return self.previous_path is not None and self.previous_path.exists()

    @property
    def summary(self) -> str:
        parts = [
            f"{self.distinct_bins or self.accepted:,} BIN(s)",
            f"{self.institutions:,} institution(s)",
        ]
        if self.shared_bins:
            parts.append(f"{self.shared_bins:,} with more than one institution")
        if self.ranges:
            parts.append(f"{self.ranges:,} range(s)")
        if self.duplicates:
            parts.append(f"{self.duplicates:,} duplicate(s) superseded")
        if self.rejected:
            parts.append(f"{self.rejected:,} row(s) skipped")
        if self.conflicts:
            parts.append(f"{self.conflicts:,} conflict(s) recorded")
        if self.enrichment.total:
            parts.append(f"{self.enrichment.total:,} field(s) filled in")
        return " · ".join(parts)


class ShrinkRefused(DatabaseError):
    """The rebuild would lose most of the database, so it was not installed."""


class RebuildService:
    """Builds a new database from the BIN list and installs it atomically."""

    def __init__(
        self,
        manager: DatabaseManager,
        database_path: Path,
        *,
        staging_dir: Path | None = None,
    ) -> None:
        self._manager = manager
        self._database_path = database_path
        self._staging_dir = staging_dir or database_path.parent / "staging"
        self._enrichment = EnrichmentReport()

    # -- paths --------------------------------------------------------------
    def set_paths(self, database_path: Path, staging_dir: Path | None = None) -> None:
        self._database_path = database_path
        self._staging_dir = staging_dir or database_path.parent / "staging"

    @property
    def previous_path(self) -> Path:
        """Where the database this rebuild replaces is kept."""
        return self._database_path.with_suffix(
            self._database_path.suffix + PREVIOUS_SUFFIX
        )

    @property
    def can_roll_back(self) -> bool:
        try:
            return self.previous_path.exists() and self.previous_path.stat().st_size > 0
        except OSError:  # pragma: no cover - unreadable volume
            return False

    # -- the rebuild --------------------------------------------------------
    def rebuild(
        self,
        list_path: Path | None = None,
        *,
        version: str | None = None,
        progress: Callable[[str], None] | None = None,
        allow_shrink: bool = False,
    ) -> RebuildOutcome:
        """Build a database from the list and make it the active one.

        Raises before touching the live database when the list cannot be read,
        when the staged database fails verification, or when the rebuild would
        drop most of the records and *allow_shrink* was not asked for.
        """
        started = datetime.now(UTC)

        def emit(message: str) -> None:
            if progress is not None:
                progress(message)

        emit("Reading the BIN list…")
        report = read_bin_list(list_path)

        previous_count = self._current_record_count()
        if (
            not allow_shrink
            and previous_count > 0
            and report.distinct_bins < previous_count * SHRINK_THRESHOLD
        ):
            raise ShrinkRefused(
                "The BIN list has far fewer rows than the database it would replace, "
                "so nothing was changed.",
                detail=(
                    f"The list holds {report.distinct_bins:,} BIN(s); the current database "
                    f"holds {previous_count:,}. If that is deliberate, rebuild again "
                    "asking to allow the shrink."
                ),
            )

        version = version or self._derive_version(started)
        self._staging_dir.mkdir(parents=True, exist_ok=True)
        candidate = self._staging_dir / f"bintel-{version}.candidate.sqlite"
        candidate.unlink(missing_ok=True)

        outcome = RebuildOutcome(
            version=version,
            previous_version=self._current_version(),
            accepted=report.accepted,
            distinct_bins=report.distinct_bins,
            shared_bins=report.shared_bins,
            rejected=report.rejected,
            duplicates=report.duplicates,
            problems=[str(problem) for problem in report.problems[:50]],
        )

        try:
            emit(f"Building {report.accepted:,} record(s)…")
            result = self._build_candidate(candidate, report, version, emit)
            outcome.enrichment = self._enrichment
            outcome.institutions = result.institutions_created
            outcome.ranges = result.ranges_created
            outcome.conflicts = result.conflicts

            emit("Verifying the new database…")
            verification = verify_database(candidate, quick=False)
            if not verification.ok:
                raise DatabaseCorruptError(
                    "The rebuilt database did not pass verification, so it was not "
                    "installed. Your existing database is unchanged.",
                    detail="; ".join(verification.errors),
                )

            emit("Installing…")
            outcome.previous_path = self._install(candidate)
        finally:
            candidate.unlink(missing_ok=True)

        outcome.elapsed_seconds = (datetime.now(UTC) - started).total_seconds()
        log_event(
            logger,
            "Database rebuilt from the BIN list",
            version=version,
            accepted=outcome.accepted,
            rejected=outcome.rejected,
            institutions=outcome.institutions,
        )
        emit(f"Ready — {outcome.summary}")
        return outcome

    # -- rollback -----------------------------------------------------------
    def rollback(self) -> Path:
        """Put the database the last rebuild replaced back into place."""
        previous = self.previous_path
        if not self.can_roll_back:
            raise DatabaseError(
                "There is no previous database to roll back to.",
                detail=f"Expected it at {previous}.",
            )

        report = verify_database(previous, quick=False)
        if not report.ok:
            raise DatabaseCorruptError(
                "The previous database did not pass verification, so it was not "
                "restored.",
                detail="; ".join(report.errors),
            )

        was_open = self._manager.is_open
        self._manager.close()
        # Swap rather than move: the database being rolled back from becomes the
        # thing you can roll *forward* to, so neither copy is ever discarded.
        superseded = self._staging_dir / "bintel-superseded.sqlite"
        self._staging_dir.mkdir(parents=True, exist_ok=True)
        try:
            if self._database_path.exists():
                shutil.copy2(self._database_path, superseded)
            install_database(previous, self._database_path)
            if superseded.exists():
                superseded.replace(previous)
        except Exception:
            if was_open:
                self._manager.open(self._database_path)
            raise
        self._manager.open(self._database_path)
        logger.info("Rolled back to the previous database")
        return self._database_path

    # -- internals ----------------------------------------------------------
    def _build_candidate(
        self,
        candidate: Path,
        report: BinListReport,
        version: str,
        emit: Callable[[str], None],
    ) -> IngestResult:
        """Create and populate the staged database. Never touches the live one."""
        manager = DatabaseManager(candidate)
        manager.open(create_if_missing=True)
        try:
            create_schema(manager.engine)
            result = IngestResult()
            with manager.session() as session:
                ingest = IngestService(
                    session,
                    source_code="bin-list",
                    source_name="Personal BIN list",
                    record_normalization=True,
                )
                for index, record in enumerate(report.records, start=1):
                    ingest.ingest(record, result)
                    if index % BATCH_SIZE == 0:
                        session.commit()
                        emit(f"Building… {index:,} of {report.accepted:,}")
                session.commit()

            # Complete the record from what the build already knows, before
            # anything is indexed or measured, so the figures describe the
            # database that will actually be installed.
            emit("Filling in what the list left blank…")
            with manager.transaction() as session:
                self._enrichment = EnrichmentService(session).run()

            with manager.transaction() as session:
                dedupe = DedupeService(session)
                dedupe_report = dedupe.run(merge=False)
            result.conflicts += dedupe_report.range_conflicts_recorded

            rebuild_indexes(manager.engine)
            stamp_schema_version(manager.engine, SCHEMA_VERSION)
            analyze(manager.engine)
            with manager.session() as session:
                write_metadata(
                    session,
                    {
                        DatabaseMetadata.VERSION: version,
                        DatabaseMetadata.SCHEMA_VERSION: SCHEMA_VERSION,
                        DatabaseMetadata.RELEASE_DATE: datetime.now(UTC).isoformat(),
                        DatabaseMetadata.RECORD_COUNT: report.distinct_bins,
                        DatabaseMetadata.PUBLISHER: "Local BIN list",
                        DatabaseMetadata.NOTES: (
                            f"Built from {report.path.name} by Bin-Tel {APP_VERSION}"
                        ),
                    },
                )
                session.commit()
            vacuum(manager.engine)
            return result
        finally:
            manager.close()

    def _install(self, candidate: Path) -> Path | None:
        """Swap the candidate in, keeping the outgoing database for rollback."""
        previous = self.previous_path
        kept: Path | None = None
        was_open = self._manager.is_open
        self._manager.close()
        try:
            if self._database_path.exists() and self._database_path.stat().st_size > 0:
                shutil.copy2(self._database_path, previous)
                kept = previous
            install_database(candidate, self._database_path)
        except Exception:
            # Nothing was replaced; reopen so the application keeps working.
            if was_open:
                self._manager.open(self._database_path)
            raise
        self._manager.open(self._database_path)
        return kept

    def _current_version(self) -> str | None:
        if not self._manager.is_open:
            return None
        try:
            with self._manager.session() as session:
                row = session.get(DatabaseMetadata, DatabaseMetadata.VERSION)
                return row.value if row else None
        except Exception:
            return None

    def _current_record_count(self) -> int:
        """How many BINs the live database holds, counted rather than assumed."""
        if not self._manager.is_open:
            return 0
        try:
            from sqlalchemy import func, select

            from app.models.entities import Bin

            with self._manager.session() as session:
                return int(session.execute(select(func.count(Bin.id))).scalar_one())
        except Exception:
            return 0

    @staticmethod
    def _derive_version(moment: datetime) -> str:
        """``2026.09.1`` — the date the list was built, not a release number."""
        return f"{moment.year}.{moment.month:02d}.{moment.day}"


__all__ = [
    "ImportError_",
    "RebuildOutcome",
    "RebuildService",
    "ShrinkRefused",
]
