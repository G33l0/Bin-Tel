"""The bank lookup result surface.

An institution header, summary statistics, and the paginated table of every
BIN associated with that institution. Like the BIN result, it shows no data
sources, provenance or internal notes.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QComboBox, QLabel, QPushButton, QWidget

from app.core.constants import UNKNOWN_DISPLAY
from app.models.schemas import (
    BinFilters,
    BinRow,
    InstitutionDetail,
    InstitutionStats,
    Page,
    PageRequest,
)
from app.ui.widgets.cards import Card, Chip, FieldRow, SectionHeader
from app.ui.widgets.data_table import DataTable
from app.utils.formatting import format_number, pluralise
from app.utils.qt_helpers import copy_to_clipboard, expanding_spacer, grid, hbox, open_url, vbox

#: Statistic tiles shown above the table, in display order.
STAT_TILES: tuple[tuple[str, str], ...] = (
    ("total", "Total BINs"),
    ("visa", "Visa"),
    ("mastercard", "Mastercard"),
    ("credit", "Credit"),
    ("debit", "Debit"),
    ("prepaid", "Prepaid"),
    ("commercial", "Commercial"),
)


class InstitutionHeader(Card):
    """Name, legal name, country, website and BIN count."""

    website_clicked = pyqtSignal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent, padding=20, spacing=14)

        top = hbox(spacing=14)
        column = vbox(spacing=6)

        self.name_label = QLabel(UNKNOWN_DISPLAY, self)
        self.name_label.setProperty("role", "pageTitle")
        self.name_label.setWordWrap(True)
        self.name_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        column.addWidget(self.name_label)

        self.legal_label = QLabel("", self)
        self.legal_label.setProperty("role", "pageSubtitle")
        self.legal_label.setWordWrap(True)
        column.addWidget(self.legal_label)

        self.chip_row = hbox(spacing=6)
        column.addLayout(self.chip_row)
        top.addLayout(column, 1)

        actions = vbox(spacing=6)
        actions.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.website_button = QPushButton("Open website", self)
        self.website_button.setProperty("variant", "")
        self.website_button.setVisible(False)
        self.website_button.setMinimumWidth(140)
        self.website_button.clicked.connect(self._open_website)
        actions.addWidget(self.website_button)
        top.addLayout(actions)
        self.body.addLayout(top)

        self._grid_container = QWidget(self)
        self._grid = grid(self._grid_container, spacing=14)
        self.body.addWidget(self._grid_container)

        self._website = ""

    def show_institution(self, institution: InstitutionDetail) -> None:
        self.name_label.setText(institution.display_name)
        legal = institution.legal_name or ""
        self.legal_label.setText(legal if legal and legal != institution.display_name else "")
        self.legal_label.setVisible(bool(self.legal_label.text()))

        while self.chip_row.count():
            item = self.chip_row.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        chips = [pluralise(institution.bin_count, "BIN")]
        if institution.country and institution.country.name:
            chips.append(institution.country.label)
        if institution.institution_type:
            chips.append(institution.institution_type)
        if institution.status and institution.status.lower() != "active":
            chips.append(institution.status)
        for text in chips:
            self.chip_row.addWidget(Chip(text, self))
        self.chip_row.addItem(expanding_spacer())

        self._website = institution.website or ""
        self.website_button.setVisible(bool(self._website))
        self.website_button.setAccessibleName(f"Open the website for {institution.display_name}")

        while self._grid.count():
            item = self._grid.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        fields: list[tuple[str, str]] = []
        if institution.country:
            fields.append(("Country", institution.country.display_name))
        if institution.parent_name:
            fields.append(("Parent institution", institution.parent_name))
        if institution.has_address and institution.address is not None:
            fields.append(("Address", institution.address.one_line))
        if institution.website:
            fields.append(("Website", institution.website))
        if institution.aliases:
            fields.append(("Also known as", ", ".join(institution.aliases[:6])))
        fields.append(("Associated BINs", format_number(institution.bin_count)))

        for index, (label, value) in enumerate(fields):
            self._grid.addWidget(FieldRow(label, value, self._grid_container), index // 3, index % 3)
        for column in range(3):
            self._grid.setColumnStretch(column, 1)

    def _open_website(self) -> None:
        if self._website:
            url = self._website if "//" in self._website else f"https://{self._website}"
            open_url(url)
            self.website_clicked.emit(url)


class StatsStrip(Card):
    """Compact summary counters above the BIN table."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent, padding=16, spacing=10)
        self.body.addWidget(SectionHeader("Portfolio summary", parent=self))

        container = QWidget(self)
        self._grid = grid(container, spacing=12)
        self.body.addWidget(container)
        self._values: dict[str, QLabel] = {}

        for index, (key, label) in enumerate(STAT_TILES):
            cell = vbox(spacing=2)
            value_label = QLabel("0", container)
            value_label.setProperty("role", "metricValue")
            value_label.setStyleSheet("font-size: 16pt;")
            caption = QLabel(label, container)
            caption.setProperty("role", "metricLabel")
            cell.addWidget(value_label)
            cell.addWidget(caption)
            holder = QWidget(container)
            holder.setLayout(cell)
            holder.setAccessibleName(f"{label}: 0")
            self._grid.addWidget(holder, 0, index)
            self._grid.setColumnStretch(index, 1)
            self._values[key] = value_label

    def show_stats(self, stats: InstitutionStats) -> None:
        values = {
            "total": stats.total_bins,
            "visa": stats.network_count("visa"),
            "mastercard": stats.network_count("mastercard"),
            "credit": stats.card_type_count("credit"),
            "debit": stats.card_type_count("debit"),
            "prepaid": stats.prepaid or stats.card_type_count("prepaid"),
            "commercial": stats.commercial,
        }
        for key, label in self._values.items():
            label.setText(format_number(values.get(key, 0)))
            parent = label.parentWidget()
            if parent is not None:
                title = dict(STAT_TILES).get(key, key)
                parent.setAccessibleName(f"{title}: {label.text()}")


