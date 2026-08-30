"""Database download, verification and atomic installation.

The pipeline never touches the working database until a candidate has passed
every gate:

    manifest → download to staging → checksum → SQLite verification
             → backup current → close database → atomic replace
             → reopen → reindex → stamp metadata

If anything after the backup fails, the previous database is restored
automatically, so a failed update can never leave the application unusable.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

from app.core.constants import APP_NAME, APP_VERSION, SCHEMA_VERSION
from app.core.errors import (
    BinTelError,
    ChecksumMismatchError,
    DatabaseCorruptError,
    OperationCancelled,
    SchemaVersionError,
    UpdateError,
)
from app.core.logging_config import get_logger
from app.database.backup import install_database
from app.database.engine import DatabaseManager
from app.database.integrity import VerificationReport, verify_database
from app.database.schema import analyze, rebuild_indexes, stamp_schema_version
from app.models.entities import DatabaseMetadata, UpdateStatus
from app.providers.base import BaseProvider, DownloadProgress
from app.providers.manager import ProviderManager
from app.providers.manifest import DatabaseManifest
from app.services.backup_service import BackupService
from app.services.update_journal import UpdateJournal
from app.utils.hashing import file_checksum

logger = get_logger(__name__)


class UpdateState(StrEnum):
    """Every stage the first-run and update screens can display."""

    IDLE = "idle"
    CHECKING = "checking"
    UP_TO_DATE = "up_to_date"
    AVAILABLE = "available"
    DOWNLOADING = "downloading"
    VERIFYING = "verifying"
    BACKING_UP = "backing_up"
    INSTALLING = "installing"
    INDEXING = "indexing"
    COMPLETE = "complete"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def label(self) -> str:
        return {
            UpdateState.IDLE: "Ready",
            UpdateState.CHECKING: "Checking for updates…",
            UpdateState.UP_TO_DATE: "Up to date",
            UpdateState.AVAILABLE: "Update available",
            UpdateState.DOWNLOADING: "Downloading…",
            UpdateState.VERIFYING: "Verifying…",
            UpdateState.BACKING_UP: "Backing up…",
            UpdateState.INSTALLING: "Installing…",
            UpdateState.INDEXING: "Building indexes…",
            UpdateState.COMPLETE: "Ready.",
            UpdateState.FAILED: "Failed",
            UpdateState.CANCELLED: "Cancelled",
        }[self]


@dataclass(slots=True)
class UpdateProgress:
    """A single progress notification, safe to marshal to the GUI thread."""

    state: UpdateState
    message: str = ""
    received: int = 0
    total: int = 0
    speed: float = 0.0
    eta_seconds: float | None = None

    @property
    def fraction(self) -> float:
        return min(1.0, self.received / self.total) if self.total else 0.0

    @property
    def percent(self) -> int:
        return int(self.fraction * 100)

    @property
    def indeterminate(self) -> bool:
        return self.total <= 0


@dataclass(slots=True)
class UpdateCheck:
    """Outcome of a lightweight metadata check."""

    manifest: DatabaseManifest | None = None
    current_version: str | None = None
    update_available: bool = False
    checked_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    provider: str = ""
    message: str = ""

    @property
    def latest_version(self) -> str | None:
        return self.manifest.version if self.manifest else None


@dataclass(slots=True)
class UpdateOutcome:
    """Result of a full install run."""

    success: bool
    version: str | None = None
    previous_version: str | None = None
    bytes_downloaded: int = 0
    backup_path: Path | None = None
    report: VerificationReport | None = None
    message: str = ""


ProgressCallback = Callable[[UpdateProgress], None]
CancelCheck = Callable[[], bool]


class DatabaseUpdateService:
    """Orchestrates the whole download → verify → install → activate flow."""

    def __init__(
        self,
        manager: DatabaseManager,
        providers: ProviderManager,
        backups: BackupService,
        *,
        database_path: Path,
        downloads_dir: Path,
        backup_before_update: bool = True,
        journal: UpdateJournal | None = None,
    ) -> None:
        self._manager = manager
        self._providers = providers
        self._backups = backups
        self._database_path = database_path
        self._downloads_dir = downloads_dir
        self._backup_before_update = backup_before_update
        # The in-database update_history table is replaced along with the
        # database, so the durable record lives beside the configuration.
        self._journal = journal or UpdateJournal(
            database_path.parent.parent / "update-history.json"
        )

    # -- configuration ----------------------------------------------------
    def set_paths(self, database_path: Path, downloads_dir: Path) -> None:
        self._database_path = database_path
        self._downloads_dir = downloads_dir

    def set_backup_before_update(self, value: bool) -> None:
        self._backup_before_update = value

    @property
    def database_path(self) -> Path:
        return self._database_path

    @property
    def journal(self) -> UpdateJournal:
        return self._journal

    @property
    def database_installed(self) -> bool:
        return self._database_path.exists() and self._database_path.stat().st_size > 0

    # -- step 1: check ----------------------------------------------------
    def check(self, current_version: str | None) -> UpdateCheck:
        """Fetch the manifest and compare it with the installed version."""
        manifest, provider = self._providers.fetch_manifest()
        if not manifest.supported_by(APP_VERSION, SCHEMA_VERSION):
            return UpdateCheck(
                manifest=manifest,
                current_version=current_version,
                update_available=False,
                provider=provider.name,
                message=(
                    f"A newer database is published, but it needs a newer version of "
                    f"{APP_NAME}. Update the application to install it."
                ),
            )
        available = manifest.is_newer_than(current_version)
        return UpdateCheck(
            manifest=manifest,
            current_version=current_version,
            update_available=available,
            provider=provider.name,
            message=(
                f"Database {manifest.version} is available."
                if available
                else "Your database is up to date."
            ),
        )

    # -- step 2: download + install --------------------------------------
    def install(
        self,
        manifest: DatabaseManifest,
        *,
        provider: BaseProvider | None = None,
        progress: ProgressCallback | None = None,
        cancelled: CancelCheck | None = None,
        previous_version: str | None = None,
    ) -> UpdateOutcome:
        """Run the full pipeline for *manifest*.

        Raises :class:`~app.core.errors.BinTelError` subclasses on failure; the
        working database is always left in a usable state.
        """
        emit = progress or (lambda _: None)
        is_cancelled = cancelled or (lambda: False)
        self._downloads_dir.mkdir(parents=True, exist_ok=True)
        staging = self._downloads_dir / f"bintel-{manifest.version}.sqlite"
        backup_path: Path | None = None
        reopened = False

        try:
            # --- download -------------------------------------------------
            emit(
                UpdateProgress(
                    UpdateState.DOWNLOADING,
                    f"Downloading database {manifest.version}…",
                    total=manifest.database_size,
                )
            )
            last_emit = 0.0

            def on_chunk(state: DownloadProgress) -> None:
                nonlocal last_emit
                now = time.monotonic()
                # Throttle to ~10 Hz: the UI cannot use more, and each signal
                # costs a queued cross-thread event.
                if now - last_emit < 0.1 and state.received < state.total:
                    return
                last_emit = now
                emit(
                    UpdateProgress(
                        UpdateState.DOWNLOADING,
                        f"Downloading database {manifest.version}…",
                        received=state.received,
                        total=state.total,
                        speed=state.bytes_per_second,
                        eta_seconds=state.eta_seconds,
                    )
                )

            downloaded = self._providers.download(
                manifest,
                staging,
                provider=provider,
                progress=on_chunk,
                cancelled=is_cancelled,
            )
            downloaded_bytes = downloaded.stat().st_size

            # --- verify ---------------------------------------------------
            emit(UpdateProgress(UpdateState.VERIFYING, "Verifying download integrity…"))
            self._verify_checksum(downloaded, manifest, emit, is_cancelled)

            emit(UpdateProgress(UpdateState.VERIFYING, "Verifying database structure…"))
            report = verify_database(downloaded, quick=False)
            if not report.ok:
                raise DatabaseCorruptError(
                    "The downloaded database did not pass verification, so it was not "
                    "installed. Your existing database is unchanged.",
                    detail="; ".join(report.errors),
                )
            if report.schema_version is not None and report.schema_version > SCHEMA_VERSION:
                raise SchemaVersionError(
                    "The downloaded database needs a newer version of Bin-Tel.",
                    detail=f"schema {report.schema_version} > supported {SCHEMA_VERSION}",
                )

            # --- backup ---------------------------------------------------
            if self._backup_before_update and self.database_installed:
                emit(UpdateProgress(UpdateState.BACKING_UP, "Backing up your current database…"))
                backup_path = self._backups.create(previous_version)

            # --- install --------------------------------------------------
            emit(UpdateProgress(UpdateState.INSTALLING, "Installing the new database…"))
            was_open = self._manager.is_open
            self._manager.close()
            try:
                install_database(downloaded, self._database_path)
            except Exception:
                # Nothing was replaced, but reopen so the app keeps working.
                if was_open:
                    self._manager.open(self._database_path)
                raise

            self._manager.open(self._database_path)
            reopened = True

            # --- post-install ---------------------------------------------
            emit(UpdateProgress(UpdateState.INDEXING, "Building indexes…"))
            rebuild_indexes(self._manager.engine)
            stamp_schema_version(self._manager.engine, manifest.schema_version)
            analyze(self._manager.engine)
            self._stamp_metadata(manifest, report)
            self._record_history(
                previous_version,
                manifest.version,
                UpdateStatus.SUCCESS,
                bytes_downloaded=downloaded_bytes,
                backup_path=str(backup_path) if backup_path else None,
            )
            self._journal.record_success(
                manifest.version,
                previous_version,
                bytes_downloaded=downloaded_bytes,
                message=manifest.notes,
            )

            emit(UpdateProgress(UpdateState.COMPLETE, f"{APP_NAME} is ready."))
            logger.info(
                "Database update installed",
                extra={
                    "context": {
                        "version": manifest.version,
                        "previous": previous_version,
                        "bytes": downloaded_bytes,
                        "bins": report.bin_count,
                    }
                },
            )
            staging.unlink(missing_ok=True)
            return UpdateOutcome(
                success=True,
                version=manifest.version,
                previous_version=previous_version,
                bytes_downloaded=downloaded_bytes,
                backup_path=backup_path,
                report=report,
                message=f"Database {manifest.version} installed.",
            )

        except OperationCancelled:
            emit(UpdateProgress(UpdateState.CANCELLED, "Update cancelled."))
            self._rollback(backup_path, reopened)
            self._journal.record_failure(
                manifest.version,
                "The download was cancelled.",
                from_version=previous_version,
                status="cancelled",
            )
            raise
        except BinTelError as exc:
            emit(UpdateProgress(UpdateState.FAILED, exc.message))
            logger.error(
                "Database update failed",
                extra={"context": {"version": manifest.version, "error": exc.detail or exc.message}},
            )
            self._rollback(backup_path, reopened)
            self._journal.record_failure(
                manifest.version,
                exc.message,
                from_version=previous_version,
                status="rolled_back" if backup_path else "failed",
            )
            raise
        except Exception as exc:  # noqa: BLE001 - surfaced as a friendly error
            emit(UpdateProgress(UpdateState.FAILED, "The update could not be completed."))
            logger.exception("Unexpected failure during database update")
            self._rollback(backup_path, reopened)
            self._journal.record_failure(
                manifest.version,
                "The update could not be completed.",
                from_version=previous_version,
                status="rolled_back" if backup_path else "failed",
            )
            raise UpdateError(
                "The database update could not be completed. Your previous database "
                "has been kept.",
                detail=f"{type(exc).__name__}: {exc}",
            ) from exc

    # -- helpers ----------------------------------------------------------
    def _verify_checksum(
        self,
        path: Path,
        manifest: DatabaseManifest,
        emit: ProgressCallback,
        is_cancelled: CancelCheck,
    ) -> None:
        if not manifest.has_checksum:
            logger.warning("Manifest published no checksum; skipping digest verification")
            return
        total = path.stat().st_size

        def on_progress(done: int, _total: int) -> None:
            emit(
                UpdateProgress(
                    UpdateState.VERIFYING,
                    "Verifying download integrity…",
                    received=done,
                    total=total,
                )
            )

        try:
            actual = file_checksum(
                path,
                manifest.checksum_algorithm,
                progress=on_progress,
                cancelled=is_cancelled,
            )
        except InterruptedError as exc:
            raise OperationCancelled("Verification was cancelled.") from exc
        except ValueError as exc:
            raise ChecksumMismatchError(
                "The update server used a checksum format Bin-Tel does not support.",
                detail=str(exc),
            ) from exc

        if actual.lower() != manifest.checksum_digest.lower():
            path.unlink(missing_ok=True)
            raise ChecksumMismatchError(
                "The downloaded database did not match its published checksum and was "
                "discarded. This is usually a corrupted or interrupted download.",
                detail=f"expected {manifest.checksum_digest[:16]}…, got {actual[:16]}…",
            )

    def _stamp_metadata(self, manifest: DatabaseManifest, report: VerificationReport) -> None:
        from app.database.schema import write_metadata

        with self._manager.transaction() as session:
            write_metadata(
                session,
                {
                    DatabaseMetadata.VERSION: manifest.version,
                    DatabaseMetadata.SCHEMA_VERSION: manifest.schema_version,
                    DatabaseMetadata.RELEASE_DATE: (
                        manifest.release_date.isoformat() if manifest.release_date else None
                    ),
                    DatabaseMetadata.RECORD_COUNT: manifest.record_count or report.bin_count,
                    DatabaseMetadata.PUBLISHER: manifest.publisher,
                    DatabaseMetadata.CHECKSUM: manifest.checksum,
                    DatabaseMetadata.INSTALLED_AT: datetime.now(UTC).isoformat(),
                    DatabaseMetadata.LAST_VERIFIED: datetime.now(UTC).isoformat(),
                    DatabaseMetadata.NOTES: manifest.notes,
                },
            )

    def _record_history(
        self,
        from_version: str | None,
        to_version: str | None,
        status: UpdateStatus,
        *,
        bytes_downloaded: int = 0,
        backup_path: str | None = None,
    ) -> None:
        """Write the install into the newly activated database's own history."""
        from app.models.entities import UpdateHistory

        try:
            with self._manager.transaction() as session:
                session.add(
                    UpdateHistory(
                        from_version=from_version,
                        to_version=to_version,
                        status=status.value,
                        finished_at=datetime.now(UTC),
                        bytes_downloaded=bytes_downloaded,
                        backup_path=backup_path,
                        message="Installed by the Bin-Tel desktop application.",
                    )
                )
        except Exception:  # noqa: BLE001 - history must never fail an update
            logger.debug("Could not write the in-database update history", exc_info=True)

    def _rollback(self, backup_path: Path | None, reopened: bool) -> None:
        """Put the previous database back and make sure something is open."""
        try:
            if backup_path is not None and backup_path.exists():
                self._manager.close()
                self._backups.restore(backup_path)
                logger.warning(
                    "Rolled back to the previous database",
                    extra={"context": {"backup": str(backup_path)}},
                )
            if self.database_installed and not self._manager.is_open:
                self._manager.open(self._database_path)
            elif reopened and not self._manager.is_open:  # pragma: no cover - defensive
                self._manager.open(self._database_path)
        except Exception:  # noqa: BLE001 - rollback must never mask the cause
            logger.exception("Rollback after a failed update did not complete cleanly")

    # -- convenience ------------------------------------------------------
    def status_for(self, entry_status: UpdateStatus) -> str:
        return entry_status.value.replace("_", " ").title()
