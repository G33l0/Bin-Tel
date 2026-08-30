"""Bank / financial-institution lookup page."""

from __future__ import annotations

from PyQt6.QtWidgets import QComboBox, QLabel, QStackedWidget, QWidget

from app.core.errors import ValidationError
from app.models.schemas import (
    BankLookupResult,
    BinFilters,
    BinRow,
    InstitutionDetail,
    InstitutionStats,
    Page,
    PageRequest,
)
from app.ui.dialogs.export_dialog import ExportDialog
from app.ui.pages.base_page import BasePage
from app.ui.widgets.bank_result_view import BankResultView
from app.ui.widgets.cards import Card
from app.ui.widgets.search_box import SearchBox
from app.ui.widgets.states import EmptyState, ErrorState, LoadingState
from app.utils.qt_helpers import expanding_spacer, hbox, shortcut
from app.workers.base import run_in_background
from app.workers.search_worker import (
    AllBinsWorker,
    BankSearchWorker,
    BinPageWorker,
    FilterOptionsWorker,
    InstitutionStatsWorker,
)

PLACEHOLDER = "Search a bank or financial institution — for example JPMorgan Chase"


class BankLookupPage(BasePage):
    """Find an institution, then browse and export every BIN it issues."""

    key = "bank_lookup"
    title = "Bank Lookup"
    subtitle = "Find a financial institution and every BIN/IIN associated with it."

    def __init__(self, context, parent: QWidget | None = None) -> None:
        super().__init__(context, parent)
        self._matches: list[InstitutionDetail] = []
        self._current: InstitutionDetail | None = None
        self._filters = BinFilters()
        self._last_query = ""

        search_card = Card(self.surface, padding=20, spacing=10)
        self.search = SearchBox(PLACEHOLDER, search_card)
        self.search.search_requested.connect(self.perform_search)
        self.search.cleared.connect(self.clear)
        search_card.body.addWidget(self.search)

        options_row = hbox(spacing=10)
        country_label = QLabel("Country", search_card)
        country_label.setProperty("role", "fieldLabel")
        options_row.addWidget(country_label)
        self.country_filter = QComboBox(search_card)
        self.country_filter.setAccessibleName("Restrict the search to one country")
        self.country_filter.addItem("Any country", None)
        self.country_filter.setMinimumWidth(190)
        self.country_filter.currentIndexChanged.connect(self._on_country_changed)
        options_row.addWidget(self.country_filter)
        options_row.addItem(expanding_spacer())
        search_card.body.addLayout(options_row)
        self.content.addWidget(search_card)

        # -- result surfaces ------------------------------------------------
        self.stack = QStackedWidget(self.surface)
        self.content.addWidget(self.stack, 1)

        self.idle_state = EmptyState(
            "Search for an institution",
            "Bin-Tel matches the institution name, its legal name and any recorded "
            "alias, so partial and historical names both work.",
            self.stack,
            icon_name="bank-lookup",
        )
        self.loading_state = LoadingState("Searching institutions…", self.stack)
        self.empty_state = EmptyState(
            "No institution found",
            "",
            self.stack,
            action_text="Clear search",
            on_action=self.clear,
        )
        self.error_state = ErrorState(parent=self.stack)
        self.error_state.retry_requested.connect(lambda: self.perform_search(self._last_query))

        self.result_view = BankResultView(
            self.stack, page_size=context.config.settings.search.results_per_page
        )
        self.result_view.page_requested.connect(self._load_page)
        self.result_view.filters_changed.connect(self._on_filters_changed)
        self.result_view.export_requested.connect(self._export_table)
        self.result_view.match_selected.connect(self.select_institution)
        self.result_view.copied.connect(self.toast)
        self.result_view.export_csv_button.clicked.connect(lambda: self._export_table_as("csv"))
        self.result_view.export_json_button.clicked.connect(lambda: self._export_table_as("json"))

        for widget in (
            self.idle_state,
            self.loading_state,
            self.empty_state,
            self.error_state,
            self.result_view,
        ):
            self.stack.addWidget(widget)
        self.stack.setCurrentWidget(self.idle_state)

        shortcut(self, "Ctrl+K", self.focus_search)

    # -- lifecycle --------------------------------------------------------
    def on_shown(self) -> None:
        super().on_shown()
        settings = self.context.config.settings.search
        self.search.set_behavior(settings.behavior, settings.search_delay_ms)
        self.result_view.table.set_page_size(settings.results_per_page)
        if self.stack.currentWidget() is self.idle_state:
            self.focus_search()

    def refresh(self) -> None:
        """Populated on demand only."""

    def focus_search(self) -> None:
        self.search.focus()

    def clear(self) -> None:
        self._matches = []
        self._current = None
        self._last_query = ""
        self.stack.setCurrentWidget(self.idle_state)

    # -- search -----------------------------------------------------------
    def perform_search(self, query: str) -> None:
        query = (query or "").strip()
        if not query:
            self.clear()
            return
        self._last_query = query
        self.search.set_text(query)

        if not self.context.database.is_open:
            self.error_state.configure(
                "Database not available",
                "The Bin-Tel database is not open yet. Install or verify it from the "
                "Database page, then try again.",
                retryable=False,
            )
            self.stack.setCurrentWidget(self.error_state)
            return

        self.stack.setCurrentWidget(self.loading_state)
        self.loading_state.set_message(f"Searching for “{query}”…")

        worker = BankSearchWorker(
            self.context.banks,
            query,
            limit=40,
            country_code=self.country_filter.currentData(),
        )
        worker.signals.result.connect(self._on_result)
        worker.signals.failed.connect(self._on_failed)
        run_in_background(worker)

    def _on_result(self, result: BankLookupResult) -> None:
        if not result.found:
            self.empty_state.configure(
                "No institution found",
                f"Nothing in your database matches “{result.query}”. Try a shorter name, "
                "or check for a database update.",
                action_text="Clear search",
            )
            self.stack.setCurrentWidget(self.empty_state)
            return

        self._matches = list(result.matches)
        self.status_message.emit(
            f"{len(self._matches)} institution(s) matched in {result.elapsed_ms:.0f} ms"
        )
        settings = self.context.config.settings.search
        self.context.config.state.record_search(result.query, settings.max_history)
        self.context.config.save_state()
        self.select_institution(self._matches[0].id)

    def _on_failed(self, exc: BaseException) -> None:
        if isinstance(exc, ValidationError):
            self.search.set_error(True)
            self.empty_state.configure(
                "Search term too short", exc.message, action_text="Clear search"
            )
            self.stack.setCurrentWidget(self.empty_state)
            return
        from app.core.errors import friendly_message, friendly_title, is_retryable

        self.error_state.configure(
            friendly_title(exc), friendly_message(exc), retryable=is_retryable(exc)
        )
        self.stack.setCurrentWidget(self.error_state)

    # -- institution ------------------------------------------------------
    def select_institution(self, institution_id: int) -> None:
        """Show *institution_id*, fetching it if it was not in the last search."""
        match = next((item for item in self._matches if item.id == institution_id), None)
        if match is None:
            detail = self.context.banks.get(institution_id)
            if detail is None:
                self.empty_state.configure(
                    "Institution not found",
                    "That institution is no longer in the database.",
                    action_text="Clear search",
                )
                self.stack.setCurrentWidget(self.empty_state)
                return
            match = detail
            self._matches = [detail, *self._matches]

        self._current = match
        self._filters = BinFilters()
        self.result_view.show_institution(match, self._matches)
        self.stack.setCurrentWidget(self.result_view)

        stats_worker = InstitutionStatsWorker(self.context.banks, match.id)
        stats_worker.signals.result.connect(self._on_stats)
        run_in_background(stats_worker)

        options_worker = FilterOptionsWorker(self.context.banks, match.id)
        options_worker.signals.result.connect(self.result_view.set_filter_options)
        run_in_background(options_worker)

        self._load_page(match.id, self.result_view.current_request(1))

    def _on_stats(self, stats: InstitutionStats) -> None:
        self.result_view.show_stats(stats)

    def _load_page(self, institution_id: int, request: PageRequest) -> None:
        worker = BinPageWorker(self.context.banks, institution_id, request, self._filters)
        worker.signals.result.connect(self._on_page)
        worker.signals.failed.connect(self._on_failed)
        run_in_background(worker)

    def _on_page(self, page: Page[BinRow]) -> None:
        self.result_view.show_page(page)

    def _on_filters_changed(self, institution_id: int, filters: BinFilters) -> None:
        self._filters = filters
        self._load_page(institution_id, self.result_view.current_request(1))

    # -- export -----------------------------------------------------------
    def _export_table(self, institution_id: int, selection_only: bool) -> None:
        if self._current is None:
            return
        if selection_only:
            rows = self.result_view.selected_rows()
            if not rows:
                self.toast("Select one or more rows first")
                return
            self._save_rows(rows)
            return

        worker = AllBinsWorker(self.context.banks, institution_id)
        worker.signals.result.connect(self._save_rows)
        worker.signals.failed.connect(lambda exc: self.show_error(exc))
        run_in_background(worker)

    def _export_table_as(self, extension: str) -> None:
        """Shortcut buttons: export everything in a fixed format."""
        if self._current is None:
            return
        from app.services.export_service import ExportFormat

        fmt = ExportFormat.CSV if extension == "csv" else ExportFormat.JSON
        worker = AllBinsWorker(self.context.banks, self._current.id)
        worker.signals.result.connect(lambda rows, chosen=fmt: self._save_rows(rows, chosen))
        worker.signals.failed.connect(lambda exc: self.show_error(exc))
        run_in_background(worker)

    def _save_rows(self, rows: list[BinRow], fmt: object | None = None) -> None:
        if self._current is None or not rows:
            self.toast("There is nothing to export")
            return
        from pathlib import Path

        from app.core.paths import get_paths
        from app.services.export_service import ExportFormat, ExportService

        name = self._current.display_name
        if fmt is None:
            chosen = ExportDialog.choose(
                self,
                name,
                title="Export institution BINs",
                subtitle=f"Export {len(rows):,} BIN record(s) for {name}.",
            )
            if chosen is None:
                return
            path, export_format = chosen
        else:
            export_format = fmt  # type: ignore[assignment]
            from PyQt6.QtWidgets import QFileDialog

            suggested = get_paths().exports_dir / ExportService.suggested_filename(
                name, export_format  # type: ignore[arg-type]
            )
            selected, _ = QFileDialog.getSaveFileName(
                self, "Export", str(suggested), export_format.label  # type: ignore[union-attr]
            )
            if not selected:
                return
            path = Path(selected)
            if not path.suffix:
                path = path.with_suffix(export_format.extension)  # type: ignore[union-attr]

        try:
            self.context.exports.export_rows(
                rows, path, export_format, title=f"{name} — BIN records"  # type: ignore[arg-type]
            )
        except Exception as exc:  # noqa: BLE001 - shown in a dialog
            self.show_error(exc)
            return
        self.toast(f"Exported {len(rows):,} record(s) to {path.name}")
        _ = ExportFormat  # keeps the import meaningful for type checkers

    # -- misc -------------------------------------------------------------
    def _on_country_changed(self) -> None:
        if self._last_query:
            self.perform_search(self._last_query)

    def set_country_options(self, options: list[tuple[str, str]]) -> None:
        current = self.country_filter.currentData()
        self.country_filter.blockSignals(True)
        self.country_filter.clear()
        self.country_filter.addItem("Any country", None)
        for code, label in options:
            self.country_filter.addItem(label, code)
        index = self.country_filter.findData(current)
        self.country_filter.setCurrentIndex(max(0, index))
        self.country_filter.blockSignals(False)

    def on_first_show(self) -> None:
        if not self.context.database.is_open:
            return
        worker = FilterOptionsWorker(self.context.banks, None)
        worker.signals.result.connect(
            lambda options: self.set_country_options(options.get("country", []))
        )
        run_in_background(worker)

    def on_theme_changed(self) -> None:
        self.search.refresh_icon()
        self.idle_state.refresh_icon()
        self.empty_state.refresh_icon()
        self.error_state.refresh_icon()
        self.result_view.refresh_theme()
