"""Lifecycle of the local database: open, verify, maintain, relocate."""

from __future__ import annotations

import shutil
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from app.core.errors import DatabaseError, DatabaseMissingError
from app.core.logging_config import get_logger
from app.database.engine import DatabaseManager
from app.database.integrity import VerificationReport, verify_database
from app.database.schema import (
    analyze,
    create_schema,
    list_indexes,
    optimize,
    rebuild_indexes,
    vacuum,
    write_metadata,
)
from app.models.entities import DatabaseMetadata

logger = get_logger(__name__)


class DatabaseService:
    """Everything the Database page can do, without touching SQL directly."""

    def __init__(self, manager: DatabaseManager, database_path: Path) -> None:
        self._manager = manager
        self._database_path = database_path

    # -- state ------------------------------------------------------------
    @property
    def manager(self) -> DatabaseManager:
        return self._manager

    @property
    def path(self) -> Path:
        return self._database_path

    @property
    def is_installed(self) -> bool:
        try:
            return self._database_path.exists() and self._database_path.stat().st_size > 0
        except OSError:  # pragma: no cover - unreadable volume
            return False

    @property
    def is_open(self) -> bool:
        return self._manager.is_open

    def set_path(self, path: Path) -> None:
        """Point at a different database file, reopening if one was open."""
        was_open = self._manager.is_open
        self._manager.close()
        self._database_path = path
        if was_open and self.is_installed:
            self._manager.open(path)

    # -- lifecycle --------------------------------------------------------
    def open(self, *, create_if_missing: bool = False) -> None:
        if not self.is_installed and not create_if_missing:
            raise DatabaseMissingError(
                "The Bin-Tel database has not been downloaded yet.",
                detail=f"Missing {self._database_path}",
            )
        self._manager.open(self._database_path, create_if_missing=create_if_missing)
        if create_if_missing:
            create_schema(self._manager.engine)

    def close(self) -> None:
        if self._manager.is_open:
            try:
                optimize(self._manager.engine)
            except Exception:  # noqa: BLE001 - shutdown must not fail
                logger.debug("PRAGMA optimize on shutdown failed", exc_info=True)
        self._manager.close()

    def reopen(self) -> None:
        self._manager.open(self._database_path)

    # -- maintenance ------------------------------------------------------
    def verify(self, *, quick: bool = False, record: bool = True) -> VerificationReport:
        """Run verification against the file on disk."""
        report = verify_database(self._database_path, quick=quick)
        if report.ok and record and self._manager.is_open:
            try:
                with self._manager.transaction() as session:
                    write_metadata(
                        session,
                        {DatabaseMetadata.LAST_VERIFIED: datetime.now(UTC).isoformat()},
                    )
            except Exception:  # noqa: BLE001 - a read-only DB must still verify
                logger.debug("Could not record the verification timestamp", exc_info=True)
        logger.info(
            "Database verification finished",
            extra={"context": {"ok": report.ok, "quick": quick, "bins": report.bin_count}},
        )
        return report

    def reindex(self) -> list[str]:
        self._require_open()
        created = rebuild_indexes(self._manager.engine)
        analyze(self._manager.engine)
        return created

    def compact(self) -> None:
        """VACUUM the database. Can take a while on a large file."""
        self._require_open()
        vacuum(self._manager.engine)

    def indexes(self) -> list[str]:
        self._require_open()
        return list_indexes(self._manager.engine)

    def size_bytes(self) -> int | None:
        try:
            total = self._database_path.stat().st_size
        except OSError:
            return None
        for suffix in ("-wal", "-shm"):
            sidecar = self._database_path.with_name(self._database_path.name + suffix)
            if sidecar.exists():
                total += sidecar.stat().st_size
        return total

    # -- relocation -------------------------------------------------------
    def move_to(
        self, new_directory: Path, *, progress: Callable[[str], None] | None = None
    ) -> Path:
        """Move the database to *new_directory*, keeping the old copy until done."""
        new_directory = Path(new_directory).expanduser()
        new_directory.mkdir(parents=True, exist_ok=True)
        destination = new_directory / self._database_path.name
        if destination == self._database_path:
            return destination
        if progress:
            progress("Closing the database…")
        self._manager.close()
        try:
            if self.is_installed:
                if progress:
                    progress("Copying the database…")
                shutil.copy2(self._database_path, destination)
                report = verify_database(destination, quick=True, require_content=False)
                if not report.ok:
                    destination.unlink(missing_ok=True)
                    raise DatabaseError(
                        "The database could not be moved because the copy failed verification.",
                        detail="; ".join(report.errors),
                    )
                old = self._database_path
                self._database_path = destination
                self._manager.open(destination)
                old.unlink(missing_ok=True)
                for suffix in ("-wal", "-shm"):
                    old.with_name(old.name + suffix).unlink(missing_ok=True)
            else:
                self._database_path = destination
        except OSError as exc:
            # Put the original back into service before reporting the failure.
            if self.is_installed:
                self._manager.open(self._database_path)
            raise DatabaseError(
                "Bin-Tel could not move the database to that folder.", detail=str(exc)
            ) from exc
        logger.info("Database relocated", extra={"context": {"path": str(destination)}})
        return destination

    def _require_open(self) -> None:
        if not self._manager.is_open:
            raise DatabaseError(
                "The database is not open.", detail="DatabaseService operation before open()"
            )
