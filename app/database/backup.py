"""Backup and restore of the active database.

Backups use SQLite's online backup API rather than a file copy, so a snapshot
taken while the application is running is always internally consistent. Every
update creates one first (when enabled), and a failed activation restores the
most recent snapshot automatically.
"""

from __future__ import annotations

import shutil
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from app.core.errors import DatabaseError
from app.core.logging_config import get_logger
from app.database.integrity import verify_database

logger = get_logger(__name__)

BACKUP_SUFFIX = ".sqlite"
BACKUP_PREFIX = "bintel-backup-"


@dataclass(frozen=True, slots=True)
class BackupInfo:
    """A backup file on disk."""

    path: Path
    created_at: datetime
    size_bytes: int
    version: str | None = None

    @property
    def name(self) -> str:
        return self.path.name


def _timestamp(when: datetime | None = None) -> str:
    return (when or datetime.now(UTC)).strftime("%Y%m%d-%H%M%S")


def backup_filename(version: str | None = None, when: datetime | None = None) -> str:
    tag = f"-v{version}" if version else ""
    return f"{BACKUP_PREFIX}{_timestamp(when)}{tag}{BACKUP_SUFFIX}"


def create_backup(
    source: Path,
    backups_dir: Path,
    *,
    version: str | None = None,
    progress: Callable[[int, int], None] | None = None,
) -> Path:
    """Snapshot *source* into *backups_dir* and return the new file's path."""
    if not source.exists():
        raise DatabaseError(
            "There is no database to back up yet.", detail=f"Missing source {source}"
        )
    backups_dir.mkdir(parents=True, exist_ok=True)
    target = backups_dir / backup_filename(version)

    def _progress(status: int, remaining: int, total: int) -> None:
        if progress is not None and total:
            progress(total - remaining, total)

    source_connection: sqlite3.Connection | None = None
    target_connection: sqlite3.Connection | None = None
    try:
        source_connection = sqlite3.connect(f"file:{source}?mode=ro", uri=True, timeout=30.0)
        target_connection = sqlite3.connect(target, timeout=30.0)
        source_connection.backup(target_connection, pages=2048, progress=_progress)
        target_connection.commit()
    except sqlite3.DatabaseError as exc:
        target.unlink(missing_ok=True)
        raise DatabaseError(
            "Bin-Tel could not create a backup of the database.", detail=str(exc)
        ) from exc
    finally:
        if source_connection is not None:
            source_connection.close()
        if target_connection is not None:
            target_connection.close()

    logger.info(
        "Database backup created",
        extra={"context": {"path": str(target), "bytes": target.stat().st_size}},
    )
    return target


def list_backups(backups_dir: Path) -> list[BackupInfo]:
    """Every backup in *backups_dir*, newest first."""
    if not backups_dir.exists():
        return []
    entries: list[BackupInfo] = []
    for path in backups_dir.glob(f"{BACKUP_PREFIX}*{BACKUP_SUFFIX}"):
        try:
            stat = path.stat()
        except OSError:  # pragma: no cover - vanished mid-scan
            continue
        version = None
        stem = path.stem
        if "-v" in stem:
            version = stem.rsplit("-v", 1)[1] or None
        entries.append(
            BackupInfo(
                path=path,
                created_at=datetime.fromtimestamp(stat.st_mtime, tz=UTC),
                size_bytes=stat.st_size,
                version=version,
            )
        )
    return sorted(entries, key=lambda item: item.created_at, reverse=True)


def latest_backup(backups_dir: Path) -> BackupInfo | None:
    backups = list_backups(backups_dir)
    return backups[0] if backups else None


def prune_backups(backups_dir: Path, keep: int) -> list[Path]:
    """Delete all but the *keep* newest backups. Returns what was removed."""
    removed: list[Path] = []
    for backup in list_backups(backups_dir)[max(0, keep) :]:
        try:
            backup.path.unlink()
            removed.append(backup.path)
        except OSError:  # pragma: no cover - permissions
            logger.warning("Could not delete old backup %s", backup.path)
    if removed:
        logger.info("Pruned old backups", extra={"context": {"removed": len(removed)}})
    return removed


def restore_backup(backup: Path, destination: Path, *, verify: bool = True) -> None:
    """Put *backup* back in place as the active database.

    The backup is verified *before* the live file is touched, so a damaged
    snapshot can never take out a working installation.
    """
    if not backup.exists():
        raise DatabaseError(
            "That backup file no longer exists.", detail=f"Missing backup {backup}"
        )
    if verify:
        report = verify_database(backup, quick=True, require_content=False)
        if not report.ok:
            raise DatabaseError(
                "That backup did not pass verification, so it was not restored.",
                detail="; ".join(report.errors),
            )

    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = destination.with_suffix(destination.suffix + ".restore-tmp")
    try:
        shutil.copy2(backup, staging)
        _clear_sidecars(destination)
        staging.replace(destination)
    except OSError as exc:
        staging.unlink(missing_ok=True)
        raise DatabaseError(
            "Bin-Tel could not restore the backup.", detail=str(exc)
        ) from exc
    logger.info(
        "Database restored from backup", extra={"context": {"backup": str(backup)}}
    )


def _clear_sidecars(database_path: Path) -> None:
    """Remove stale ``-wal`` / ``-shm`` files that belong to the old file."""
    for suffix in ("-wal", "-shm"):
        sidecar = database_path.with_name(database_path.name + suffix)
        sidecar.unlink(missing_ok=True)


def install_database(source: Path, destination: Path) -> None:
    """Atomically move a verified package into place as the active database.

    ``Path.replace`` is atomic within a filesystem; the staging copy guarantees
    that even a cross-device move never leaves a half-written database behind.
    """
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = destination.with_suffix(destination.suffix + ".incoming")
    try:
        shutil.copy2(source, staging)
        _clear_sidecars(destination)
        staging.replace(destination)
    except OSError as exc:
        staging.unlink(missing_ok=True)
        raise DatabaseError(
            "Bin-Tel could not install the downloaded database.", detail=str(exc)
        ) from exc
