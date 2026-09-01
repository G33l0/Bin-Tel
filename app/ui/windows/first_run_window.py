"""The first-run experience.

Bin-Tel builds its database from a BIN list the user maintains, so the first
launch is a *build*, not a download. This window says so, shows what it found
in the list, and builds — offline, from a file on this machine.

Downloading a prepared package is still possible for anyone who has a manifest
to point at, but it is the secondary path. Making it the primary one produced
exactly the wall this window now exists to avoid: an application that cannot
start because a server it does not need is unreachable.

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
from app.core.errors import BinTelError, OfflineError
from app.core.logging_config import get_logger
from app.core.paths import free_space, human_size
from app.providers.local_provider import LocalPackageProvider
from app.providers.manifest import DatabaseManifest
from app.services.bin_list import read_bin_list
from app.services.rebuild_service import RebuildOutcome
from app.services.update_service import UpdateCheck, UpdateOutcome, UpdateProgress
from app.ui.widgets.brand import BrandSplash
from app.ui.widgets.cards import FieldRow
from app.ui.widgets.progress_panel import ProgressPanel
from app.ui.widgets.states import StateBanner, StateKind
from app.utils.formatting import format_bytes, format_number
from app.utils.qt_helpers import centered_paragraph, expanding_spacer, grid, hbox, vbox
from app.workers.base import run_in_background
from app.workers.maintenance_worker import RebuildWorker
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
            f"{APP_NAME} builds its database from your own BIN list — a plain CSV "
            "file on this machine. Point it at your list and it will build one now. "
            "Every lookup then runs offline.",
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
        self.list_row = FieldRow("Your BIN list", "Looking…", holder, selectable=False)
        self.records_row = FieldRow("BINs ready to build", "—", holder, selectable=False)
        self.version_row = FieldRow("Database version", "Not built yet", holder, selectable=False)
        self.size_row = FieldRow("Estimated size", "—", holder, selectable=False)
        self.storage_row = FieldRow("Free space", "—", holder, selectable=False)
        self.location_row = FieldRow(
            "Install location", str(self.context.database_path.parent), holder, selectable=False
        )
        for index, row in enumerate(
            (
                self.list_row,
                self.records_row,
                self.version_row,
                self.size_row,
                self.storage_row,
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

        self.choose_button = QPushButton("Choose a CSV file…", self)
        self.choose_button.setProperty("variant", "ghost")
        self.choose_button.setToolTip(
            "Point Bin-Tel at a BIN list anywhere on this machine. It needs a "
            "`bin,bank` header and one line per BIN."
        )
        self.choose_button.clicked.connect(self._choose_bin_list)
        actions.addWidget(self.choose_button)

        self.open_list_button = QPushButton("Open my list", self)
        self.open_list_button.setProperty("variant", "ghost")
        self.open_list_button.setToolTip("Open the list in your text editor to add BINs.")
        self.open_list_button.clicked.connect(self._open_bin_list)
        actions.addWidget(self.open_list_button)

        self.local_button = QPushButton("Use a package…", self)
        self.local_button.setProperty("variant", "ghost")
        self.local_button.setToolTip(
            "Install from a prepared database package (a .json manifest) instead of "
            "building from a list."
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

        self.primary_button = QPushButton("Build my database", self)
        self.primary_button.setProperty("variant", "primary")
        self.primary_button.setMinimumWidth(190)
        self.primary_button.setDefault(True)
        self.primary_button.setEnabled(False)
        self.primary_button.clicked.connect(self._on_primary)
        actions.addWidget(self.primary_button)

        outer.addLayout(actions)

        # Read the list, not the network. Nothing here needs a server.
        QTimer.singleShot(120, self.inspect_list)

    # -- the BIN list ------------------------------------------------------
    @property
    def list_path(self) -> Path:
        return self.context.config.bin_list_path()

    def inspect_list(self) -> None:
        """Read the list and say what can be built from it. No network at all.

        Does nothing once a database exists: the startup timer that first calls
        this can fire again while a build is running, and re-reading the list
        would reset a finished window back to "Build my database".
        """
        if self._ready:
            return
        path = self.list_path
        self.list_row.set_value(path.name)
        self.list_row.setToolTip(str(path))
        self.location_row.set_value(str(self.context.database_path.parent))

        available = free_space(self.context.database_path.parent)
        self.storage_row.set_value(
            human_size(available) if available is not None else "Unknown"
        )

        try:
            report = read_bin_list(path)
        except BinTelError as exc:
            self.records_row.set_value("None yet")
            self.size_row.set_value("—")
            self.primary_button.setEnabled(False)
            self._advise(
                f"{exc.message} {exc.detail or ''}".strip()
                + "  Open your list, add a line per BIN, then press Try again.",
                action="Try again",
                handler=self.inspect_list,
            )
            return

        self.records_row.set_value(f"{report.distinct_bins:,}")
        # Roughly what a built database costs: enough to be useful, never
        # presented as exact.
        self.size_row.set_value(human_size(max(64_000, report.accepted * 900)))
        self.banner.hide()
        self.primary_button.setEnabled(True)
        self.primary_button.setText("Build my database")
        summary = report.summary
        if report.rejected:
            self._advise(
                f"{report.rejected:,} line(s) could not be read and will be skipped: "
                f"{report.problems[0]}",
                kind=StateKind.WARNING,
            )
        logger.info("First run read the BIN list: %s", summary)

    def _advise(
        self,
        message: str,
        *,
        kind: StateKind = StateKind.WARNING,
        action: str = "",
        handler=None,
    ) -> None:
        self.banner.show_message(message, kind, action_text=action)
        try:
            self.banner.action_triggered.disconnect()
        except TypeError:
            pass
        if action and handler is not None:
            self.banner.action_triggered.connect(handler)

    def _choose_bin_list(self) -> None:
        """Point at a CSV anywhere on this machine."""
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Choose your BIN list",
            str(self.list_path.parent),
            "BIN list (*.csv *.tsv *.txt);;All files (*)",
        )
        if not path:
            return
        self.context.config.set_bin_list_path(Path(path))
        self.context.config.save_settings()
        self.inspect_list()

    def _open_bin_list(self) -> None:
        """Open the list in whatever the platform uses for text files."""
        from app.utils.qt_helpers import open_path

        open_path(self.list_path)
        self._advise(
            "Add a line per BIN — for example `414720,Chase Bank` — then save the "
            "file and press Try again.",
            kind=StateKind.INFO,
            action="Try again",
            handler=self.inspect_list,
        )

    def _build_from_list(self) -> None:
        self.banner.hide()
        self.progress.setVisible(True)
        self.progress.set_indeterminate("Building your database…")
        self._set_busy(True)
        worker = RebuildWorker(self.context.rebuilds, self.list_path)
        worker.signals.progress.connect(
            lambda message: self.progress.set_indeterminate(str(message))
        )
        worker.signals.result.connect(self._on_built)
        worker.signals.failed.connect(self._on_build_failed)
        run_in_background(worker)

    def _on_built(self, outcome: RebuildOutcome) -> None:
        self.progress.setVisible(False)
        self._set_busy(False)
        self._ready = True
        self.version_row.set_value(outcome.version)
        self.records_row.set_value(f"{outcome.distinct_bins:,}")
        self._advise(
            f"Your database is ready — {outcome.summary}",
            kind=StateKind.SUCCESS,
        )
        self.primary_button.setText("Get Started")
        self.primary_button.setEnabled(True)

    def _on_build_failed(self, exc: BaseException) -> None:
        self.progress.setVisible(False)
        self._set_busy(False)
        message = getattr(exc, "message", None) or str(exc)
        detail = getattr(exc, "detail", None) or ""
        self._advise(
            f"{message} {detail}".strip(),
            action="Try again",
            handler=self.inspect_list,
        )
        self.primary_button.setEnabled(True)
        logger.warning("First-run build failed: %s", exc)

    def _set_busy(self, busy: bool) -> None:
        for button in (
            self.primary_button,
            self.choose_button,
            self.open_list_button,
            self.local_button,
        ):
            button.setEnabled(not busy)

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
        if self._manifest is not None:
            # A package was chosen deliberately, so install that instead.
            self._start_install()
            return
        self._build_from_list()

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
            "Database manifest (*.json);;All files (*)",
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
