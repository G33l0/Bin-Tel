"""Database management page."""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QLabel, QProgressBar, QPushButton, QWidget

from app.core.constants import UNKNOWN_DISPLAY
from app.database.integrity import VerificationReport
from app.models.schemas import DatabaseInfo
from app.ui.dialogs.backup_dialog import BackupDialog
from app.ui.dialogs.confirm_dialog import ConfirmDialog
from app.ui.pages.base_page import BasePage
from app.ui.widgets.cards import Card, CardGrid, MetricCard, SectionHeader
from app.ui.widgets.states import StateBanner, StateKind
from app.utils.formatting import (
    format_bytes,
    format_datetime_with_relative,
    format_number,
    format_relative,
)
from app.utils.qt_helpers import hbox, reveal_in_file_manager, vbox
from app.workers.base import Worker, run_in_background
from app.workers.maintenance_worker import BackupWorker, ReindexWorker, RestoreWorker, VerifyWorker


class DatabasePage(BasePage):
    """Status, verification, backups and the database's location on disk."""

    key = "database"
    title = "Database"
    subtitle = "Manage the local intelligence database that powers offline lookups."

    def __init__(self, context, parent: QWidget | None = None) -> None:
        super().__init__(context, parent)
        self._info: DatabaseInfo | None = None
        self._busy = False

        self.banner = StateBanner("", StateKind.INFO, self.surface, dismissible=True)
        self.banner.hide()
        self.content.addWidget(self.banner)

        # -- status metrics -------------------------------------------------
        self.metrics = CardGrid(self.surface, minimum_width=205)
        self.cards: dict[str, MetricCard] = {}
        for key, label, icon_name in (
            ("status", "Database Status", "shield"),
            ("version", "Database Version", "database"),
            ("size", "Database Size", "database"),
            ("bins", "BIN Count", "bin-lookup"),
            ("institutions", "Institution Count", "bank-lookup"),
            ("countries", "Country Count", "globe"),
            ("updated", "Last Updated", "updates"),
            ("verified", "Last Verification", "check"),
        ):
            card = MetricCard(label, "—", icon_name, self.metrics)
            self.cards[key] = card
            self.metrics.add_card(card)
        self.content.addWidget(self.metrics)

        # -- actions --------------------------------------------------------
        actions = Card(self.surface, padding=18, spacing=14)
        actions.body.addWidget(
            SectionHeader(
                "Maintenance",
                "Bin-Tel verifies a database before it activates it, and keeps a backup "
                "before every update.",
                actions,
            )
        )
        row = hbox(spacing=10)
        self.buttons: dict[str, QPushButton] = {}
        for key, label, primary in (
            ("check", "Check for Updates", True),
            ("update", "Update Database", False),
            ("verify", "Verify Database", False),
            ("reindex", "Rebuild Indexes", False),
            ("open", "Open Database Location", False),
            ("backup", "Backup Database", False),
            ("restore", "Restore Backup", False),
        ):
            button = QPushButton(label, actions)
            button.setProperty("variant", "primary" if primary else "")
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setAccessibleName(label)
            self.buttons[key] = button
            row.addWidget(button)
        row.addStretch(1)
        actions.body.addLayout(row)

        self.progress = QProgressBar(actions)
        self.progress.setTextVisible(False)
        self.progress.setVisible(False)
        actions.body.addWidget(self.progress)

        self.activity_label = QLabel("", actions)
        self.activity_label.setProperty("role", "muted")
        self.activity_label.setVisible(False)
        actions.body.addWidget(self.activity_label)
        self.content.addWidget(actions)

        self.buttons["check"].clicked.connect(lambda: self.navigate("updates"))
        self.buttons["update"].clicked.connect(lambda: self.navigate("updates"))
        self.buttons["verify"].clicked.connect(self.verify_database)
        self.buttons["reindex"].clicked.connect(self.rebuild_indexes)
        self.buttons["open"].clicked.connect(self.open_location)
        self.buttons["backup"].clicked.connect(self.create_backup)
        self.buttons["restore"].clicked.connect(self.restore_backup)

        # -- location and backups -------------------------------------------
        details = Card(self.surface, padding=18, spacing=12)
        details.body.addWidget(SectionHeader("Storage", parent=details))

        self.path_label = QLabel("", details)
        self.path_label.setProperty("role", "mono")
        self.path_label.setStyleSheet("font-size: 10pt;")
        self.path_label.setWordWrap(True)
        self.path_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        details.body.addWidget(self.path_label)

        self.backups_label = QLabel("", details)
        self.backups_label.setProperty("role", "muted")
        details.body.addWidget(self.backups_label)
        self.content.addWidget(details)

        # -- verification report ---------------------------------------------
        self.report_card = Card(self.surface, padding=18, spacing=10)
        self.report_card.body.addWidget(
            SectionHeader("Latest verification", parent=self.report_card)
        )
        self.report_label = QLabel("", self.report_card)
        self.report_label.setWordWrap(True)
        self.report_card.body.addWidget(self.report_label)
        self.report_card.hide()
        self.content.addWidget(self.report_card)

        self.add_stretch()

    # -- data -------------------------------------------------------------
    def refresh(self) -> None:
        info = self.context.stats.info() if self.context.database.is_open else None
        path = self.context.database.path
        self.path_label.setText(str(path))

        if info is None:
            for card in self.cards.values():
                card.set_value("—")
            self.cards["status"].set_value("Not installed", "Download it from the Updates page")
            self.banner.show_message(
                "The Bin-Tel database has not been installed yet.",
                StateKind.WARNING,
                action_text="Go to Updates",
            )
            self._set_maintenance_enabled(False)
            self._update_backup_summary()
            return

        self.banner.hide()
        self._info = info
        stats = info.stats
        self.cards["status"].set_value(
            "Ready" if info.healthy else "Attention needed", info.status_message
        )
        self.cards["version"].set_value(info.version or UNKNOWN_DISPLAY, info.publisher or "")
        self.cards["size"].set_value(format_bytes(info.size_bytes))
        self.cards["bins"].set_value(
            format_number(stats.bins), f"{format_number(stats.bin_ranges)} ranges"
        )
        self.cards["institutions"].set_value(
            format_number(stats.institutions), f"{format_number(stats.aliases)} aliases"
        )
        self.cards["countries"].set_value(format_number(stats.countries))
        self.cards["updated"].set_value(
            format_relative(info.installed_at) if info.installed_at else "Never"
        )
        self.cards["verified"].set_value(
            format_relative(info.last_verified) if info.last_verified else "Never"
        )
        self._set_maintenance_enabled(not self._busy)
        self._update_backup_summary()

    def _update_backup_summary(self) -> None:
        backups = self.context.backups.list()
        if not backups:
            self.backups_label.setText("No backups yet.")
            self.buttons["restore"].setEnabled(False)
            return
        newest = backups[0]
        total = sum(item.size_bytes for item in backups)
        self.backups_label.setText(
            f"{len(backups)} backup(s), {format_bytes(total)} total. "
            f"Most recent: {format_datetime_with_relative(newest.created_at)}."
        )
        self.buttons["restore"].setEnabled(not self._busy)

    def _set_maintenance_enabled(self, enabled: bool) -> None:
        installed = self.context.database.is_installed
        for key in ("verify", "reindex", "backup"):
            self.buttons[key].setEnabled(enabled and installed)
        self.buttons["restore"].setEnabled(enabled and bool(self.context.backups.list()))

    def _begin(self, message: str, *, determinate: bool = False) -> None:
        self._busy = True
        self.activity_label.setText(message)
        self.activity_label.setVisible(True)
        self.progress.setVisible(True)
        self.progress.setRange(0, 100 if determinate else 0)
        self.progress.setValue(0)
        self._set_maintenance_enabled(False)

    def _end(self, message: str = "") -> None:
        self._busy = False
        self.progress.setVisible(False)
        self.activity_label.setText(message)
        self.activity_label.setVisible(bool(message))
        self._set_maintenance_enabled(True)
        self.refresh()

    # -- actions ----------------------------------------------------------
    def verify_database(self) -> None:
        self._begin("Verifying the database…")
        worker = VerifyWorker(self.context.database, quick=False)
        worker.signals.result.connect(self._on_verified)
        worker.signals.failed.connect(self._on_failed)
        run_in_background(worker)

    def _on_verified(self, report: VerificationReport) -> None:
        self._end()
        self.report_card.show()
        lines = [report.summary]
        if report.warnings:
            lines.extend(f"• {warning}" for warning in report.warnings)
        if report.errors:
            lines.extend(f"• {error}" for error in report.errors)
        lines.append(
            f"{format_number(report.bin_count)} BINs · "
            f"{format_number(report.institution_count)} institutions · "
            f"{format_bytes(report.size_bytes)}"
        )
        self.report_label.setText("\n".join(lines))
        self.banner.show_message(
            report.summary, StateKind.SUCCESS if report.ok else StateKind.DANGER
        )
        self.toast("Verification complete" if report.ok else "Verification found problems")

    def rebuild_indexes(self) -> None:
        self._begin("Rebuilding indexes…")
        worker = ReindexWorker(self.context.database)
        worker.signals.result.connect(lambda created: self._on_reindexed(len(created)))
        worker.signals.failed.connect(self._on_failed)
        run_in_background(worker)

    def _on_reindexed(self, count: int) -> None:
        self._end()
        self.toast(f"Indexes verified ({count} covering indexes)")

    def create_backup(self) -> None:
        self._begin("Creating a backup…", determinate=True)
        worker = BackupWorker(self.context.backups, self._info.version if self._info else None)
        worker.signals.progress.connect(self._on_backup_progress)
        worker.signals.result.connect(self._on_backup_created)
        worker.signals.failed.connect(self._on_failed)
        run_in_background(worker)

    def _on_backup_progress(self, payload: object) -> None:
        try:
            done, total = payload  # type: ignore[misc]
        except (TypeError, ValueError):  # pragma: no cover - defensive
            return
        if total:
            self.progress.setValue(int(done / total * 100))

    def _on_backup_created(self, path: Path) -> None:
        self._end()
        self.toast(f"Backup created: {path.name}")

    def restore_backup(self) -> None:
        backups = self.context.backups.list()
        selected = BackupDialog.choose(self, backups)
        if selected is None:
            return
        confirmed = ConfirmDialog.ask(
            self,
            "Restore this backup?",
            "Your current database will be replaced by the selected backup. Bin-Tel "
            "verifies the backup first, so a damaged snapshot will not be activated.",
            confirm_text="Restore",
            destructive=True,
        )
        if not confirmed:
            return
        self._begin("Restoring the backup…")
        self.context.database.close()
        worker = RestoreWorker(self.context.backups, selected)
        worker.signals.result.connect(lambda _: self._on_restored())
        worker.signals.failed.connect(self._on_restore_failed)
        run_in_background(worker)

    def _on_restored(self) -> None:
        try:
            self.context.database.open()
        except Exception as exc:  # noqa: BLE001 - reported to the user
            self._on_failed(exc)
            return
        self._end()
        self.banner.show_message("The backup was restored successfully.", StateKind.SUCCESS)
        self.toast("Database restored")
        self.on_database_changed()

    def _on_restore_failed(self, exc: BaseException) -> None:
        try:
            if self.context.database.is_installed:
                self.context.database.open()
        except Exception:  # noqa: BLE001 - the original error matters more
            pass
        self._on_failed(exc)

    def open_location(self) -> None:
        path = self.context.database.path
        target = path if path.exists() else path.parent
        if reveal_in_file_manager(target):
            self.toast("Opened the database location")
        else:
            self.toast("Could not open the folder on this system")

    def _on_failed(self, exc: BaseException) -> None:
        self._end()
        self.banner.show_message("The operation did not complete.", StateKind.DANGER)
        self.show_error(exc)

    def on_theme_changed(self) -> None:
        for card in self.cards.values():
            card.refresh_icon()
