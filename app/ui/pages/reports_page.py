"""Report Center — build, preview and export professional reports."""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QWidget,
)

from app.core.errors import ValidationError
from app.models.schemas import AdvancedQuery
from app.services.report_service import (
    ReportContent,
    ReportFormat,
    ReportType,
    available_formats,
)
from app.ui.dialogs.confirm_dialog import ConfirmDialog
from app.ui.pages.base_page import BasePage
from app.ui.themes.icons import IconProvider
from app.ui.widgets.cards import Card, SectionHeader
from app.ui.widgets.states import StateBanner, StateKind
from app.utils.formatting import format_bytes, format_relative
from app.utils.qt_helpers import expanding_spacer, grid, hbox, open_path, vbox
from app.workers.base import Worker, run_in_background


class ReportsPage(BasePage):
    """Choose a report, scope it, preview it, then export it."""

    key = "reports"
    title = "Reports"
    subtitle = "Build a professional report from your database and export it."

    def __init__(self, context, parent: QWidget | None = None) -> None:
        super().__init__(context, parent)
        self._content: ReportContent | None = None
        self._building = False

        self.banner = StateBanner("", StateKind.INFO, self.surface, dismissible=True)
        self.banner.hide()
        self.content.addWidget(self.banner)

        splitter = QSplitter(Qt.Orientation.Horizontal, self.surface)
        splitter.setChildrenCollapsible(False)

        # -- builder ---------------------------------------------------------
        builder = QWidget(splitter)
        builder_layout = vbox(builder, spacing=14)

        setup = Card(builder, padding=18, spacing=12)
        setup.body.addWidget(
            SectionHeader(
                "Report builder",
                "Reports contain BIN and issuer metadata only.",
                setup,
            )
        )

        form = QWidget(setup)
        form_grid = grid(form, spacing=10)

        self.type_combo = self._combo("Report type", setup)
        for report_type in ReportType:
            self.type_combo.addItem(report_type.label, report_type.value)
        self.type_combo.currentIndexChanged.connect(self._on_type_changed)

        self.format_combo = self._combo("Output format", setup)
        self._populate_formats()

        self.country_combo = self._combo("Country", setup)
        self.country_combo.addItem("All countries", None)
        self.network_combo = self._combo("Network", setup)
        self.network_combo.addItem("All networks", None)
        self.card_type_combo = self._combo("Card type", setup)
        self.card_type_combo.addItem("All card types", None)
        self.funding_combo = self._combo("Funding type", setup)
        self.funding_combo.addItem("All funding types", None)

        self.institution_field = QLineEdit(setup)
        self.institution_field.setPlaceholderText("Any institution")
        self.institution_field.setAccessibleName("Institution name")

        self.bin_field = QLineEdit(setup)
        self.bin_field.setPlaceholderText("Any BIN prefix, e.g. 4147")
        self.bin_field.setAccessibleName("BIN prefix")

        self.title_field = QLineEdit(setup)
        self.title_field.setPlaceholderText("Report title (optional)")
        self.title_field.setAccessibleName("Report title")

        fields: list[tuple[str, QWidget]] = [
            ("Report type", self.type_combo),
            ("Output format", self.format_combo),
            ("Country", self.country_combo),
            ("Network", self.network_combo),
            ("Card type", self.card_type_combo),
            ("Funding type", self.funding_combo),
            ("Institution", self.institution_field),
            ("BIN prefix", self.bin_field),
            ("Title", self.title_field),
        ]
        for index, (label_text, widget) in enumerate(fields):
            label = QLabel(label_text, form)
            label.setProperty("role", "fieldLabel")
            row, column = divmod(index, 2)
            form_grid.addWidget(label, row * 2, column)
            form_grid.addWidget(widget, row * 2 + 1, column)
        form_grid.setColumnStretch(0, 1)
        form_grid.setColumnStretch(1, 1)
        setup.body.addWidget(form)

        buttons = hbox(spacing=10)
        self.preview_button = QPushButton("Preview", setup)
        self.preview_button.clicked.connect(self._preview)
        buttons.addWidget(self.preview_button)
        self.generate_button = QPushButton("Generate and export", setup)
        self.generate_button.setProperty("variant", "primary")
        self.generate_button.clicked.connect(self._generate)
        buttons.addWidget(self.generate_button)
        self.save_template_button = QPushButton("Save as template", setup)
        self.save_template_button.setProperty("variant", "ghost")
        self.save_template_button.clicked.connect(self._save_template)
        buttons.addWidget(self.save_template_button)
        buttons.addItem(expanding_spacer())
        setup.body.addLayout(buttons)
        builder_layout.addWidget(setup)

        templates = Card(builder, padding=16, spacing=10)
        templates.body.addWidget(
            SectionHeader("Saved templates", "Reuse a report definition.", templates)
        )
        self.templates_list = QListWidget(templates)
        self.templates_list.setAccessibleName("Saved report templates")
        self.templates_list.setMaximumHeight(140)
        self.templates_list.itemDoubleClicked.connect(self._load_template)
        templates.body.addWidget(self.templates_list)
        template_actions = hbox(spacing=8)
        self.load_template_button = QPushButton("Load", templates)
        self.load_template_button.setProperty("variant", "ghost")
        self.load_template_button.clicked.connect(
            lambda: self._load_template(self.templates_list.currentItem())
        )
        template_actions.addWidget(self.load_template_button)
        self.delete_template_button = QPushButton("Delete", templates)
        self.delete_template_button.setProperty("variant", "ghost")
        self.delete_template_button.clicked.connect(self._delete_template)
        template_actions.addWidget(self.delete_template_button)
        template_actions.addItem(expanding_spacer())
        templates.body.addLayout(template_actions)
        builder_layout.addWidget(templates)
        splitter.addWidget(builder)

        # -- preview and history ----------------------------------------------
        right = QWidget(splitter)
        right_layout = vbox(right, spacing=14)

        preview_card = Card(right, padding=16, spacing=10)
        preview_card.body.addWidget(
            SectionHeader("Preview", "What the report will contain.", preview_card)
        )
        self.preview_view = QPlainTextEdit(preview_card)
        self.preview_view.setReadOnly(True)
        self.preview_view.setAccessibleName("Report preview")
        self.preview_view.setPlaceholderText(
            "Choose a report type and press Preview to see what it will contain."
        )
        self.preview_view.setStyleSheet(
            "font-family: 'JetBrains Mono','SF Mono','Consolas','DejaVu Sans Mono',monospace;"
            " font-size: 9pt;"
        )
        self.preview_view.setMinimumHeight(320)
        preview_card.body.addWidget(self.preview_view, 1)
        right_layout.addWidget(preview_card, 1)

        history_card = Card(right, padding=16, spacing=10)
        history_card.body.addWidget(
            SectionHeader("Recent reports", parent=history_card)
        )
        self.history_list = QListWidget(history_card)
        self.history_list.setAccessibleName("Recently generated reports")
        self.history_list.setMaximumHeight(150)
        self.history_list.itemDoubleClicked.connect(self._open_report)
        history_card.body.addWidget(self.history_list)
        right_layout.addWidget(history_card)
        splitter.addWidget(right)
        splitter.setSizes([500, 560])

        self.content.addWidget(splitter, 1)

    @staticmethod
    def _combo(name: str, parent: QWidget) -> QComboBox:
        combo = QComboBox(parent)
        combo.setAccessibleName(name)
        combo.setMinimumWidth(180)
        return combo

    def _populate_formats(self) -> None:
        self.format_combo.blockSignals(True)
        current = self.format_combo.currentData()
        self.format_combo.clear()
        for fmt in available_formats():
            self.format_combo.addItem(fmt.display, fmt.value)
        index = self.format_combo.findData(current or ReportFormat.CSV.value)
        self.format_combo.setCurrentIndex(max(0, index))
        self.format_combo.blockSignals(False)

    # -- lifecycle ---------------------------------------------------------
    def on_first_show(self) -> None:
        self._populate_filters()

    def refresh(self) -> None:
        self._populate_formats()
        self._reload_templates()
        self._reload_history()

    def _populate_filters(self) -> None:
        if not self.context.database.is_open:
            return
        worker: Worker = Worker(self.context.search.filter_values)
        worker.signals.result.connect(self._fill_filters)
        run_in_background(worker)

    def _fill_filters(self, values: dict) -> None:
        for combo, key, placeholder in (
            (self.country_combo, "country", "All countries"),
            (self.network_combo, "network", "All networks"),
            (self.card_type_combo, "card_type", "All card types"),
            (self.funding_combo, "funding_type", "All funding types"),
        ):
            combo.blockSignals(True)
            combo.clear()
            combo.addItem(placeholder, None)
            for code, label in values.get(key, []):
                combo.addItem(label, code)
            combo.blockSignals(False)

    def _on_type_changed(self) -> None:
        report_type = ReportType(self.type_combo.currentData())
        scoped = report_type is ReportType.SEARCH_RESULTS
        for widget in (
            self.country_combo,
            self.network_combo,
            self.card_type_combo,
            self.funding_combo,
            self.institution_field,
            self.bin_field,
        ):
            widget.setEnabled(scoped)
        self.preview_view.clear()
        self._content = None

    # -- building ----------------------------------------------------------
    def _query(self) -> AdvancedQuery:
        return AdvancedQuery(
            country_code=self.country_combo.currentData(),
            network_code=self.network_combo.currentData(),
            card_type=self.card_type_combo.currentData(),
            funding_type=self.funding_combo.currentData(),
            institution=self.institution_field.text().strip() or None,
            bin_prefix=self.bin_field.text().strip() or None,
        )

    def _selected_format(self) -> ReportFormat:
        return ReportFormat(self.format_combo.currentData() or ReportFormat.CSV.value)

    def _build(self, on_ready) -> None:
        if self._building or not self.context.database.is_open:
            if not self.context.database.is_open:
                self.banner.show_message(
                    "The database is not open, so there is nothing to report on.",
                    StateKind.WARNING,
                )
            return
        report_type = ReportType(self.type_combo.currentData())
        self._building = True
        self.preview_button.setEnabled(False)
        self.generate_button.setEnabled(False)

        version = self.context.database_version()
        title = self.title_field.text().strip()
        query = self._query()
        cap = self.context.search.export_cap()
        maximum = self.context.config.settings.reports.max_report_rows
        limit = maximum if cap is None else min(cap, maximum)

        def work() -> ReportContent:
            reports = self.context.reports
            if report_type is ReportType.ANALYTICS:
                return reports.build_analytics_report(
                    self.context.analytics.snapshot(version=version), database_version=version
                )
            if report_type is ReportType.DATABASE_HEALTH:
                return reports.build_health_report(
                    self.context.health.evaluate(quick=True), database_version=version
                )
            if report_type is ReportType.WATCHLIST:
                alerts = self.context.watchlists.events(limit=2000)
                return reports.build_watchlist_report(
                    "All watchlists", alerts, database_version=version
                )
            if report_type is ReportType.INSTITUTION_PROFILE:
                result = self.context.banks.search(
                    self.institution_field.text().strip() or "bank", limit=1
                )
                if not result.found or result.best is None:
                    raise ValidationError(
                        "Enter an institution name to build a profile report."
                    )
                rows = self.context.banks.all_bins(result.best.id)[:limit]
                return reports.build_institution_report(
                    result.best,
                    rows,
                    stats=self.context.banks.stats(result.best.id).model_dump(),
                    database_version=version,
                )
            if report_type is ReportType.BIN_RECORD:
                digits = self.bin_field.text().strip()
                lookup = self.context.lookup.lookup(digits)
                if not lookup.found or lookup.best is None:
                    raise ValidationError(
                        "Enter a BIN that exists in the database to build a BIN report."
                    )
                return reports.build_bin_report(lookup.best, database_version=version)

            total = self.context.search.count(query)
            rows = self.context.search.export_rows(query)[:limit]
            return reports.build_search_report(
                query, rows, title=title, database_version=version, total=total
            )

        worker: Worker = Worker(work)
        worker.signals.result.connect(on_ready)
        worker.signals.failed.connect(self._on_failed)
        worker.signals.finished.connect(self._on_build_finished)
        run_in_background(worker)

    def _on_build_finished(self) -> None:
        self._building = False
        self.preview_button.setEnabled(True)
        self.generate_button.setEnabled(True)

    def _on_failed(self, exc: BaseException) -> None:
        if isinstance(exc, ValidationError):
            self.banner.show_message(exc.message, StateKind.WARNING)
            return
        self.show_error(exc)

    def _preview(self) -> None:
        def show(content: ReportContent) -> None:
            self._content = content
            self.preview_view.setPlainText(self.context.reports.preview(content, limit=40))
            self.banner.hide()

        self._build(show)

    def _generate(self) -> None:
        def write(content: ReportContent) -> None:
            self._content = content
            fmt = self._selected_format()
            suggested = self.context.config.reports_path() / self.context.reports.suggested_filename(
                content.title, fmt
            )
            path, _ = QFileDialog.getSaveFileName(
                self, "Save report", str(suggested), fmt.label
            )
            if not path:
                return
            destination = Path(path)
            if not destination.suffix:
                destination = destination.with_suffix(fmt.extension)
            try:
                result = self.context.reports.generate(content, fmt, destination)
            except Exception as exc:
                self.show_error(exc)
                return
            self.context.workspace.record_report(
                content.title,
                content.report_type.value,
                fmt.value,
                str(result.path),
                row_count=result.row_count,
                size_bytes=result.size_bytes,
                database_version=content.database_version,
            )
            self.preview_view.setPlainText(self.context.reports.preview(content, limit=40))
            self._reload_history()
            self.banner.show_message(
                f"Report written to {result.path.name} ({format_bytes(result.size_bytes)}).",
                StateKind.SUCCESS,
                action_text="Open",
            )
            try:
                self.banner.action_triggered.disconnect()
            except TypeError:
                pass
            self.banner.action_triggered.connect(lambda: open_path(result.path))
            if self.context.config.settings.reports.open_after_export:
                open_path(result.path)
            self.toast(f"Report saved: {result.path.name}")

        self._build(write)

    # -- templates ---------------------------------------------------------
    def _save_template(self) -> None:
        from app.ui.dialogs.watchlist_dialog import CreateWatchlistDialog

        dialog = CreateWatchlistDialog(self)
        dialog.setWindowTitle("Bin-Tel — Save report template")
        dialog.name_field.setPlaceholderText("US credit portfolio")
        from PyQt6.QtWidgets import QDialog

        if dialog.exec() != QDialog.DialogCode.Accepted or not dialog.watchlist_name:
            return
        try:
            self.context.workspace.save_template(
                dialog.watchlist_name,
                str(self.type_combo.currentData()),
                str(self.format_combo.currentData()),
                self._query().model_dump_json(),
                dialog.description,
            )
        except ValidationError as exc:
            self.banner.show_message(exc.message, StateKind.WARNING)
            return
        self._reload_templates()
        self.toast(f"Template “{dialog.watchlist_name}” saved")

    def _reload_templates(self) -> None:
        self.templates_list.clear()
        provider = IconProvider.instance()
        for template in self.context.workspace.templates():
            used = (
                f"used {template.use_count}×"
                if template.use_count
                else "never used"
            )
            item = QListWidgetItem(
                f"{template.name}\n{ReportType(template.report_type).label} · "
                f"{template.output_format.upper()} · {used}",
                self.templates_list,
            )
            item.setData(Qt.ItemDataRole.UserRole, template.id)
            item.setIcon(provider.icon("filter", provider.theme.text_secondary, 15))
            self.templates_list.addItem(item)

    def _load_template(self, item: QListWidgetItem | None) -> None:
        if item is None:
            return
        identifier = item.data(Qt.ItemDataRole.UserRole)
        template = next(
            (row for row in self.context.workspace.templates() if row.id == identifier), None
        )
        if template is None:
            return
        index = self.type_combo.findData(template.report_type)
        if index >= 0:
            self.type_combo.setCurrentIndex(index)
        index = self.format_combo.findData(template.output_format)
        if index >= 0:
            self.format_combo.setCurrentIndex(index)
        try:
            query = AdvancedQuery.model_validate_json(template.criteria)
        except Exception:
            query = AdvancedQuery()
        for combo, value in (
            (self.country_combo, query.country_code),
            (self.network_combo, query.network_code),
            (self.card_type_combo, query.card_type),
            (self.funding_combo, query.funding_type),
        ):
            position = combo.findData(value)
            combo.setCurrentIndex(position if position >= 0 else 0)
        self.institution_field.setText(query.institution or "")
        self.bin_field.setText(query.bin_prefix or "")
        self.context.workspace.record_template_use(template.id)
        self._reload_templates()
        self.toast(f"Loaded “{template.name}”")

    def _delete_template(self) -> None:
        item = self.templates_list.currentItem()
        if item is None:
            return
        identifier = item.data(Qt.ItemDataRole.UserRole)
        if identifier is None:
            return
        if not ConfirmDialog.ask(
            self,
            "Delete this template?",
            "The saved report definition is removed. Reports you already exported are "
            "not affected.",
            confirm_text="Delete template",
            destructive=True,
        ):
            return
        self.context.workspace.delete_template(int(identifier))
        self._reload_templates()

    # -- history -----------------------------------------------------------
    def _reload_history(self) -> None:
        self.history_list.clear()
        provider = IconProvider.instance()
        for report in self.context.workspace.recent_reports(limit=20):
            item = QListWidgetItem(
                f"{report.title}\n{report.output_format.upper()} · "
                f"{report.row_count:,} rows · {format_bytes(report.size_bytes)} · "
                f"{format_relative(report.created_at)}",
                self.history_list,
            )
            item.setData(Qt.ItemDataRole.UserRole, report.path)
            item.setIcon(provider.icon("export", provider.theme.text_secondary, 15))
            item.setToolTip(report.path)
            self.history_list.addItem(item)

    def _open_report(self, item: QListWidgetItem | None) -> None:
        if item is None:
            return
        path = Path(str(item.data(Qt.ItemDataRole.UserRole)))
        if not path.exists():
            self.toast("That report file has been moved or deleted")
            return
        open_path(path)

    def on_theme_changed(self) -> None:
        self._reload_templates()
        self._reload_history()
