"""Database Administration — health, verification, optimisation and backups."""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QLabel,
    QListWidget,
    QListWidgetItem,
    QProgressBar,
    QPushButton,
    QWidget,
)

from app.database.backup import BackupInfo
from app.database.integrity import VerificationReport
from app.services.health_service import HealthReport
from app.services.report_service import ReportFormat
from app.ui.dialogs.confirm_dialog import ConfirmDialog
from app.ui.dialogs.export_dialog import ExportDialog
from app.ui.pages.base_page import BasePage
from app.ui.themes.icons import IconProvider
from app.ui.widgets.cards import Card, CardGrid, MetricCard, SectionHeader
from app.ui.widgets.charts import HealthGauge
from app.ui.widgets.states import StateBanner, StateKind
from app.utils.formatting import (
    format_bytes,
    format_datetime_with_relative,
    format_number,
    format_relative,
)
from app.utils.qt_helpers import expanding_spacer, grid, hbox, reveal_in_file_manager, vbox
from app.workers.base import Worker, run_in_background
from app.workers.maintenance_worker import (
    BackupWorker,
    ReindexWorker,
    RestoreWorker,
    VerifyWorker,
)


class DatabaseAdminPage(BasePage):
    """The maintenance surface: health, integrity, indexes, storage, backups."""

    key = "admin"
    title = "Database Administration"
    subtitle = "Assess, verify, optimise and protect the local intelligence database."

    def __init__(self, context, parent: QWidget | None = None) -> None:
        super().__init__(context, parent)
        self._busy = False
        self._health: HealthReport | None = None

        self.banner = StateBanner("", StateKind.INFO, self.surface, dismissible=True)
        self.banner.hide()
        self.content.addWidget(self.banner)

        # -- health ------------------------------------------------------------
        health_card = Card(self.surface, padding=20, spacing=14)
        health_card.body.addWidget(
            SectionHeader(
                "Database health",
                "Every figure here is measured from the database in front of you.",
                health_card,
                action=self._health_action(health_card),
            )
        )
        health_row = hbox(spacing=20)
        self.gauge = HealthGauge(health_card)
        health_row.addWidget(self.gauge, 0, Qt.AlignmentFlag.AlignTop)

        checks_holder = QWidget(health_card)
        self._checks_grid = grid(checks_holder, spacing=10)
        health_row.addWidget(checks_holder, 1)
        health_card.body.addLayout(health_row)

        self.health_summary = QLabel("", health_card)
        self.health_summary.setProperty("role", "muted")
        self.health_summary.setWordWrap(True)
        health_card.body.addWidget(self.health_summary)
        self.content.addWidget(health_card)

        # -- statistics ---------------------------------------------------------
        self.metrics = CardGrid(self.surface, minimum_width=196)
        self.cards: dict[str, MetricCard] = {}
        for key, label_text, icon_name in (
            ("version", "Database Version", "database"),
            ("schema", "Schema Version", "shield"),
            ("size", "Database Size", "database"),
            ("bins", "BIN Count", "bin-lookup"),
            ("institutions", "Institution Count", "bank-lookup"),
            ("countries", "Country Count", "globe"),
            ("networks", "Network Count", "shield"),
            ("updated", "Last Update", "updates"),
            ("verified", "Last Verification", "check"),
        ):
            card = MetricCard(label_text, "—", icon_name, self.metrics)
            self.cards[key] = card
            self.metrics.add_card(card)
        self.content.addWidget(self.metrics)

        # -- data quality -------------------------------------------------------
        # Measured, not estimated. Every row shows the counts behind the ratio
        # so a figure can always be explained rather than merely believed.
        self.quality_card = Card(self.surface, padding=18, spacing=12)
        self.quality_card.body.addWidget(
            SectionHeader(
                "Data quality",
                "Counted from this database. A metric with nothing to measure "
                "says so rather than reporting a misleading 100%.",
                self.quality_card,
            )
        )
        self.quality_grid = grid(spacing=10)
        self.quality_card.body.addLayout(self.quality_grid)
        self.quality_summary = QLabel("", self.quality_card)
        self.quality_summary.setProperty("role", "muted")
        self.quality_summary.setWordWrap(True)
        self.quality_card.body.addWidget(self.quality_summary)
        self.content.addWidget(self.quality_card)

        # -- operations ---------------------------------------------------------
        operations = Card(self.surface, padding=18, spacing=14)
        operations.body.addWidget(
            SectionHeader(
                "Maintenance",
                "Anything that alters the database asks for confirmation first.",
                operations,
            )
        )
        buttons_holder = QWidget(operations)
        buttons_grid = grid(buttons_holder, spacing=10)
        self.buttons: dict[str, QPushButton] = {}
        operations_spec = (
            ("integrity", "Check Integrity", False, self._check_integrity),
            ("verify", "Verify Database", False, self._verify),
            ("reindex", "Rebuild Indexes", False, self._reindex),
            ("optimize", "Optimize Database", False, self._optimize),
            ("vacuum", "Vacuum Database", True, self._vacuum),
            ("orphans", "Remove Orphans", True, self._remove_orphans),
            ("backup", "Create Backup", False, self._create_backup),
            ("restore", "Restore Backup", True, self._restore_backup),
            ("export", "Export Database", False, self._export_database),
            ("folder", "Open Database Folder", False, self._open_folder),
            ("updates", "Check for Updates", False, lambda: self.navigate("updates")),
            ("report", "Export Health Report", False, self._export_health),
        )
        for index, (key, label_text, destructive, handler) in enumerate(operations_spec):
            button = QPushButton(label_text, buttons_holder)
            if destructive:
                button.setProperty("variant", "danger")
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setAccessibleName(label_text)
            button.clicked.connect(handler)
            self.buttons[key] = button
            buttons_grid.addWidget(button, index // 4, index % 4)
        for column in range(4):
            buttons_grid.setColumnStretch(column, 1)
        operations.body.addWidget(buttons_holder)

        self.progress = QProgressBar(operations)
        self.progress.setTextVisible(False)
        self.progress.setVisible(False)
        operations.body.addWidget(self.progress)

        self.activity_label = QLabel("", operations)
        self.activity_label.setProperty("role", "muted")
        self.activity_label.setVisible(False)
        operations.body.addWidget(self.activity_label)
        self.content.addWidget(operations)

        # -- backups ------------------------------------------------------------
        backups_card = Card(self.surface, padding=18, spacing=10)
        backups_card.body.addWidget(
            SectionHeader(
                "Backups",
                "A backup is taken before every update, and verified before it is "
                "ever restored.",
                backups_card,
            )
        )
        self.backups_list = QListWidget(backups_card)
        self.backups_list.setAccessibleName("Database backups")
        self.backups_list.setMaximumHeight(170)
        backups_card.body.addWidget(self.backups_list)

        backup_actions = hbox(spacing=8)
        self.delete_backup_button = QPushButton("Delete selected backup", backups_card)
        self.delete_backup_button.setProperty("variant", "ghost")
        self.delete_backup_button.clicked.connect(self._delete_backup)
        backup_actions.addWidget(self.delete_backup_button)
        backup_actions.addItem(expanding_spacer())
        self.backup_summary = QLabel("", backups_card)
        self.backup_summary.setProperty("role", "muted")
        backup_actions.addWidget(self.backup_summary)
        backups_card.body.addLayout(backup_actions)
        self.content.addWidget(backups_card)

        # -- storage -------------------------------------------------------------
        storage_card = Card(self.surface, padding=18, spacing=10)
        storage_card.body.addWidget(SectionHeader("Storage", parent=storage_card))
        self.storage_label = QLabel("", storage_card)
        self.storage_label.setProperty("role", "mono")
        self.storage_label.setStyleSheet("font-size: 9pt;")
        self.storage_label.setWordWrap(True)
        self.storage_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        storage_card.body.addWidget(self.storage_label)
        self.content.addWidget(storage_card)

        self.add_stretch()

    def _health_action(self, parent: QWidget) -> QPushButton:
        button = QPushButton("Re-assess", parent)
        button.setProperty("variant", "ghost")
        button.clicked.connect(self._assess_health)
        return button

    # -- lifecycle -----------------------------------------------------------
    def refresh(self) -> None:
        self._render_statistics()
        self._render_backups()
        self._render_storage()
        self._assess_health()
        self._measure_quality()

    def _measure_quality(self) -> None:
        """Count the quality metrics on a worker thread."""
        if not self.context.database.is_open:
            self.quality_card.hide()
            return
        worker: Worker = Worker(
            lambda: self.context.quality.evaluate(
                database_version=self.context.database_version()
            )
        )
        worker.signals.result.connect(self._render_quality)
        run_in_background(worker)

    def _render_quality(self, report) -> None:
        while self.quality_grid.count():
            item = self.quality_grid.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        if report.error:
            self.quality_card.hide()
            return

        theme = IconProvider.instance().theme
        for index, metric in enumerate(report.metrics):
            row, column = divmod(index, 3)
            holder = QWidget(self.quality_card)
            holder_layout = vbox(holder, spacing=2)

            label = QLabel(metric.label, holder)
            label.setProperty("role", "fieldLabel")
            holder_layout.addWidget(label)

            value = QLabel(metric.display, holder)
            value.setProperty("role", "fieldValue")
            if metric.measured and not metric.neutral:
                # Green means "good", which depends on which way the metric
                # points: a high duplicate rate is not a success. Composition
                # figures get no colour at all — they are facts, not scores.
                ratio = metric.ratio or 0.0
                good = ratio >= 0.95 if metric.higher_is_better else ratio <= 0.01
                value.setStyleSheet(
                    f"color: {theme.success if good else theme.warning};"
                )
            value.setAccessibleName(f"{metric.label}: {metric.display}")
            holder_layout.addWidget(value)

            detail = QLabel(metric.detail, holder)
            detail.setProperty("role", "muted")
            detail.setToolTip(metric.description)
            holder_layout.addWidget(detail)

            self.quality_grid.addWidget(holder, row, column)

        self.quality_summary.setText(
            f"{report.summary}  Measured in {report.elapsed_ms:.0f} ms."
        )
        self.quality_card.show()

    def _render_statistics(self) -> None:
        if not self.context.database.is_open:
            for card in self.cards.values():
                card.set_value("—")
            self.banner.show_message(
                "The database is not installed yet.",
                StateKind.WARNING,
                action_text="Go to Updates",
            )
            self._set_enabled(False)
            return
        self.banner.hide()
        info = self.context.stats.info()
        stats = info.stats
        self.cards["version"].set_value(info.version or "Unknown", info.publisher or "")
        self.cards["schema"].set_value(
            str(info.schema_version) if info.schema_version else "Unknown"
        )
        self.cards["size"].set_value(format_bytes(info.size_bytes))
        self.cards["bins"].set_value(
            format_number(stats.bins), f"{format_number(stats.bin_ranges)} ranges"
        )
        self.cards["institutions"].set_value(
            format_number(stats.institutions), f"{format_number(stats.aliases)} aliases"
        )
        self.cards["countries"].set_value(format_number(stats.countries))
        self.cards["networks"].set_value(format_number(stats.networks))
        self.cards["updated"].set_value(
            format_relative(info.installed_at) if info.installed_at else "Never"
        )
        self.cards["verified"].set_value(
            format_relative(info.last_verified) if info.last_verified else "Never"
        )
        self._set_enabled(not self._busy)

    def _render_backups(self) -> None:
        backups = self.context.backups.list()
        self.backups_list.clear()
        provider = IconProvider.instance()
        for backup in backups:
            item = QListWidgetItem(
                f"{format_datetime_with_relative(backup.created_at)}\n"
                f"{format_bytes(backup.size_bytes)}"
                + (f"  ·  database {backup.version}" if backup.version else ""),
                self.backups_list,
            )
            item.setData(Qt.ItemDataRole.UserRole, str(backup.path))
            item.setIcon(provider.icon("backup", provider.theme.text_secondary, 15))
            item.setToolTip(backup.name)
            self.backups_list.addItem(item)
        if not backups:
            self.backups_list.addItem("No backups yet.")
        total = sum(item.size_bytes for item in backups)
        keep = self.context.config.settings.database.max_backups
        self.backup_summary.setText(
            f"{len(backups)} backup(s) · {format_bytes(total)} · keeping the newest {keep}"
        )
        self.delete_backup_button.setEnabled(bool(backups))
        self.buttons["restore"].setEnabled(bool(backups) and not self._busy)

    def _render_storage(self) -> None:
        paths = self.context.paths

        def folder_size(path: Path) -> int:
            if not path.exists():
                return 0
            return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())

        self.storage_label.setText(
            f"Intelligence database   {self.context.database_path}\n"
            f"User data               {self.context.user_store.path} "
            f"({format_bytes(self.context.user_store.size_bytes())})\n"
            f"Backups                 {self.context.config.backups_path()} "
            f"({format_bytes(folder_size(self.context.config.backups_path()))})\n"
            f"Downloads               {paths.downloads_dir} "
            f"({format_bytes(folder_size(paths.downloads_dir))})\n"
            f"Reports                 {self.context.config.reports_path()}\n"
            f"Logs                    {paths.logs_dir} "
            f"({format_bytes(folder_size(paths.logs_dir))})"
        )

    # -- health ---------------------------------------------------------------
    def _assess_health(self) -> None:
        if not self.context.database.is_open or self._busy:
            return
        worker: Worker = Worker(self.context.health.evaluate, quick=True)
        worker.signals.result.connect(self._render_health)
        worker.signals.failed.connect(lambda exc: self.show_error(exc))
        run_in_background(worker)

    def _render_health(self, report: HealthReport) -> None:
        self._health = report
        self.gauge.set_score(report.score, report.grade.label, report.grade.state)
        self.health_summary.setText(report.summary)

        while self._checks_grid.count():
            item = self._checks_grid.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        provider = IconProvider.instance()
        theme = provider.theme
        for index, check in enumerate(report.checks):
            row = QWidget(self.surface)
            row_layout = hbox(row, spacing=10)

            icon = QLabel(row)
            icon.setFixedSize(15, 15)
            colour = theme.success if check.passed else (
                theme.danger if check.score < 0.6 else theme.warning
            )
            icon.setPixmap(provider.pixmap("check" if check.passed else "warning", colour, 14))
            row_layout.addWidget(icon, 0, Qt.AlignmentFlag.AlignTop)

            column = vbox(spacing=1)
            title = QLabel(f"{check.label} — {check.grade.label}", row)
            title.setProperty("role", "fieldValue")
            column.addWidget(title)
            detail = QLabel(check.detail, row)
            detail.setProperty("role", "muted")
            detail.setWordWrap(True)
            column.addWidget(detail)
            row_layout.addLayout(column, 1)

            self._checks_grid.addWidget(row, index // 2, index % 2)
        for column in range(2):
            self._checks_grid.setColumnStretch(column, 1)

    # -- operations -------------------------------------------------------------
    def _set_enabled(self, enabled: bool) -> None:
        installed = self.context.database.is_installed
        for key, button in self.buttons.items():
            if key in ("updates", "folder"):
                button.setEnabled(True)
            elif key == "restore":
                button.setEnabled(enabled and bool(self.context.backups.list()))
            else:
                button.setEnabled(enabled and installed)

    def _begin(self, message: str, *, determinate: bool = False) -> None:
        self._busy = True
        self.activity_label.setText(message)
        self.activity_label.setVisible(True)
        self.progress.setVisible(True)
        self.progress.setRange(0, 100 if determinate else 0)
        self.progress.setValue(0)
        self._set_enabled(False)

    def _end(self, message: str = "") -> None:
        self._busy = False
        self.progress.setVisible(False)
        self.activity_label.setText(message)
        self.activity_label.setVisible(bool(message))
        self._set_enabled(True)
        self._render_statistics()
        self._render_backups()
        self._render_storage()

    def _check_integrity(self) -> None:
        self._begin("Checking integrity…")
        worker = VerifyWorker(self.context.database, quick=True)
        worker.signals.result.connect(self._on_verified)
        worker.signals.failed.connect(self._on_failed)
        run_in_background(worker)

    def _verify(self) -> None:
        self._begin("Verifying the database in full…")
        worker = VerifyWorker(self.context.database, quick=False)
        worker.signals.result.connect(self._on_verified)
        worker.signals.failed.connect(self._on_failed)
        run_in_background(worker)

    def _on_verified(self, report: VerificationReport) -> None:
        self._end()
        self.banner.show_message(
            report.summary, StateKind.SUCCESS if report.ok else StateKind.DANGER
        )
        from app.telemetry.events import Event

        self.context.telemetry.record(
            Event.DATABASE_VERIFIED,
            {
                "ok": report.ok,
                "quick": False,
                "health_score": self._health.percent if self._health else 0,
            },
        )
        self._assess_health()
        self.toast("Verification complete" if report.ok else "Verification found problems")

    def _reindex(self) -> None:
        self._begin("Rebuilding indexes…")
        worker = ReindexWorker(self.context.database)
        worker.signals.result.connect(lambda created: self._on_reindexed(len(created)))
        worker.signals.failed.connect(self._on_failed)
        run_in_background(worker)

    def _on_reindexed(self, count: int) -> None:
        self._end()
        self.banner.show_message(
            f"Indexes verified; {count} covering index(es) ensured.", StateKind.SUCCESS
        )
        self._assess_health()

    def _optimize(self) -> None:
        self._begin("Optimising…")

        def work() -> None:
            from app.database.schema import analyze, optimize

            optimize(self.context.manager.engine)
            analyze(self.context.manager.engine)

        worker: Worker = Worker(work)
        worker.signals.result.connect(
            lambda _: (
                self._end(),
                self.banner.show_message(
                    "Query planner statistics refreshed.", StateKind.SUCCESS
                ),
            )
        )
        worker.signals.failed.connect(self._on_failed)
        run_in_background(worker)

    def _vacuum(self) -> None:
        if not ConfirmDialog.ask(
            self,
            "Vacuum the database?",
            "Bin-Tel rewrites the database file to reclaim unused space. It can take a "
            "while on a large database, and needs free disk space roughly equal to the "
            "database size. Lookups are unavailable while it runs.",
            confirm_text="Vacuum",
            destructive=True,
        ):
            return
        before = self.context.database.size_bytes() or 0
        self._begin("Vacuuming the database…")

        worker: Worker = Worker(self.context.database.compact)
        worker.signals.result.connect(lambda _: self._on_vacuumed(before))
        worker.signals.failed.connect(self._on_failed)
        run_in_background(worker)

    def _on_vacuumed(self, before: int) -> None:
        self._end()
        after = self.context.database.size_bytes() or 0
        saved = max(0, before - after)
        self.banner.show_message(
            f"Database compacted; {format_bytes(saved)} reclaimed.", StateKind.SUCCESS
        )

    def _remove_orphans(self) -> None:
        if not ConfirmDialog.ask(
            self,
            "Remove orphaned rows?",
            "Rows that point at records which no longer exist are deleted. This cannot "
            "be undone, but it never removes a BIN or an institution.",
            confirm_text="Remove orphans",
            destructive=True,
        ):
            return
        self._begin("Removing orphaned rows…")
        worker: Worker = Worker(self.context.health.remove_orphans)
        worker.signals.result.connect(self._on_orphans_removed)
        worker.signals.failed.connect(self._on_failed)
        run_in_background(worker)

    def _on_orphans_removed(self, removed: dict) -> None:
        self._end()
        total = sum(removed.values())
        self.banner.show_message(
            f"{total:,} orphaned row(s) removed." if total else "No orphaned rows were found.",
            StateKind.SUCCESS,
        )
        self._assess_health()

    def _create_backup(self) -> None:
        self._begin("Creating a backup…", determinate=True)
        worker = BackupWorker(self.context.backups, self.context.database_version())
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
        from app.telemetry.events import Event, size_bucket

        self.context.telemetry.record(
            Event.DATABASE_BACKUP_CREATED,
            {"size_bucket": size_bucket(path.stat().st_size)},
        )
        self.banner.show_message(f"Backup created: {path.name}", StateKind.SUCCESS)
        self.toast("Backup created")

    def _restore_backup(self) -> None:
        item = self.backups_list.currentItem()
        path = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
        if path is None:
            latest = self.context.backups.latest()
            if latest is None:
                self.toast("There are no backups to restore")
                return
            path = str(latest.path)
        if not ConfirmDialog.ask(
            self,
            "Restore this backup?",
            "Your current database is replaced by the selected backup. Bin-Tel verifies "
            "the backup first, so a damaged snapshot is never activated.",
            confirm_text="Restore",
            destructive=True,
        ):
            return
        self._begin("Restoring the backup…")
        self.context.database.close()
        worker = RestoreWorker(self.context.backups, Path(path))
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
        from app.telemetry.events import Event

        self.context.telemetry.record(Event.DATABASE_RESTORED, {"ok": True})
        self.banner.show_message("The backup was restored successfully.", StateKind.SUCCESS)
        self.navigation_requested.emit("__database_reloaded__")

    def _on_restore_failed(self, exc: BaseException) -> None:
        try:
            if self.context.database.is_installed:
                self.context.database.open()
        except Exception:  # noqa: BLE001 - the original error matters more
            pass
        from app.telemetry.events import Event

        self.context.telemetry.record(Event.DATABASE_RESTORED, {"ok": False})
        self._on_failed(exc)

    def _delete_backup(self) -> None:
        item = self.backups_list.currentItem()
        if item is None:
            return
        path = item.data(Qt.ItemDataRole.UserRole)
        if path is None:
            return
        if not ConfirmDialog.ask(
            self,
            "Delete this backup?",
            "The snapshot file is permanently removed.",
            confirm_text="Delete backup",
            destructive=True,
        ):
            return
        try:
            Path(path).unlink(missing_ok=True)
        except OSError as exc:
            self.show_error(exc)
            return
        self._render_backups()
        self.toast("Backup deleted")

    def _export_database(self) -> None:
        from PyQt6.QtWidgets import QFileDialog

        if not self.context.database.is_installed:
            return
        suggested = (
            self.context.config.reports_path()
            / f"bintel-{self.context.database_version() or 'database'}.sqlite"
        )
        path, _ = QFileDialog.getSaveFileName(
            self, "Export database", str(suggested), "SQLite database (*.sqlite)"
        )
        if not path:
            return
        self._begin("Exporting a copy of the database…", determinate=True)

        def work() -> Path:
            from app.database.backup import create_backup

            destination = Path(path)
            produced = create_backup(
                self.context.database_path, destination.parent, version=self.context.database_version()
            )
            produced.replace(destination)
            return destination

        worker: Worker = Worker(work)
        worker.signals.result.connect(
            lambda destination: (
                self._end(),
                self.banner.show_message(
                    f"Database exported to {Path(destination).name}.", StateKind.SUCCESS
                ),
            )
        )
        worker.signals.failed.connect(self._on_failed)
        run_in_background(worker)

    def _export_health(self) -> None:
        if self._health is None:
            self.toast("Assess the database first")
            return
        content = self.context.reports.build_health_report(
            self._health, database_version=self.context.database_version()
        )
        chosen = ExportDialog.choose(
            self,
            "database-health",
            title="Export health report",
            subtitle="Export the health assessment and every check behind it.",
        )
        if chosen is None:
            return
        path, fmt = chosen
        try:
            result = self.context.reports.generate(content, ReportFormat(fmt.value), path)
        except Exception as exc:  # noqa: BLE001 - shown in a dialog
            self.show_error(exc)
            return
        self.toast(f"Health report written to {result.path.name}")

    def _open_folder(self) -> None:
        path = self.context.database_path
        reveal_in_file_manager(path if path.exists() else path.parent)

    def _on_failed(self, exc: BaseException) -> None:
        self._end()
        self.banner.show_message("The operation did not complete.", StateKind.DANGER)
        self.show_error(exc)

    def on_theme_changed(self) -> None:
        for card in self.cards.values():
            card.refresh_icon()
        if self._health is not None:
            self._render_health(self._health)
        self._render_backups()
