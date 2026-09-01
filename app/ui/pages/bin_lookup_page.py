"""BIN / IIN lookup page."""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QLabel, QPushButton, QTabWidget, QWidget

from app.core.config import SearchBehavior
from app.core.errors import ValidationError
from app.models.schemas import (
    AdvancedQuery,
    AdvancedSearchResult,
    BinLookupResult,
    BinRecord,
    BinRow,
    PageRequest,
)
from app.models.user_entities import FavoriteKind, SearchKind, WatchTargetType
from app.ui.dialogs.export_dialog import ExportDialog
from app.ui.dialogs.watchlist_dialog import AddToWatchlistDialog, CreateWatchlistDialog
from app.ui.pages.base_page import BasePage
from app.ui.widgets.adaptive_stack import AdaptiveStack
from app.ui.widgets.advanced_search import AdvancedSearchPanel
from app.ui.widgets.bin_result_card import BinResultCard
from app.ui.widgets.cards import Card
from app.ui.widgets.external_reading_panel import ExternalReadingPanel
from app.ui.widgets.search_box import SearchBox
from app.ui.widgets.states import EmptyState, ErrorState, LoadingState, StateKind
from app.utils.qt_helpers import expanding_spacer, hbox, shortcut, vbox
from app.workers.base import Worker, run_in_background
from app.workers.search_worker import BinSearchWorker

PLACEHOLDER = "Enter a BIN or IIN — for example 414720"


