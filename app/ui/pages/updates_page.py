"""Database update page — check, download, install, all without blocking."""

from __future__ import annotations

from datetime import UTC, datetime

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QLabel, QPushButton, QWidget

from app.core.constants import UNKNOWN_DISPLAY
from app.core.errors import OfflineError, OperationCancelled
from app.providers.manifest import DatabaseManifest
from app.services.update_service import UpdateCheck, UpdateOutcome, UpdateProgress
from app.ui.pages.base_page import BasePage
from app.ui.widgets.cards import Card, CardGrid, MetricCard, SectionHeader
from app.ui.widgets.progress_panel import ProgressPanel
from app.ui.widgets.states import StateBanner, StateKind
from app.utils.formatting import (
    format_bytes,
    format_datetime,
    format_number,
    format_relative,
)
from app.utils.qt_helpers import hbox
from app.workers.base import run_in_background
from app.workers.update_worker import UpdateCheckWorker, UpdateInstallWorker


class UpdatesPage(BasePage):
    """The user-controlled update surface. Nothing here is forced."""

    key = "updates"
    title = "Updates"
    subtitle = "Check for a newer intelligence database and install it on your terms."

    def __init__(self, context, parent: QWidget | None = None) -> None:
        super().__init__(context, parent)
        self._manifest: DatabaseManifest | None = None
        self._install_worker: UpdateInstallWorker | None = None
        self._checking = False

        self.banner = StateBanner("", StateKind.INFO, self.surface, dismissible=True)
        self.banner.hide()
        self.content.addWidget(self.banner)

        self.metrics = CardGrid(self.surface, minimum_width=210)
        self.cards: dict[str, MetricCard] = {}
        for key, label, icon_name in (
            ("current", "Current Version", "database"),
            ("latest", "Latest Version", "download"),
            ("checked", "Last Checked", "refresh"),
            ("installed", "Last Updated", "updates"),
        ):
            card = MetricCard(label, "—", icon_name, self.metrics)
            self.cards[key] = card
            self.metrics.add_card(card)
        self.content.addWidget(self.metrics)

        # -- update panel ---------------------------------------------------
        self.panel = Card(self.surface, padding=20, spacing=14)
        self.panel_header = SectionHeader(
            "No update information yet",
            "Bin-Tel checks a small metadata file first, so checking never downloads "
            "the full database.",
            self.panel,
        )
        self.panel.body.addWidget(self.panel_header)

        self.release_notes = QLabel("", self.panel)
        self.release_notes.setProperty("role", "pageSubtitle")
        self.release_notes.setWordWrap(True)
        self.release_notes.setVisible(False)
        self.panel.body.addWidget(self.release_notes)

        self.progress = ProgressPanel(self.panel, tall=True)
        self.progress.setVisible(False)
        self.panel.body.addWidget(self.progress)

        buttons = hbox(spacing=10)
        self.check_button = self._button("Check for Updates", primary=True)
        self.download_button = self._button("Download Update")
        self.install_button = self._button("Install Update")
        self.cancel_button = self._button("Cancel")
        self.cancel_button.setProperty("variant", "danger")
        self.cancel_button.setVisible(False)
        for button in (
            self.check_button,
            self.download_button,
            self.install_button,
            self.cancel_button,
        ):
            buttons.addWidget(button)
        buttons.addStretch(1)
        self.panel.body.addLayout(buttons)

        self.check_button.clicked.connect(self.check_for_updates)
        self.download_button.clicked.connect(self.install_update)
        self.install_button.clicked.connect(self.install_update)
        self.cancel_button.clicked.connect(self.cancel)

        self.download_button.setEnabled(False)
        self.install_button.setEnabled(False)
        self.content.addWidget(self.panel)

        # -- schedule -------------------------------------------------------
        schedule = Card(self.surface, padding=18, spacing=10)
        schedule.body.addWidget(
            SectionHeader(
                "Update schedule",
                "Change how often Bin-Tel checks from Settings → Database.",
                schedule,
                action=self._link_button("Open settings", lambda: self.navigate("settings")),
            )
        )
        self.schedule_label = QLabel("", schedule)
        self.schedule_label.setProperty("role", "fieldValue")
        self.schedule_label.setWordWrap(True)
        schedule.body.addWidget(self.schedule_label)
        self.content.addWidget(schedule)

        # -- history --------------------------------------------------------
        self.history_card = Card(self.surface, padding=18, spacing=10)
        self.history_card.body.addWidget(SectionHeader("Recent updates", parent=self.history_card))
        self.history_label = QLabel("No updates recorded yet.", self.history_card)
        self.history_label.setProperty("role", "muted")
        self.history_label.setWordWrap(True)
        self.history_card.body.addWidget(self.history_label)
        self.content.addWidget(self.history_card)

        self.add_stretch()

    def _button(self, text: str, *, primary: bool = False) -> QPushButton:
        button = QPushButton(text, self.panel)
        button.setProperty("variant", "primary" if primary else "")
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setAccessibleName(text)
        button.setMinimumWidth(160)
        return button

    def _link_button(self, text: str, handler: object) -> QPushButton:
        button = QPushButton(text, self.surface)
        button.setProperty("variant", "link")
        button.clicked.connect(handler)  # type: ignore[arg-type]
        return button

    # -- data -------------------------------------------------------------
    def refresh(self) -> None:
        config = self.context.config
        info = self.context.stats.info() if self.context.database.is_open else None
        current = info.version if info else None

        self.cards["current"].set_value(
            current or UNKNOWN_DISPLAY,
            format_number(info.record_count) + " records" if info and info.record_count else "",
        )
        self.cards["latest"].set_value(
            config.state.last_known_remote_version or UNKNOWN_DISPLAY
        )
        self.cards["checked"].set_value(
            format_relative(config.state.last_update_check)
            if config.state.last_update_check
            else "Never"
        )
        self.cards["installed"].set_value(
            format_relative(info.installed_at) if info and info.installed_at else "Never"
        )

        settings = config.settings.database
        if not settings.automatic_updates:
            schedule = "Automatic checks are turned off. Use “Check for Updates” whenever you like."
        else:
            due = config.next_update_due()
            schedule = (
                f"Checking {settings.update_frequency.label.lower()}. "
                f"Next check {format_relative(due)}."
                if due
                else f"Checking {settings.update_frequency.label.lower()}."
            )
            extras = []
            if settings.download_automatically:
                extras.append("downloads automatically")
            if settings.install_automatically:
                extras.append("installs automatically")
            if settings.backup_before_update:
                extras.append("backs up first")
            if extras:
                schedule += " Bin-Tel " + ", ".join(extras) + "."
        self.schedule_label.setText(schedule)

        if self._manifest is None:
            latest = config.state.last_known_remote_version
            if latest and current and latest == current:
                self.panel_header.title_label.setText("Your database is up to date")
                self.panel_header.set_subtitle(
                    f"The last check found version {latest}, which is what you have installed."
                )
            elif latest and current:
                self.panel_header.title_label.setText(f"Database {latest} may be available")
                self.panel_header.set_subtitle(
                    "Check again to confirm and see the download size."
                )

        self._render_history()

    def _render_history(self) -> None:
        """Read the durable journal, which survives database replacement."""
        entries = self.context.journal.entries()[:6]
        if not entries:
            self.history_label.setText("No updates recorded yet.")
            return
        lines = []
        for entry in entries:
            detail = f" — {entry.message}" if entry.message else ""
            lines.append(f"{format_datetime(entry.at)} · {entry.label}{detail}")
        self.history_label.setText("\n".join(lines))

    # -- check ------------------------------------------------------------
    def check_for_updates(self, *, silent: bool = False) -> None:
        if self._checking:
            return
        self._checking = True
        self.check_button.setEnabled(False)
        self.progress.setVisible(True)
        self.progress.set_indeterminate("Checking for updates…")

        info = self.context.stats.info() if self.context.database.is_open else None
        worker = UpdateCheckWorker(self.context.updates, info.version if info else None)
        worker.signals.result.connect(lambda check: self._on_checked(check, silent))
        worker.signals.failed.connect(lambda exc: self._on_check_failed(exc, silent))
        run_in_background(worker)

    def _on_checked(self, check: UpdateCheck, silent: bool = False) -> None:
        self._checking = False
        self.check_button.setEnabled(True)
        self.progress.setVisible(False)
        self._manifest = check.manifest
        self.context.config.mark_update_checked(check.latest_version)

        if check.manifest is None:
            self.banner.show_message(check.message or "No update information available.", StateKind.WARNING)
            self.refresh()
            return

        manifest = check.manifest
        self.cards["latest"].set_value(
            manifest.version,
            format_datetime(manifest.release_date, with_time=False)
            if manifest.release_date
            else "",
        )

        if check.update_available:
            self.panel_header.title_label.setText(f"Database {manifest.version} is available")
            self.panel_header.set_subtitle(
                f"{format_bytes(manifest.database_size)} download · "
                f"{format_number(manifest.record_count)} records · "
                f"published by {manifest.publisher}"
            )
            self.release_notes.setText(manifest.notes)
            self.release_notes.setVisible(bool(manifest.notes))
            self.download_button.setEnabled(True)
            self.install_button.setEnabled(True)
            self.banner.show_message(
                f"Database {manifest.version} is ready to install.",
                StateKind.INFO,
                action_text="Install now",
            )
            self.banner.action_triggered.connect(self.install_update)
            if self.context.config.settings.database.download_automatically:
                self.install_update()
        else:
            self.panel_header.title_label.setText("Your database is up to date")
            self.panel_header.set_subtitle(check.message)
            self.release_notes.setVisible(False)
            self.download_button.setEnabled(False)
            self.install_button.setEnabled(False)
            if not silent:
                self.banner.show_message("Your database is up to date.", StateKind.SUCCESS)

        self.refresh()

    def _on_check_failed(self, exc: BaseException, silent: bool = False) -> None:
        self._checking = False
        self.check_button.setEnabled(True)
        self.progress.setVisible(False)
        self.context.config.mark_update_checked()
        if isinstance(exc, OfflineError):
            self.banner.show_message(
                "Bin-Tel is offline. Your local database is unaffected and lookups "
                "continue to work.",
                StateKind.WARNING,
                action_text="Try again",
            )
            self.banner.action_triggered.connect(lambda: self.check_for_updates())
            return
        self.banner.show_message("Could not check for updates.", StateKind.DANGER)
        if not silent:
            if self.show_error(exc):
                self.check_for_updates()

    # -- install ----------------------------------------------------------
    def install_update(self) -> None:
        if self._manifest is None or self._install_worker is not None:
            return
        info = self.context.stats.info() if self.context.database.is_open else None
        self.progress.setVisible(True)
        self.progress.reset("Preparing…")
        self._set_busy(True)

        worker = UpdateInstallWorker(
            self.context.updates,
            self._manifest,
            previous_version=info.version if info else None,
        )
        worker.signals.stage.connect(self._on_stage)
        worker.signals.result.connect(self._on_installed)
        worker.signals.failed.connect(self._on_install_failed)
        worker.signals.cancelled.connect(self._on_cancelled)
        self._install_worker = worker
        run_in_background(worker)

    def _on_stage(self, progress: UpdateProgress) -> None:
        self.progress.update_progress(progress)
        self.status_message.emit(progress.message or progress.state.label)

    def _on_installed(self, outcome: UpdateOutcome) -> None:
        self._install_worker = None
        self._set_busy(False)
        self.context.config.state.last_update_installed = datetime.now(UTC)
        self.context.config.save_state()
        self.banner.show_message(
            f"Database {outcome.version} installed successfully.", StateKind.SUCCESS
        )
        self.toast(f"Database {outcome.version} installed")
        self.download_button.setEnabled(False)
        self.install_button.setEnabled(False)
        self.on_database_changed()
        self.navigation_requested.emit("__database_reloaded__")

    def _on_install_failed(self, exc: BaseException) -> None:
        self._install_worker = None
        self._set_busy(False)
        self.progress.set_error("The update did not complete.")
        self.banner.show_message(
            "The update failed. Your previous database has been kept.", StateKind.DANGER
        )
        if self.show_error(exc):
            self.install_update()

    def _on_cancelled(self) -> None:
        self._install_worker = None
        self._set_busy(False)
        self.progress.reset("Update cancelled.")
        self.banner.show_message("The update was cancelled.", StateKind.WARNING)

    def cancel(self) -> None:
        if self._install_worker is not None:
            self._install_worker.cancel()
            self.progress.set_indeterminate("Cancelling…")

    def _set_busy(self, busy: bool) -> None:
        self.check_button.setEnabled(not busy)
        self.download_button.setEnabled(not busy and self._manifest is not None)
        self.install_button.setEnabled(not busy and self._manifest is not None)
        self.cancel_button.setVisible(busy)

    def on_theme_changed(self) -> None:
        for card in self.cards.values():
            card.refresh_icon()

    def closeEvent(self, event: object) -> None:
        if self._install_worker is not None:
            self._install_worker.cancel()
        super().closeEvent(event)  # type: ignore[arg-type]

    @staticmethod
    def _unused(_: OperationCancelled) -> None:  # pragma: no cover
        return None
