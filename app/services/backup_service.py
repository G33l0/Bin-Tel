"""Backup and restore, wrapped for the UI."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from app.core.logging_config import get_logger
from app.database.backup import (
    BackupInfo,
    create_backup,
    latest_backup,
    list_backups,
    prune_backups,
    restore_backup,
)

logger = get_logger(__name__)


class BackupService:
    """Creates, lists, prunes and restores database snapshots."""

    def __init__(self, database_path: Path, backups_dir: Path, *, keep: int = 3) -> None:
        self._database_path = database_path
        self._backups_dir = backups_dir
        self._keep = keep

    def set_paths(self, database_path: Path, backups_dir: Path) -> None:
        self._database_path = database_path
        self._backups_dir = backups_dir

    @property
    def backups_dir(self) -> Path:
        return self._backups_dir

    def create(
        self,
        version: str | None = None,
        *,
        progress: Callable[[int, int], None] | None = None,
        prune: bool = True,
    ) -> Path:
        path = create_backup(
            self._database_path, self._backups_dir, version=version, progress=progress
        )
        if prune:
            prune_backups(self._backups_dir, self._keep)
        return path

    def list(self) -> list[BackupInfo]:
        return list_backups(self._backups_dir)

    def latest(self) -> BackupInfo | None:
        return latest_backup(self._backups_dir)

    def restore(self, backup: Path) -> None:
        restore_backup(backup, self._database_path)

    def restore_latest(self) -> Path | None:
        """Roll back to the most recent snapshot. Used after a failed update."""
        newest = self.latest()
        if newest is None:
            return None
        self.restore(newest.path)
        return newest.path

    def set_retention(self, keep: int) -> None:
        self._keep = max(1, keep)
