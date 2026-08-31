"""The advanced search panel.

A criteria form above a paginated result table. Every field maps to one
attribute of :class:`~app.models.schemas.AdvancedQuery`, and the query is run
on a worker so a broad search never blocks the interface.
"""

from __future__ import annotations

from datetime import UTC, datetime

from PyQt6.QtCore import QDate, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDateEdit,
    QLabel,
    QLineEdit,
    QPushButton,
    QWidget,
)

from app.models.schemas import AdvancedQuery, BinRow, MatchMode, Page, PageRequest
from app.ui.widgets.cards import Card, SectionHeader
from app.ui.widgets.data_table import DataTable
from app.utils.qt_helpers import expanding_spacer, grid, hbox, vbox


class AdvancedSearchPanel(QWidget):
    """Criteria form plus results, emitting the query it wants run."""

    search_requested = pyqtSignal(object, object)  # AdvancedQuery, PageRequest
    save_requested = pyqtSignal(object)  # AdvancedQuery
    export_requested = pyqtSignal(object)  # AdvancedQuery
    copied = pyqtSignal(str)

    def __init__(self, parent: QWidget | None = None, *, page_size: int = 50) -> None:
        super().__init__(parent)
        layout = vbox(self, spacing=14)

        form_card = Card(self, padding=18, spacing=12)
        form_card.body.addWidget(
            SectionHeader(
                "Advanced search",
                "Combine any criteria. Leave a field empty to ignore it.",
                form_card,
            )
        )

        holder = QWidget(form_card)
        form = grid(holder, spacing=10)

        self.bin_prefix = self._line("BIN starts with", "4147")
        self.bin_from = self._line("BIN from", "400000")
        self.bin_to = self._line("BIN to", "499999")
        self.institution = self._line("Institution", "Any institution")

        self.match_mode = QComboBox(holder)
        self.match_mode.setAccessibleName("How the institution name is matched")
        for mode in MatchMode:
            self.match_mode.addItem(mode.label, mode.value)
        self.match_mode.setCurrentIndex(self.match_mode.findData(MatchMode.CONTAINS.value))

        self.country = self._combo("Country", "Any country")
        self.network = self._combo("Network", "Any network")
        self.card_type = self._combo("Card type", "Any card type")
        self.funding_type = self._combo("Funding type", "Any funding type")
        self.status = self._combo("Status", "Any status")
        self.currency = self._combo("Currency", "Any currency")

        self.region = self._line("State / province", "Any region")
        self.city = self._line("City", "Any city")
        self.postal_code = self._line("Postal / ZIP", "Any postal code")

        self.prepaid = QComboBox(holder)
        self.prepaid.setAccessibleName("Prepaid")
        for label, value in (("Any", None), ("Prepaid only", True), ("Exclude prepaid", False)):
            self.prepaid.addItem(label, value)

        self.commercial = QComboBox(holder)
        self.commercial.setAccessibleName("Commercial")
        for label, value in (
            ("Any", None),
            ("Commercial only", True),
            ("Consumer only", False),
        ):
            self.commercial.addItem(label, value)

        self.use_dates = QCheckBox("Limit to a date range", holder)
        self.use_dates.setAccessibleName("Limit results to a date range")
        self.updated_after = QDateEdit(holder)
        self.updated_after.setCalendarPopup(True)
        self.updated_after.setAccessibleName("Updated after")
        self.updated_after.setDate(QDate.currentDate().addYears(-1))
        self.updated_before = QDateEdit(holder)
        self.updated_before.setCalendarPopup(True)
        self.updated_before.setAccessibleName("Updated before")
        self.updated_before.setDate(QDate.currentDate())
        self.use_dates.toggled.connect(self.updated_after.setEnabled)
        self.use_dates.toggled.connect(self.updated_before.setEnabled)
        self.updated_after.setEnabled(False)
        self.updated_before.setEnabled(False)

        fields: list[tuple[str, QWidget]] = [
            ("BIN starts with", self.bin_prefix),
            ("BIN from", self.bin_from),
            ("BIN to", self.bin_to),
            ("Institution", self.institution),
            ("Name matching", self.match_mode),
            ("Country", self.country),
            ("Network", self.network),
            ("Card type", self.card_type),
            ("Funding type", self.funding_type),
            ("State / province", self.region),
            ("City", self.city),
            ("Postal / ZIP", self.postal_code),
            ("Currency", self.currency),
            ("Status", self.status),
            ("Prepaid", self.prepaid),
            ("Commercial", self.commercial),
            ("Date range", self.use_dates),
            ("Updated after", self.updated_after),
            ("Updated before", self.updated_before),
        ]
        columns = 4
        for index, (label_text, widget) in enumerate(fields):
            label = QLabel(label_text, holder)
            label.setProperty("role", "fieldLabel")
            row, column = divmod(index, columns)
            form.addWidget(label, row * 2, column)
            form.addWidget(widget, row * 2 + 1, column)
        for column in range(columns):
            form.setColumnStretch(column, 1)
        form_card.body.addWidget(holder)

        buttons = hbox(spacing=10)
        self.search_button = QPushButton("Search", form_card)
        self.search_button.setProperty("variant", "primary")
        self.search_button.setMinimumWidth(140)
        self.search_button.clicked.connect(self.run_search)
        buttons.addWidget(self.search_button)

        self.clear_button = QPushButton("Clear", form_card)
        self.clear_button.clicked.connect(self.clear)
        buttons.addWidget(self.clear_button)

        self.save_button = QPushButton("Save this search", form_card)
        self.save_button.setProperty("variant", "ghost")
        self.save_button.clicked.connect(lambda: self.save_requested.emit(self.query()))
        buttons.addWidget(self.save_button)

        self.export_button = QPushButton("Export results", form_card)
        self.export_button.setProperty("variant", "ghost")
        self.export_button.setEnabled(False)
        self.export_button.clicked.connect(lambda: self.export_requested.emit(self.query()))
        buttons.addWidget(self.export_button)

        buttons.addItem(expanding_spacer())
        self.summary_label = QLabel("", form_card)
        self.summary_label.setProperty("role", "muted")
        buttons.addWidget(self.summary_label)
        form_card.body.addLayout(buttons)
        layout.addWidget(form_card)

        results_card = Card(self, padding=16, spacing=10)
        results_card.body.addWidget(
            SectionHeader("Results", "Sort, filter and export what you find.", results_card)
        )
        self.table = DataTable(results_card, page_size=page_size)
        self.table.page_requested.connect(self._on_page_requested)
        self.table.filters_changed.connect(lambda _: self.run_search())
        self.table.copy_requested.connect(self.copied.emit)
        self.table.export_requested.connect(lambda _: self.export_requested.emit(self.query()))
        results_card.body.addWidget(self.table, 1)
        layout.addWidget(results_card, 1)

        for field in (self.bin_prefix, self.institution, self.city, self.postal_code):
            field.returnPressed.connect(self.run_search)

    def _line(self, name: str, placeholder: str) -> QLineEdit:
        field = QLineEdit(self)
        field.setPlaceholderText(placeholder)
        field.setAccessibleName(name)
        return field

    def _combo(self, name: str, placeholder: str) -> QComboBox:
        combo = QComboBox(self)
        combo.setAccessibleName(name)
        combo.addItem(placeholder, None)
        return combo

    # -- query -------------------------------------------------------------
    def query(self) -> AdvancedQuery:
        """Build the query the form currently describes."""
        filters = self.table.filter_bar.filters()
        after = before = None
        if self.use_dates.isChecked():
            after = datetime.combine(
                self.updated_after.date().toPyDate(), datetime.min.time(), tzinfo=UTC
            )
            before = datetime.combine(
                self.updated_before.date().toPyDate(), datetime.max.time(), tzinfo=UTC
            )
        return AdvancedQuery(
            bin_prefix=self.bin_prefix.text().strip() or None,
            bin_from=self.bin_from.text().strip() or None,
            bin_to=self.bin_to.text().strip() or None,
            institution=self.institution.text().strip() or None,
            institution_match=MatchMode(self.match_mode.currentData()),
            # A filter chosen in the table narrows the form's own criterion.
            country_code=filters.country_code or self.country.currentData(),
            network_code=filters.network_code or self.network.currentData(),
            card_type=filters.card_type or self.card_type.currentData(),
            funding_type=filters.funding_type or self.funding_type.currentData(),
            region=filters.region or self.region.text().strip() or None,
            city=self.city.text().strip() or None,
            postal_code=self.postal_code.text().strip() or None,
            currency=self.currency.currentData(),
            status=self.status.currentData(),
            prepaid=self.prepaid.currentData(),
            commercial=self.commercial.currentData(),
            updated_after=after,
            updated_before=before,
        )

    def set_query(self, query: AdvancedQuery) -> None:
        """Populate the form from a saved search."""
        self.bin_prefix.setText(query.bin_prefix or "")
        self.bin_from.setText(query.bin_from or "")
        self.bin_to.setText(query.bin_to or "")
        self.institution.setText(query.institution or "")
        index = self.match_mode.findData(query.institution_match.value)
        self.match_mode.setCurrentIndex(max(0, index))
        for combo, value in (
            (self.country, query.country_code),
            (self.network, query.network_code),
            (self.card_type, query.card_type),
            (self.funding_type, query.funding_type),
            (self.currency, query.currency),
            (self.status, query.status),
        ):
            position = combo.findData(value)
            combo.setCurrentIndex(position if position >= 0 else 0)
        self.region.setText(query.region or "")
        self.city.setText(query.city or "")
        self.postal_code.setText(query.postal_code or "")
        self.prepaid.setCurrentIndex(max(0, self.prepaid.findData(query.prepaid)))
        self.commercial.setCurrentIndex(max(0, self.commercial.findData(query.commercial)))
        self.use_dates.setChecked(query.updated_after is not None)

    def clear(self) -> None:
        for field in (
            self.bin_prefix,
            self.bin_from,
            self.bin_to,
            self.institution,
            self.region,
            self.city,
            self.postal_code,
        ):
            field.clear()
        for combo in (
            self.country,
            self.network,
            self.card_type,
            self.funding_type,
            self.currency,
            self.status,
            self.prepaid,
            self.commercial,
        ):
            combo.setCurrentIndex(0)
        self.use_dates.setChecked(False)
        self.table.filter_bar.clear()
        self.table.show_empty(
            "Ready when you are",
            "Set any combination of criteria and press Search.",
        )
        self.summary_label.setText("")
        self.export_button.setEnabled(False)

    # -- running -----------------------------------------------------------
    def run_search(self) -> None:
        self.search_requested.emit(self.query(), self.table.current_request(1))

    def _on_page_requested(self, request: PageRequest) -> None:
        self.search_requested.emit(self.query(), request)

    def show_page(self, page: Page[BinRow], elapsed_ms: float = 0.0) -> None:
        self.table.set_page(page)
        self.summary_label.setText(
            f"{page.total:,} match(es) in {elapsed_ms:.0f} ms"
            if page.total
            else "No records matched those criteria."
        )
        self.export_button.setEnabled(page.total > 0)

    def set_filter_options(self, values: dict[str, list[tuple[str, str]]]) -> None:
        """Populate both the form's selectors and the table's filter bar."""
        self.table.set_filter_options(values)
        for combo, key, placeholder in (
            (self.country, "country", "Any country"),
            (self.network, "network", "Any network"),
            (self.card_type, "card_type", "Any card type"),
            (self.funding_type, "funding_type", "Any funding type"),
            (self.currency, "currency", "Any currency"),
            (self.status, "status", "Any status"),
        ):
            combo.blockSignals(True)
            current = combo.currentData()
            combo.clear()
            combo.addItem(placeholder, None)
            for code, label in values.get(key, []):
                combo.addItem(label, code)
            position = combo.findData(current)
            combo.setCurrentIndex(max(0, position))
            combo.blockSignals(False)

    def set_page_size(self, size: int) -> None:
        self.table.set_page_size(size)

    def selected_rows(self) -> list[BinRow]:
        return self.table.selected_rows()

    def refresh_theme(self) -> None:
        self.table.refresh_theme()
