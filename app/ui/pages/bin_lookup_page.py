"""BIN / IIN lookup page."""

from __future__ import annotations

from PyQt6.QtWidgets import QLabel, QStackedWidget, QWidget

from app.core.config import SearchBehavior
from app.core.errors import ValidationError
from app.models.schemas import BinLookupResult, BinRecord
from app.services.export_service import ExportFormat
from app.ui.dialogs.export_dialog import ExportDialog
from app.ui.pages.base_page import BasePage
from app.ui.widgets.bin_result_card import BinResultCard
from app.ui.widgets.cards import Card
from app.ui.widgets.search_box import SearchBox
from app.ui.widgets.states import EmptyState, ErrorState, LoadingState
from app.utils.qt_helpers import shortcut, vbox
from app.workers.base import run_in_background
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

        search_card = Card(self.surface, padding=20, spacing=10)
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
        self.content.addWidget(search_card)

        # -- result surfaces ------------------------------------------------
        self.stack = QStackedWidget(self.surface)
        self.content.addWidget(self.stack, 1)

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

        shortcut(self, "Ctrl+K", self.focus_search)

    # -- lifecycle --------------------------------------------------------
    def on_shown(self) -> None:
        super().on_shown()
        settings = self.context.config.settings.search
        self.search.set_behavior(settings.behavior, settings.search_delay_ms)
        self.search.set_history(self.context.config.state.search_history)
        self.focus_search()

    def refresh(self) -> None:
        """No periodic work — results are only fetched on demand."""

    def focus_search(self) -> None:
        self.search.focus()

    def clear(self) -> None:
        self._last_query = ""
        self.search.set_error(False)
        self.result_card.clear()
        self.stack.setCurrentWidget(self.idle_state)

    # -- search -----------------------------------------------------------
    def perform_search(self, query: str) -> None:
        query = (query or "").strip()
        if not query:
            self.clear()
            return
        self._last_query = query
        self.search.set_text(query)

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
        self.result_card.show_record(record)

        notes = {
            "prefix": (
                f"No exact match for {result.query}. Showing the closest issuer prefix "
                f"on record ({record.bin})."
            ),
            "range": (
                f"{result.query} falls inside an allocated issuer range "
                f"({record.bin_range})."
            ),
        }
        note = notes.get(result.matched_by, "")
        if len(result.records) > 1:
            note = f"{note} {len(result.records)} candidate prefixes matched.".strip()
        self.match_note.setText(note)
        self.match_note.setVisible(bool(note))

        self.stack.setCurrentWidget(self.result_holder)
        self.status_message.emit(
            f"Found {record.bin} in {result.elapsed_ms:.0f} ms"
        )

        settings = self.context.config.settings.search
        self.context.config.state.record_search(result.query, settings.max_history)
        self.context.config.save_state()
        self.search.set_history(self.context.config.state.search_history)

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
        except Exception as exc:  # noqa: BLE001 - shown in a dialog
            self.show_error(exc)
            return
        self.toast(f"Exported to {path.name}")

    def _open_institution(self, institution_id: int) -> None:
        self.navigate(f"bank_lookup:{institution_id}")

    def on_theme_changed(self) -> None:
        self.search.refresh_icon()
        self.idle_state.refresh_icon()
        self.empty_state.refresh_icon()
        self.error_state.refresh_icon()
        record = self.result_card.record
        if record is not None:
            self.result_card.show_record(record)

    def set_search_behavior(self, behavior: SearchBehavior, delay_ms: int) -> None:
        self.search.set_behavior(behavior, delay_ms)