class BankResultView(QWidget):
    """Institution header + statistics + the institution's BIN table."""

    page_requested = pyqtSignal(int, object)  # institution_id, PageRequest
    filters_changed = pyqtSignal(int, object)  # institution_id, BinFilters
    export_requested = pyqtSignal(int, bool)  # institution_id, selection_only
    copied = pyqtSignal(str)
    match_selected = pyqtSignal(int)

    def __init__(self, parent: QWidget | None = None, *, page_size: int = 50) -> None:
        super().__init__(parent)
        self._institution: InstitutionDetail | None = None
        self._filters = BinFilters()

        layout = vbox(self, spacing=14)

        self.match_row = QWidget(self)
        match_layout = hbox(self.match_row, spacing=8)
        match_label = QLabel("Other matches", self.match_row)
        match_label.setProperty("role", "fieldLabel")
        match_layout.addWidget(match_label)
        self.match_combo = QComboBox(self.match_row)
        self.match_combo.setAccessibleName("Other matching institutions")
        self.match_combo.setMinimumWidth(280)
        self.match_combo.activated.connect(self._on_match_selected)
        match_layout.addWidget(self.match_combo)
        match_layout.addItem(expanding_spacer())
        self.match_row.hide()
        layout.addWidget(self.match_row)

        self.header = InstitutionHeader(self)
        layout.addWidget(self.header)

        self.stats = StatsStrip(self)
        layout.addWidget(self.stats)

        table_card = Card(self, padding=16, spacing=12)
        actions = QWidget(table_card)
        action_layout = hbox(actions, spacing=8)
        self.copy_bins_button = QPushButton("Copy selected BINs", actions)
        self.copy_bins_button.setProperty("variant", "ghost")
        self.copy_bins_button.setEnabled(False)
        action_layout.addWidget(self.copy_bins_button)
        self.export_csv_button = QPushButton("Export CSV", actions)
        self.export_csv_button.setProperty("variant", "ghost")
        action_layout.addWidget(self.export_csv_button)
        self.export_json_button = QPushButton("Export JSON", actions)
        self.export_json_button.setProperty("variant", "ghost")
        action_layout.addWidget(self.export_json_button)

        table_card.body.addWidget(
            SectionHeader("Associated BIN records", "Sort, filter and export the full list.", table_card, action=actions)
        )

        self.table = DataTable(table_card, page_size=page_size)
        self.table.page_requested.connect(self._on_page_requested)
        self.table.filters_changed.connect(self._on_filters_changed)
        self.table.export_requested.connect(self._on_export_requested)
        self.table.copy_requested.connect(self.copied.emit)
        self.table.selection_changed.connect(self._on_selection_changed)
        self.copy_bins_button.clicked.connect(self.table.copy_selected_bins)
        table_card.body.addWidget(self.table, 1)
        layout.addWidget(table_card, 1)

    # -- content ----------------------------------------------------------
    @property
    def institution(self) -> InstitutionDetail | None:
        return self._institution

    @property
    def filters(self) -> BinFilters:
        return self._filters

    def show_institution(self, institution: InstitutionDetail, others: list[InstitutionDetail]) -> None:
        self._institution = institution
        self.header.show_institution(institution)
        self.match_combo.blockSignals(True)
        self.match_combo.clear()
        for candidate in others:
            country = candidate.country.iso2 if candidate.country else "—"
            self.match_combo.addItem(
                f"{candidate.display_name} · {country} · {candidate.bin_count} BINs", candidate.id
            )
        index = self.match_combo.findData(institution.id)
        if index >= 0:
            self.match_combo.setCurrentIndex(index)
        self.match_combo.blockSignals(False)
        self.match_row.setVisible(len(others) > 1)

    def show_stats(self, stats: InstitutionStats) -> None:
        self.stats.show_stats(stats)

    def show_page(self, page: Page[BinRow]) -> None:
        self.table.set_page(page)

    def set_filter_options(self, options: dict[str, list[tuple[str, str]]]) -> None:
        self.table.set_filter_options(options)

    def current_request(self, page: int = 1) -> PageRequest:
        return self.table.current_request(page)

    def reload(self) -> None:
        if self._institution is not None:
            self.page_requested.emit(self._institution.id, self.table.current_request(1))

    def selected_bins(self) -> list[str]:
        return self.table.selected_bins()

    def selected_rows(self) -> list[BinRow]:
        return self.table.selected_rows()

    def copy_all_bins(self, rows: list[BinRow]) -> None:
        if rows and copy_to_clipboard("\n".join(row.bin for row in rows)):
            self.copied.emit(f"{len(rows)} BIN(s) copied")

    # -- signals ----------------------------------------------------------
    def _on_match_selected(self, index: int) -> None:
        identifier = self.match_combo.itemData(index)
        if identifier is not None:
            self.match_selected.emit(int(identifier))

    def _on_page_requested(self, request: PageRequest) -> None:
        if self._institution is not None:
            self.page_requested.emit(self._institution.id, request)

    def _on_filters_changed(self, filters: BinFilters) -> None:
        self._filters = filters
        if self._institution is not None:
            self.filters_changed.emit(self._institution.id, filters)

    def _on_export_requested(self, selection_only: bool) -> None:
        if self._institution is not None:
            self.export_requested.emit(self._institution.id, selection_only)

    def _on_selection_changed(self, count: int) -> None:
        self.copy_bins_button.setEnabled(count > 0)
        self.copy_bins_button.setText(
            f"Copy {count} selected BIN(s)" if count else "Copy selected BINs"
        )

    def refresh_theme(self) -> None:
        self.table.refresh_theme()
