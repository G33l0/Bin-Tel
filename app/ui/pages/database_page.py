"""Database management page."""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QLabel,
    QProgressBar,
    QPushButton,
    QWidget,
)

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
from app.utils.qt_helpers import hbox, reveal_in_file_manager
from app.workers.base import run_in_background
from app.workers.maintenance_worker import (
    BackupWorker,
    RebuildWorker,
    ReindexWorker,
    RestoreWorker,
    RollbackWorker,
    VerifyWorker,
)


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

        # -- the BIN list ----------------------------------------------------
        source = Card(self.surface, padding=18, spacing=12)
        source.body.addWidget(
            SectionHeader(
                "BIN list",
                "Your database is built from this file. Add rows to it, rebuild, and "
                "Bin-Tel is looking at the new data. The database it replaces is kept, "
                "so a rebuild is always reversible.",
                source,
            )
        )
        self.list_path_label = QLabel("", source)
        self.list_path_label.setProperty("role", "mono")
        self.list_path_label.setStyleSheet("font-size: 10pt;")
        self.list_path_label.setWordWrap(True)
        self.list_path_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        source.body.addWidget(self.list_path_label)

        self.list_status_label = QLabel("", source)
        self.list_status_label.setProperty("role", "muted")
        self.list_status_label.setWordWrap(True)
        source.body.addWidget(self.list_status_label)

        # Shown only when the list actually contains BINs shorter than a BIN,
        # because offering to pad a file that needs no padding invites someone
        # to tick it on a list where it would be wrong.
        self.pad_short_bins = QCheckBox(
            "Restore leading zeros a spreadsheet stripped from the BIN column",
            source,
        )
        self.pad_short_bins.setToolTip(
            "Only tick this if you know the list has been through a "
            "spreadsheet. 42410 and 042410 are different BINs."
        )
        self.pad_short_bins.hide()
        self.pad_short_bins.toggled.connect(lambda _: self.refresh_list_status())
        source.body.addWidget(self.pad_short_bins)

        source_row = hbox(spacing=10)
        for key, label, primary in (
            ("rebuild", "Rebuild from BIN list", True),
            ("check_list", "Check the list", False),
            ("open_list", "Open the list", False),
            ("choose_list", "Choose a CSV file…", False),
            ("rollback", "Roll back", False),
        ):
            button = QPushButton(label, source)
            button.setProperty("variant", "primary" if primary else "")
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setAccessibleName(label)
            self.buttons[key] = button
            source_row.addWidget(button)
        source_row.addStretch(1)
        source.body.addLayout(source_row)
        self.content.addWidget(source)

        # -- what Bin-Tel has noticed but not decided ------------------------
        self.learned_card = Card(self.surface, padding=18, spacing=10)
        self.learned_card.body.addWidget(
            SectionHeader(
                "Waiting for you",
                "Things Bin-Tel worked out or was told. None of this is in "
                "your database; each one is here because it needs your "
                "decision, not because it is going in.",
                parent=self.learned_card,
            )
        )
        self.learned_label = QLabel("", self.learned_card)
        self.learned_label.setWordWrap(True)
        self.learned_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self.learned_card.body.addWidget(self.learned_label)

        learned_row = hbox(spacing=10)
        for key, label, primary in (
            ("learn", "Look for more", False),
            ("approve_learned", "Approve all", True),
            ("reject_learned", "Dismiss all", False),
        ):
            button = QPushButton(label, self.learned_card)
            button.setProperty("variant", "primary" if primary else "")
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setAccessibleName(label)
            self.buttons[key] = button
            learned_row.addWidget(button)
        learned_row.addStretch(1)
        self.learned_card.body.addLayout(learned_row)
        self.learned_card.hide()
        self.content.addWidget(self.learned_card)

        self.buttons["rebuild"].clicked.connect(self.rebuild_from_list)
        self.buttons["check_list"].clicked.connect(self.check_list)
        self.buttons["open_list"].clicked.connect(self.open_list_location)
        self.buttons["choose_list"].clicked.connect(self.choose_list)
        self.buttons["rollback"].clicked.connect(self.roll_back)
        self.buttons["learn"].clicked.connect(self.gather_learning)
        self.buttons["approve_learned"].clicked.connect(
            lambda: self.decide_learned(approve=True)
        )
        self.buttons["reject_learned"].clicked.connect(
            lambda: self.decide_learned(approve=False)
        )

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
            self.refresh_list_status()
            self.learned_card.hide()
            return

        self.banner.hide()
        self.refresh_learned()
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
        self.refresh_list_status()

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
        # A rebuild does not need a database to already exist — it is how the
        # first one gets built — so it is gated on the list, not on `installed`.
        for key in ("rebuild", "check_list", "open_list", "choose_list"):
            self.buttons[key].setEnabled(enabled)
        self.buttons["rollback"].setEnabled(
            enabled and self.context.rebuilds.can_roll_back
        )

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

    # -- the BIN list ------------------------------------------------------
    @property
    def list_path(self) -> Path:
        """Whatever list the user chose, or the default working copy."""
        return self.context.config.bin_list_path()

    def refresh_list_status(self) -> None:
        """Describe the list without building anything from it."""
        from app.core.errors import BinTelError
        from app.services.bin_list import read_bin_list

        path = self.list_path
        self.list_path_label.setText(str(path))
        self.buttons["rollback"].setEnabled(self.context.rebuilds.can_roll_back)
        padding = self.pad_short_bins.isChecked()
        try:
            report = read_bin_list(path, pad_short_bins=padding)
        except BinTelError as exc:
            self.list_status_label.setText(f"{exc.message} {exc.detail or ''}".strip())
            self.buttons["rebuild"].setEnabled(False)
            return
        # Keep the option visible once it has been offered, so unticking it is
        # possible: a padded read reports no short BINs of its own.
        self.pad_short_bins.setVisible(bool(report.short_bins) or padding)
        self.list_status_label.setText(f"Ready to build — {report.summary}")
        self.buttons["rebuild"].setEnabled(True)

    # -- learning ----------------------------------------------------------
    def _learning_service(self, session):
        from app.services.learning_service import Authorization, LearningService

        return LearningService(
            session, Authorization.from_settings(self.context.config.settings)
        )

    def refresh_learned(self) -> None:
        """Show what is waiting, or hide the card when nothing is."""
        from app.services.learning_service import LearningService

        if not self.context.database.is_open:
            self.learned_card.hide()
            return
        with self.context.manager.session() as session:
            facts = LearningService(session).pending(limit=25)
            lines = [
                f"{'•' if fact.is_new_information else '!'}  {fact.subject_key} · "
                f"{fact.field}: {fact.current_value or UNKNOWN_DISPLAY} → "
                f"{fact.proposed_value}   ({fact.source_code})"
                for fact in facts
            ]
            contradictions = sum(1 for fact in facts if not fact.is_new_information)
        if not lines:
            self.learned_card.hide()
            return
        heading = f"{len(lines):,} waiting"
        if contradictions:
            heading += (
                f" — {contradictions:,} marked ! would overrule something you hold"
            )
        self.learned_label.setText(heading + "\n\n" + "\n".join(lines))
        self.learned_card.show()

    def gather_learning(self) -> None:
        """Look for more, using only evidence already in the database."""
        self._begin("Looking for what could be learned…")
        try:
            with self.context.manager.transaction() as session:
                service = self._learning_service(session)
                report = service.record(service.gather_local())
        finally:
            self._end()
        self.refresh_learned()
        self.toast(report.summary)

    def decide_learned(self, *, approve: bool) -> None:
        """Accept or dismiss everything waiting, after a confirmation."""
        with self.context.manager.session() as session:
            from app.services.learning_service import LearningService

            facts = LearningService(session).pending(limit=10_000)
            ids = [fact.id for fact in facts]
            overruling = sum(1 for fact in facts if not fact.is_new_information)
        if not ids:
            return
        if approve and overruling:
            # Bulk-approving a contradiction is exactly the click that puts a
            # wrong value into a database nobody re-checks, so it is the one
            # that has to be asked for out loud.
            confirmed = ConfirmDialog.ask(
                self,
                f"Overrule {overruling:,} value(s) you already hold?",
                f"{overruling:,} of these {len(ids):,} proposals contradict "
                "something already in your database rather than filling a gap. "
                "Approving them all replaces those values.",
                confirm_text="Approve all",
            )
            if not confirmed:
                return

        with self.context.manager.transaction() as session:
            service = self._learning_service(session)
            for fact_id in ids:
                if approve:
                    service.approve(fact_id, "approved in the app")
                else:
                    service.reject(fact_id, "dismissed in the app")
            applied = service.apply_approved().applied if approve else 0

        self.refresh_learned()
        self.toast(
            f"{applied:,} written into the database."
            if approve
            else f"{len(ids):,} dismissed."
        )

    def check_list(self) -> None:
        self.refresh_list_status()
        self.toast(self.list_status_label.text())

    def open_list_location(self) -> None:
        reveal_in_file_manager(self.list_path)

    def choose_list(self) -> None:
        """Point Bin-Tel at a BIN list anywhere on this machine."""
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
        self.refresh_list_status()
        self.toast(f"Now building from {Path(path).name}")

    def rebuild_from_list(self, *, allow_shrink: bool = False) -> None:
        self._begin("Rebuilding from the BIN list…")
        worker = RebuildWorker(
            self.context.rebuilds,
            self.list_path,
            allow_shrink=allow_shrink,
            pad_short_bins=self.pad_short_bins.isChecked(),
        )
        worker.signals.progress.connect(self._on_rebuild_progress)
        worker.signals.result.connect(self._on_rebuilt)
        worker.signals.failed.connect(self._on_rebuild_failed)
        run_in_background(worker)

    def _on_rebuild_progress(self, message: object) -> None:
        self.activity_label.setText(str(message))

    def _on_rebuilt(self, outcome) -> None:
        self._end()
        self.banner.show_message(
            f"Database {outcome.version} is live — {outcome.summary}", StateKind.SUCCESS
        )
        lines: list[str] = []
        if outcome.enrichment.total or outcome.enrichment.networks_ambiguous:
            lines.append(f"Filled in from evidence: {outcome.enrichment.summary}")
            lines.extend(f"• {example}" for example in outcome.enrichment.examples[:8])
        if outcome.problems:
            if lines:
                lines.append("")
            lines.append(f"{outcome.rejected:,} row(s) in the list were skipped:")
            lines.extend(f"• {problem}" for problem in outcome.problems[:12])
        if lines:
            self.report_card.show()
            self.report_label.setText("\n".join(lines))
        self.navigation_requested.emit("__database_reloaded__")

    def _on_rebuild_failed(self, exc: BaseException) -> None:
        from app.services.rebuild_service import ShrinkRefused

        self._end()
        if isinstance(exc, ShrinkRefused):
            # Losing most of the database is exactly the case worth stopping
            # for, and exactly the case where only the person can say whether
            # it was meant.
            confirmed = ConfirmDialog.ask(
                self,
                "Rebuild with far fewer BINs?",
                f"{exc.message}\n\n{exc.detail}",
                confirm_text="Rebuild anyway",
                destructive=True,
            )
            if confirmed:
                self.rebuild_from_list(allow_shrink=True)
            return
        self.banner.show_message("The rebuild did not complete.", StateKind.DANGER)
        self.show_error(exc)

    def roll_back(self) -> None:
        if not ConfirmDialog.ask(
            self,
            "Roll back to the previous database?",
            "The database the last rebuild replaced becomes the active one again. "
            "The current one is kept, so you can roll forward the same way.",
            confirm_text="Roll back",
        ):
            return
        self._begin("Rolling back…")
        worker = RollbackWorker(self.context.rebuilds)
        worker.signals.result.connect(self._on_rolled_back)
        worker.signals.failed.connect(self._on_failed)
        run_in_background(worker)

    def _on_rolled_back(self, _path: Path) -> None:
        self._end()
        self.banner.show_message(
            "Rolled back to the previous database.", StateKind.SUCCESS
        )
        self.navigation_requested.emit("__database_reloaded__")

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
        except Exception as exc:
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
        except Exception:
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
