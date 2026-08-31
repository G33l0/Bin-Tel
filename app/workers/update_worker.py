"""Workers for checking, downloading and installing database updates."""

from __future__ import annotations

from PyQt6.QtCore import pyqtSignal

from app.core.logging_config import get_logger
from app.providers.manifest import DatabaseManifest
from app.services.update_service import (
    DatabaseUpdateService,
    UpdateCheck,
    UpdateOutcome,
    UpdateProgress,
)
from app.workers.base import Worker, WorkerSignals

logger = get_logger(__name__)


class UpdateSignals(WorkerSignals):
    """Adds a typed progress channel on top of the standard worker signals."""

    stage = pyqtSignal(object)  # UpdateProgress


class UpdateCheckWorker(Worker[UpdateCheck]):
    """Fetches the manifest and compares versions — a few hundred bytes."""

    def __init__(self, service: DatabaseUpdateService, current_version: str | None) -> None:
        super().__init__(service.check, current_version)
        self.current_version = current_version


class UpdateInstallWorker(Worker[UpdateOutcome]):
    """Runs the whole download → verify → install → activate pipeline."""

    def __init__(
        self,
        service: DatabaseUpdateService,
        manifest: DatabaseManifest,
        *,
        previous_version: str | None = None,
    ) -> None:
        super().__init__(self._execute)
        self.signals = UpdateSignals()
        self._service = service
        self._manifest = manifest
        self._previous_version = previous_version

    @property
    def manifest(self) -> DatabaseManifest:
        return self._manifest

    def _execute(self) -> UpdateOutcome:
        def on_progress(progress: UpdateProgress) -> None:
            # Emitted from the worker thread; Qt queues it to the GUI thread.
            self.signals.stage.emit(progress)

        return self._service.install(
            self._manifest,
            progress=on_progress,
            cancelled=self.token,
            previous_version=self._previous_version,
        )