class BinLookupPage(BasePage):
    """Search a BIN, then present the record it resolves to."""

    key = "bin_lookup"
    title = "BIN Lookup"
    subtitle = "Resolve a Bank Identification Number to its issuing institution and card attributes."

    def __init__(self, context, parent: QWidget | None = None) -> None:
        super().__init__(context, parent)
        self._worker: BinSearchWorker | None = None
        self._last_query = ""

        self.tabs = QTabWidget(self.surface)
        self.tabs.setDocumentMode(True)
        self.content.addWidget(self.tabs, 1)

        quick = QWidget(self.tabs)
        quick_layout = vbox(quick, margins=(0, 14, 0, 0), spacing=16)
        self.tabs.addTab(quick, "Quick lookup")

        search_card = Card(quick, padding=20, spacing=10)
        self.search = SearchBox(PLACEHOLDER, search_card)
        self.search.search_requested.connect(self.perform_search)
        self.search.cleared.connect(self.clear)
        search_card.body.addWidget(self.search)

        self.hint = QLabel(
            "Bin-Tel searches issuer identification numbers only — never full card numbers.",
            search_card,
        )
        self.hint.setProperty("role", "muted")
        search_card.body.addWidget(self.hint)
        quick_layout.addWidget(search_card)

        # -- result surfaces ------------------------------------------------
        self.stack = AdaptiveStack(quick)
        quick_layout.addWidget(self.stack, 1)

        # -- second opinion --------------------------------------------------
        # Outside the stack deliberately: the most useful moment to ask an
        # external service is when *your* list has no record, and that is a
        # different stack page from a result.
        self.second_opinion_row = QWidget(quick)
        second_opinion_layout = hbox(self.second_opinion_row, spacing=10)
        self.binlist_button = QPushButton("Check binlist.net", self.second_opinion_row)
        self.binlist_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.binlist_button.setAccessibleName("Check this BIN against binlist.net")
        self.binlist_button.clicked.connect(self._check_binlist)
        second_opinion_layout.addWidget(self.binlist_button)
        self.binlist_status = QLabel("", self.second_opinion_row)
        self.binlist_status.setProperty("role", "muted")
        second_opinion_layout.addWidget(self.binlist_status)
        second_opinion_layout.addItem(expanding_spacer())
        self.second_opinion_row.hide()
        quick_layout.addWidget(self.second_opinion_row)

        self.external_panel = ExternalReadingPanel(quick)
        self.external_panel.copy_requested.connect(self._copy_external_row)
        quick_layout.addWidget(self.external_panel)

        self.idle_state = EmptyState(
            "Search for a BIN",
            "Type a 6- or 8-digit issuer identification number and press Enter. "
            "Press Ctrl+K from anywhere to jump straight to the search field.",
            self.stack,
            icon_name="bin-lookup",
        )
        self.loading_state = LoadingState("Searching the local database…", self.stack)
        self.empty_state = EmptyState(
            "No record found",
            "",
            self.stack,
            icon_name="empty-box",
            action_text="Clear search",
            on_action=self.clear,
        )
        self.error_state = ErrorState(parent=self.stack)
        self.error_state.retry_requested.connect(lambda: self.perform_search(self._last_query))

        self.result_holder = QWidget(self.stack)
        result_layout = vbox(self.result_holder, spacing=14)
        self.match_note = QLabel("", self.result_holder)
        self.match_note.setProperty("role", "muted")
        self.match_note.setVisible(False)
        result_layout.addWidget(self.match_note)

        self.result_card = BinResultCard(self.result_holder)
        self.result_card.copied.connect(self.toast)
        self.result_card.export_requested.connect(self._export)
        self.result_card.institution_selected.connect(self._open_institution)
        self.result_card.watch_requested.connect(self._watch_record)
        self.result_card.favorite_toggled.connect(self._toggle_favorite)
        result_layout.addWidget(self.result_card, 1)

        for widget in (
            self.idle_state,
            self.loading_state,
            self.empty_state,
            self.error_state,
            self.result_holder,
        ):
            self.stack.addWidget(widget)
        self.stack.setCurrentWidget(self.idle_state)

        # -- advanced search tab ---------------------------------------------
        advanced = QWidget(self.tabs)
        advanced_layout = vbox(advanced, margins=(0, 14, 0, 0), spacing=0)
        self.advanced_panel = AdvancedSearchPanel(
            advanced, page_size=context.config.settings.search.results_per_page
        )
        self.advanced_panel.search_requested.connect(self._run_advanced)
        self.advanced_panel.save_requested.connect(self._save_search)
        self.advanced_panel.export_requested.connect(self._export_advanced)
        self.advanced_panel.copied.connect(self.toast)
        advanced_layout.addWidget(self.advanced_panel, 1)
        self.tabs.addTab(advanced, "Advanced search")

        # Connected last: adding a tab emits currentChanged, and the handler
        # touches widgets that only exist once both tabs are built.
        self.tabs.currentChanged.connect(self._on_tab_changed)

        shortcut(self, "Ctrl+K", self.focus_search)

    # -- lifecycle --------------------------------------------------------
    def on_shown(self) -> None:
        super().on_shown()
        settings = self.context.config.settings.search
        self.search.set_behavior(settings.behavior, settings.search_delay_ms)
        self.search.set_history(
            self.context.workspace.recent_terms(SearchKind.BIN, settings.max_history)
        )
        self.focus_search()

    def on_first_show(self) -> None:
        self._load_filter_values()

    def on_database_changed(self) -> None:
        super().on_database_changed()
        self._load_filter_values()

    def _load_filter_values(self) -> None:
        if not self.context.database.is_open:
            return
        worker: Worker = Worker(self.context.search.filter_values)
        worker.signals.result.connect(self.advanced_panel.set_filter_options)
        run_in_background(worker)

    def _on_tab_changed(self, index: int) -> None:
        if self.tabs.count() < 2:
            return
        if index == 1:
            self.refresh()
        else:
            self.focus_search()

    def refresh(self) -> None:
        self.tabs.setTabToolTip(1, "Combine any criteria in one query")
        self.advanced_panel.set_page_size(
            self.context.config.settings.search.results_per_page
        )
        self._refresh_second_opinion()

    # -- second opinion ----------------------------------------------------
    @property
    def _binlist_enabled(self) -> bool:
        return self.context.config.settings.external.binlist_enabled

    def _refresh_second_opinion(self) -> None:
        """Show the button only when it is switched on and there is a query."""
        available = self._binlist_enabled and bool(self._last_query)
        self.second_opinion_row.setVisible(available)
        if not self._binlist_enabled:
            self.external_panel.clear()
            return
        remaining = self.context.binlist.remaining()
        self.binlist_button.setEnabled(remaining > 0 and bool(self._last_query))
        self.binlist_status.setText(self.context.binlist.status_line())

    def _check_binlist(self) -> None:
        """Ask binlist.net about the current query, on the worker thread."""
        query = self._last_query
        if not query:
            return
        self.binlist_button.setEnabled(False)
        self.binlist_status.setText("Asking binlist.net…")
        worker: Worker = Worker(self.context.binlist.lookup, query)
        worker.signals.result.connect(self._on_external_reading)
        worker.signals.failed.connect(self._on_external_failed)
        run_in_background(worker)

    def _on_external_reading(self, reading) -> None:
        self._refresh_second_opinion()
        if reading is None:
            self.external_panel.show_message(
                f"binlist.net has no record for {self._last_query} either.",
                StateKind.INFO,
            )
            return
        # Disagreements are listed, never resolved: the local list stays the
        # authority and this is somebody else's reading of the same BIN.
        record = self.result_card.record
        self.external_panel.show_reading(
            reading,
            differences=reading.differences_from(record),
            allowance=self.context.binlist.status_line(),
        )

    def _on_external_failed(self, exc: BaseException) -> None:
        from app.providers.binlist import RateLimited

        self._refresh_second_opinion()
        message = getattr(exc, "message", None) or str(exc)
        self.external_panel.show_message(
            message,
            StateKind.WARNING if isinstance(exc, RateLimited) else StateKind.DANGER,
        )

    def _copy_external_row(self, text: str) -> None:
        from PyQt6.QtWidgets import QApplication

        QApplication.clipboard().setText(text)
        self.toast("Copied — paste it into data/bin-list.csv, then rebuild")

    def focus_search(self) -> None:
        self.search.focus()

    def clear(self) -> None:
        self._last_query = ""
        self.search.set_error(False)
        self.result_card.clear()
        self.external_panel.clear()
        self._refresh_second_opinion()
        self.stack.setCurrentWidget(self.idle_state)

    # -- search -----------------------------------------------------------
    def perform_search(self, query: str) -> None:
        query = (query or "").strip()
        if not query:
            self.clear()
            return
        self._last_query = query
        self.search.set_text(query)
        # A reading belongs to the BIN it was fetched for, so a new search
        # drops it rather than leaving it beside a different answer.
        self.external_panel.clear()
        self._refresh_second_opinion()

        if self._worker is not None:
            self._worker.cancel()

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
        self.loading_state.set_message(f"Searching for {query}…")

        worker = BinSearchWorker(self.context.lookup, query)
        worker.signals.result.connect(self._on_result)
        worker.signals.failed.connect(self._on_failed)
        self._worker = worker
        run_in_background(worker)

    def _on_result(self, result: BinLookupResult) -> None:
        self._worker = None
        if not result.found:
            self.empty_state.configure(
                "No record found",
                f"Bin-Tel has no record for {result.query}. It may not be allocated, "
                "or your database may predate its assignment — check for a database "
                "update from the Updates page.",
                icon_name="empty-box",
                action_text="Clear search",
            )
            self.stack.setCurrentWidget(self.empty_state)
            return

        record = result.best
        if record is None:  # pragma: no cover - guarded by result.found
            return
        # The card is handed the whole result, not just the record: how the
        # match was reached, how well it is evidenced and whether anything
        # disagrees are all part of the answer.
        self.result_card.show_lookup(result)

        # The card already states how the match was reached; this line says
        # what the *query* resolved to when that is not the value typed.
        note = ""
        if record.bin != result.query:
            if record.bin_range:
                note = (
                    f"{result.query} falls inside the allocated range "
                    f"{record.bin_range}."
                )
            else:
                note = (
                    f"No assignment recorded at {len(result.query)} digits. "
                    f"Answered from the {record.bin_length_label.replace(' digits', '-digit')} "
                    f"allocation {record.bin}."
                )
        if len(result.records) > 1:
            note = (
                f"{note} {len(result.records) - 1} broader allocation(s) also "
                "cover this value."
            ).strip()
        self.match_note.setText(note)
        self.match_note.setVisible(bool(note))

        self.stack.setCurrentWidget(self.result_holder)
        self.status_message.emit(
            f"Found {record.bin} in {result.elapsed_ms:.0f} ms"
        )

        settings = self.context.config.settings
        self.context.workspace.record_search(
            result.query,
            SearchKind.BIN,
            result_count=len(result.records),
            elapsed_ms=result.elapsed_ms,
            enabled=settings.privacy.remember_search_history,
            keep=settings.search.max_history,
        )
        self.search.set_history(
            self.context.workspace.recent_terms(SearchKind.BIN, settings.search.max_history)
        )
        self._update_record_actions(record)

    def _on_failed(self, exc: BaseException) -> None:
        self._worker = None
        if isinstance(exc, ValidationError):
            self.search.set_error(True)
            self.empty_state.configure(
                "That is not a BIN Bin-Tel can search",
                exc.message,
                icon_name="warning",
                action_text="Clear search",
            )
            self.stack.setCurrentWidget(self.empty_state)
            return
        from app.core.errors import friendly_message, friendly_title, is_retryable

        self.error_state.configure(
            friendly_title(exc), friendly_message(exc), retryable=is_retryable(exc)
        )
        self.stack.setCurrentWidget(self.error_state)

    # -- actions ----------------------------------------------------------
    def _export(self, record: BinRecord) -> None:
        chosen = ExportDialog.choose(
            self,
            f"bin-{record.bin}",
            title="Export BIN result",
            subtitle=f"Export the record for {record.bin}.",
        )
        if chosen is None:
            return
        path, fmt = chosen
        try:
            self.context.exports.export_record(record, path, fmt)
        except Exception as exc:
            self.show_error(exc)
            return
        self.toast(f"Exported to {path.name}")

    def _open_institution(self, institution_id: int) -> None:
        self.navigate(f"institutions:{institution_id}")

    # -- record actions ----------------------------------------------------
    def _update_record_actions(self, record: BinRecord) -> None:
        self.result_card.set_watch_state(
            self.context.watchlists.is_watched(WatchTargetType.BIN, record.bin)
        )
        self.result_card.set_favorite_state(
            self.context.workspace.is_favorite(FavoriteKind.BIN, record.bin)
        )

    def _watch_record(self, record: BinRecord) -> None:
        AddToWatchlistDialog.add(
            self,
            self.context,
            WatchTargetType.BIN,
            record.bin,
            f"BIN {record.bin} — {record.issuer_name}",
            on_added=lambda name: (
                self.toast(f"Added to “{name}”"),
                self._update_record_actions(record),
            ),
        )

    def _toggle_favorite(self, record: BinRecord) -> None:
        added = self.context.workspace.toggle_favorite(
            FavoriteKind.BIN, record.bin, f"BIN {record.bin}", record.issuer_name
        )
        self._update_record_actions(record)
        self.toast("Added to favourites" if added else "Removed from favourites")

    # -- advanced search ---------------------------------------------------
    def _run_advanced(self, query: AdvancedQuery, request: PageRequest) -> None:
        if not self.context.database.is_open:
            self.advanced_panel.show_page(
                self.advanced_panel.table.model.page, 0.0
            )
            self.toast("The database is not open yet")
            return
        settings = self.context.config.settings
        worker: Worker = Worker(
            self.context.search.search,
            query,
            request,
            history_enabled=settings.privacy.remember_search_history,
            history_size=settings.search.max_history,
        )
        worker.signals.result.connect(self._on_advanced_result)
        worker.signals.failed.connect(lambda exc: self.show_error(exc))
        run_in_background(worker)

    def _on_advanced_result(self, result: AdvancedSearchResult) -> None:
        self.advanced_panel.show_page(result.page, result.elapsed_ms)
        self.status_message.emit(
            f"{result.page.total:,} match(es) in {result.elapsed_ms:.0f} ms"
        )

    def _save_search(self, query: AdvancedQuery) -> None:
        dialog = CreateWatchlistDialog(self)
        dialog.setWindowTitle("Bin-Tel — Save search")
        dialog.name_field.setPlaceholderText("US credit BINs")
        from PyQt6.QtWidgets import QDialog

        if dialog.exec() != QDialog.DialogCode.Accepted or not dialog.watchlist_name:
            return
        try:
            self.context.workspace.save_search(
                dialog.watchlist_name,
                kind=SearchKind.ADVANCED,
                query=query.describe(),
                criteria=query,
            )
        except ValidationError as exc:
            self.toast(exc.message)
            return
        self.toast(f"Saved “{dialog.watchlist_name}”")

    def _export_advanced(self, query: AdvancedQuery) -> None:
        selected = self.advanced_panel.selected_rows()

        def write(rows: list[BinRow]) -> None:
            if not rows:
                self.toast("There is nothing to export")
                return
            chosen = ExportDialog.choose(
                self,
                "advanced-search",
                title="Export search results",
                subtitle=f"Export {len(rows):,} matching record(s).",
            )
            if chosen is None:
                return
            path, fmt = chosen
            try:
                self.context.exports.export_rows(rows, path, fmt, title="Search results")
            except Exception as exc:
                self.show_error(exc)
                return
            self.toast(f"Exported {len(rows):,} record(s)")

        if selected:
            write(selected)
            return
        worker: Worker = Worker(self.context.search.export_rows, query)
        worker.signals.result.connect(write)
        worker.signals.failed.connect(lambda exc: self.show_error(exc))
        run_in_background(worker)

    def on_theme_changed(self) -> None:
        self.search.refresh_icon()
        self.idle_state.refresh_icon()
        self.empty_state.refresh_icon()
        self.error_state.refresh_icon()
        record = self.result_card.record
        if record is not None:
            self.result_card.show_record(record)
            self._update_record_actions(record)
        self.advanced_panel.refresh_theme()

    def set_search_behavior(self, behavior: SearchBehavior, delay_ms: int) -> None:
        self.search.set_behavior(behavior, delay_ms)
