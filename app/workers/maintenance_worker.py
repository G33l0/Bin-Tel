"""Workers for verification, backup, restore, reindexing and imports."""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import pyqtSignal

from app.database.integrity import VerificationReport
from app.importers.base import BaseImporter, ImportSummary
from app.services.backup_service import BackupService
from app.services.database_service import DatabaseService
from app.workers.base import Worker, WorkerSignals


class VerifyWorker(Worker[VerificationReport]):
    """Full or quick database verification."""

    def __init__(self, service: DatabaseService, *, quick: bool = False) -> None:
        super().__init__(service.verify, quick=quick)
        self.quick = quick


class BackupWorker(Worker[Path]):
    """Creates a database snapshot, reporting byte progress."""

    def __init__(self, service: BackupService, version: str | None = None) -> None:
        super().__init__(self._execute)
        self._service = service
        self._version = version

    def _execute(self) -> Path:
        def on_progress(done: int, total: int) -> None:
            self.signals.progress.emit((done, total))

        return self._service.create(self._version, progress=on_progress)


class RestoreWorker(Worker[None]):
    """Restores a snapshot after verifying it."""

    def __init__(self, service: BackupService, backup: Path) -> None:
        super().__init__(service.restore, backup)
        self.backup = backup


class ReindexWorker(Worker[list]):
    """Rebuilds indexes and refreshes the query planner's statistics."""

    def __init__(self, service: DatabaseService) -> None:
        super().__init__(service.reindex)


class ImportSignals(WorkerSignals):
    row_progress = pyqtSignal(int, str)


class ImportWorker(Worker[ImportSummary]):
    """Streams a source file into the database."""

    def __init__(self, importer: BaseImporter, manager: object) -> None:
        super().__init__(self._execute)
        self.signals = ImportSignals()
        self._importer = importer
        self._manager = manager

    def _execute(self) -> ImportSummary:
        def on_progress(processed: int, message: str) -> None:
            self.signals.row_progress.emit(processed, message)

        return self._importer.run(
            self._manager,  # type: ignore[arg-type]
            progress=on_progress,
            cancelled=self.token,
        )
