"""The first-run experience.

Bin-Tel does not ship the production database inside the installer, so the very
first launch downloads it. This window is what the user sees until that has
finished: a branded welcome, what will be downloaded, live progress, and clear
recovery when something goes wrong.

The main window is never shown before this completes.
"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QDialog,
    QFileDialog,
    QFrame,
    QLabel,
    QPushButton,
    QWidget,
)

from app.core.constants import APP_NAME, APP_VERSION
from app.core.context import AppContext
from app.core.errors import OfflineError
from app.core.logging_config import get_logger
from app.core.paths import free_space, human_size
from app.providers.local_provider import LocalPackageProvider
from app.providers.manifest import DatabaseManifest
from app.services.update_service import UpdateCheck, UpdateOutcome, UpdateProgress
from app.ui.widgets.brand import BrandSplash
from app.ui.widgets.cards import FieldRow
from app.ui.widgets.progress_panel import ProgressPanel
from app.ui.widgets.states import StateBanner, StateKind
from app.utils.formatting import format_bytes, format_number
from app.utils.qt_helpers import centered_paragraph, expanding_spacer, grid, hbox, vbox
from app.workers.base import run_in_background
from app.workers.update_worker import UpdateCheckWorker, UpdateInstallWorker

logger = get_logger(__name__)


class FirstRunWindow(QDialog):
    """``Welcome to Bin-Tel`` → download → verify → install → ``Get Started``."""

    completed = pyqtSignal()

    def __init__(self, context: AppContext, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.context = context
        self._manifest: DatabaseManifest | None = None
        self._worker: UpdateInstallWorker | None = None
        self._ready = False

        self.setWindowTitle(f"Welcome to {APP_NAME}")
        self.setObjectName("FirstRunSurface")
        self.setMinimumSize(620, 720)
        self.setModal(True)

        outer = vbox(self, margins=(30, 26, 30, 26), spacing=18)

        self.splash = BrandSplash(460, self)
        outer.addWidget(self.splash, 0, Qt.AlignmentFlag.AlignHCenter)

        heading = QLabel(f"Welcome to {APP_NAME}", self)
        heading.setProperty("role", "pageTitle")
        heading.setAlignment(Qt.AlignmentFlag.AlignCenter)
        outer.addWidget(heading)

        message_block = centered_paragraph(
            f"{APP_NAME} needs to download its local intelligence database before you "
            "can begin. Once it is installed, every lookup runs offline from your own "
            "machine.",
            self,
            max_width=520,
        )
        self.message = message_block.label  # type: ignore[attr-defined]
        outer.addWidget(message_block)

        self.banner = StateBanner("", StateKind.INFO, self)
        self.banner.hide()
        outer.addWidget(self.banner)

        # -- package summary ------------------------------------------------
        self.panel = QFrame(self)
        self.panel.setObjectName("FirstRunPanel")
        panel_layout = vbox(self.panel, margins=(22, 20, 22, 20), spacing=16)

        holder = QWidget(self.panel)
        self._facts = grid(holder, spacing=14)
        self.version_row = FieldRow("Database version", "Checking…", holder, selectable=False)
        self.size_row = FieldRow("Estimated size", "—", holder, selectable=False)
        self.records_row = FieldRow("Records", "—", holder, selectable=False)
        self.storage_row = FieldRow("Required storage", "—", holder, selectable=False)
        self.connection_row = FieldRow("Internet connection", "Checking…", holder, selectable=False)
        self.location_row = FieldRow(
            "Install location", str(self.context.database_path.parent), holder, selectable=False
        )
        for index, row in enumerate(
            (
                self.version_row,
                self.size_row,
                self.records_row,
                self.storage_row,
                self.connection_row,
                self.location_row,
            )
        ):
            self._facts.addWidget(row, index // 2, index % 2)
        self._facts.setColumnStretch(0, 1)
        self._facts.setColumnStretch(1, 1)
        panel_layout.addWidget(holder)

        self.progress = ProgressPanel(self.panel, tall=True)
        self.progress.setVisible(False)
        panel_layout.addWidget(self.progress)
        outer.addWidget(self.panel)

        outer.addItem(expanding_spacer(horizontal=False))

        # -- actions ---------------------------------------------------------
        actions = hbox(spacing=10)

        self.local_button = QPushButton("Use a local package…", self)
        self.local_button.setProperty("variant", "ghost")
        self.local_button.setToolTip(
            "Install from a database package you already have — useful on a machine "
            "without internet access."
        )
        self.local_button.clicked.connect(self._choose_local_package)
        actions.addWidget(self.local_button)

        actions.addItem(expanding_spacer())

        self.quit_button = QPushButton("Quit", self)
        self.quit_button.clicked.connect(self.reject)
        actions.addWidget(self.quit_button)

        self.cancel_button = QPushButton("Cancel", self)
        self.cancel_button.setProperty("variant", "danger")
        self.cancel_button.setVisible(False)
        self.cancel_button.clicked.connect(self._cancel)
        actions.addWidget(self.cancel_button)

        self.primary_button = QPushButton("Download Database", self)
        self.primary_button.setProperty("variant", "primary")
        self.primary_button.setMinimumWidth(190)
        self.primary_button.setDefault(True)
        self.primary_button.setEnabled(False)
        self.primary_button.clicked.connect(self._on_primary)
        actions.addWidget(self.primary_button)

        outer.addLayout(actions)

        QTimer.singleShot(120, self.check)

    # -- discovery --------------------------------------------------------
    def check(self) -> None:
        """Fetch the manifest so the user knows what they are about to download."""
        self.connection_row.set_value("Checking…")
        self.version_row.set_value("Checking…")
        self.primary_button.setEnabled(False)
        self.progress.setVisible(True)
        self.progress.set_indeterminate("Contacting the database server…")

        worker = UpdateCheckWorker(self.context.updates, None)
        worker.signals.result.connect(self._on_manifest)
        worker.signals.failed.connect(self._on_check_failed)
        run_in_background(worker)

    def _on_manifest(self, check: UpdateCheck) -> None:
        self.progress.setVisible(False)
        manifest = check.manifest
        if manifest is None:
            self._on_check_failed(OfflineError("No database package is available yet."))
            return

        self._manifest = manifest
        self.version_row.set_value(manifest.version)
        self.size_row.set_value(format_bytes(manifest.database_size))
        self.records_row.set_value(
            format_number(manifest.record_count) if manifest.record_count else "Unknown"
        )
        self.connection_row.set_value("Connected")

        available = free_space(self.context.database_path.parent)
        # The install needs room for the download plus the installed copy.
        needed = int(manifest.database_size * 2.2) if manifest.database_size else 0
        if available is None:
            self.storage_row.set_value(f"{human_size(needed)} recommended")
        else:
            self.storage_row.set_value(
                f"{human_size(needed)} needed · {human_size(available)} free"
            )
            if needed and available < needed:
                self.banner.show_message(
                    "There may not be enough free disk space for this database.",
                    StateKind.WARNING,
                )

        self.primary_button.setEnabled(True)
        self.primary_button.setText("Download Database")

    def _on_check_failed(self, exc: BaseException) -> None:
        self.progress.setVisible(False)
        self.connection_row.set_value("Unavailable")
        self.version_row.set_value("Unknown")
        offline = isinstance(exc, OfflineError)
        self.banner.show_message(
            (
                "Bin-Tel could not reach the database server. Check your connection and "
                "try again, or install from a package you already have."
                if offline
                else "The database server did not return usable update information."
            ),
            StateKind.WARNING,
            action_text="Try again",
        )
        try:
            self.banner.action_triggered.disconnect()
        except TypeError:
            pass
        self.banner.action_triggered.connect(self.check)
        self.primary_button.setText("Try Again")
        self.primary_button.setEnabled(True)
        logger.warning("First-run manifest check failed: %s", exc)

    # -- install ----------------------------------------------------------
    def _on_primary(self) -> None:
        if self._ready:
            self.accept()
            return
        if self._manifest is None:
            self.check()
            return
        self._start_install()

    def _start_install(self) -> None:
        if self._manifest is None or self._worker is not None:
            return
        self.banner.hide()
        self.progress.setVisible(True)
        self.progress.reset("Preparing…")
        self.primary_button.setEnabled(False)
        self.local_button.setEnabled(False)
        self.quit_button.setVisible(False)
        self.cancel_button.setVisible(True)

        worker = UpdateInstallWorker(self.context.updates, self._manifest)
        worker.signals.stage.connect(self.progress.update_progress)
        worker.signals.result.connect(self._on_installed)
        worker.signals.failed.connect(self._on_install_failed)
        worker.signals.cancelled.connect(self._on_cancelled)
        self._worker = worker
        run_in_background(worker)

    def _on_installed(self, outcome: UpdateOutcome) -> None:
        self._worker = None
        self._ready = True
        self.cancel_button.setVisible(False)
        self.local_button.setEnabled(True)

        state = self.context.config.state
        state.first_run_completed = True
        self.context.config.save_state()
        self.context.config.mark_update_checked(outcome.version)

        report = outcome.report
        if report is not None:
            self.records_row.set_value(format_number(report.bin_count))
            self.size_row.set_value(format_bytes(report.size_bytes))
        self.version_row.set_value(outcome.version or APP_VERSION)

        self.message.setText(
            f"{APP_NAME} is ready. Your database is installed locally, so lookups work "
            "even without an internet connection."
        )
        self.banner.show_message(
            f"Database {outcome.version} installed and verified.", StateKind.SUCCESS
        )
        self.primary_button.setText("Get Started")
        self.primary_button.setEnabled(True)
        self.primary_button.setFocus()
        logger.info(
            "First-run setup completed",
            extra={"context": {"version": outcome.version, "bytes": outcome.bytes_downloaded}},
        )

    def _on_install_failed(self, exc: BaseException) -> None:
        self._worker = None
        self.cancel_button.setVisible(False)
        self.quit_button.setVisible(True)
        self.local_button.setEnabled(True)
        self.primary_button.setEnabled(True)
        self.primary_button.setText("Retry Download")
        self.progress.set_error("The database could not be installed.")

        from app.core.errors import friendly_message
        from app.ui.dialogs.error_dialog import ErrorDialog

        self.banner.show_message(friendly_message(exc), StateKind.DANGER)
        if ErrorDialog.show_for(exc, self):
            self._start_install()

    def _on_cancelled(self) -> None:
        self._worker = None
        self.cancel_button.setVisible(False)
        self.quit_button.setVisible(True)
        self.local_button.setEnabled(True)
        self.primary_button.setEnabled(True)
        self.primary_button.setText("Download Database")
        self.progress.reset("Download cancelled. Nothing was installed.")
        self.banner.show_message(
            "The download was cancelled. No partial database was left behind.",
            StateKind.WARNING,
        )

    def _cancel(self) -> None:
        if self._worker is not None:
            self._worker.cancel()
            self.progress.set_indeterminate("Cancelling…")

    # -- offline install --------------------------------------------------
    def _choose_local_package(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select a Bin-Tel database manifest",
            str(Path.home()),
            "Database manifest (*.json)",
        )
        if not path:
            return
        provider = LocalPackageProvider(Path(path))
        self.context.providers.register(provider, first=True)
        self.context.config.settings.database.manifest_url = Path(path).as_uri()
        self.context.config.save_settings()
        self.banner.show_message("Using the selected local package.", StateKind.INFO)
        self.check()

    # -- dialog behaviour --------------------------------------------------
    def accept(self) -> None:
        if self._ready:
            self.completed.emit()
        super().accept()

    def reject(self) -> None:
        if self._worker is not None:
            self._cancel()
            return
        super().reject()

    def refresh_theme(self) -> None:
        self.splash.refresh()

    @property
    def database_ready(self) -> bool:
        return self._ready

    @staticmethod
    def _unused(_: UpdateProgress) -> None:  # pragma: no cover
        return None
