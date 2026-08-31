"""The Bin-Tel table: sorting, filtering, column control, copy, export, paging.

The view is deliberately *page-bound*. It asks its owner for one page at a
time via :attr:`DataTable.page_requested`; it never holds a whole result set,
which is what keeps a multi-million-row database usable.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QAction, QKeySequence
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QHeaderView,
    QLabel,
    QMenu,
    QPushButton,
    QSizePolicy,
    QTableView,
    QWidget,
)

from app.models.schemas import BinFilters, BinRow, Page, PageRequest
from app.ui.models.bin_table_model import DEFAULT_HIDDEN, BinTableModel
from app.ui.themes.icons import IconProvider
from app.ui.widgets.cards import IconButton
from app.ui.widgets.states import EmptyState
from app.utils.qt_helpers import copy_to_clipboard, expanding_spacer, grid, hbox, vbox

PAGE_SIZE_CHOICES = (25, 50, 100, 250, 500)


class FilterBar(QWidget):
    """Country / network / card type / funding / region filters."""

    filters_changed = pyqtSignal(object)  # BinFilters

    #: (filter key, placeholder shown when nothing is selected)
    _FIELDS = (
        ("country", "All countries"),
        ("network", "All networks"),
        ("card_type", "All card types"),
        ("funding_type", "All funding"),
        ("region", "All regions"),
    )

    #: Filters whose options are fixed rather than read from the database:
    #: ``(key, placeholder, [(label, value), ...])``.
    _FIXED_FIELDS = (
        (
            "standing",
            "All records",
            (("Current only", True), ("Historical only", False)),
        ),
        (
            "length",
            "Any length",
            (("6-digit", 6), ("8-digit", 8)),
        ),
    )

    #: Narrowest a filter combo is allowed to get before the bar reflows.
    _COMBO_WIDTH = 148
    _SPACING = 8

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        # A grid rather than a row: filters have grown from five to seven and
        # will grow again, and a fixed row would eventually push the whole page
        # sideways. This reflows into as many columns as actually fit.
        self._grid = grid(self, spacing=self._SPACING)
        self._combos: dict[str, QComboBox] = {}
        self._ordered: list[QWidget] = []
        self._columns = 0
        self._suspend = False

        for key, placeholder in self._FIELDS:
            combo = QComboBox(self)
            combo.setAccessibleName(placeholder)
            combo.setToolTip(f"Filter by {placeholder.removeprefix('All ')}")
            # Sized from a fixed character budget rather than the longest
            # option: a country list would otherwise widen the bar far past
            # the page and force the whole view to scroll sideways.
            combo.setSizeAdjustPolicy(
                QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
            )
            combo.setMinimumContentsLength(13)
            combo.addItem(placeholder, None)
            combo.setMinimumWidth(148)
            combo.setMaximumWidth(228)
            combo.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
            combo.currentIndexChanged.connect(self._emit)
            self._combos[key] = combo
            self._ordered.append(combo)

        # Standing and length are not data-derived, so their options are
        # known up front. Standing defaults to "both": a BIN an issuer used to
        # hold is part of its record, and hiding it by default would quietly
        # under-report the portfolio.
        for key, placeholder, options in self._FIXED_FIELDS:
            combo = QComboBox(self)
            combo.setAccessibleName(placeholder)
            combo.setToolTip(f"Filter by {placeholder.lower()}")
            combo.setSizeAdjustPolicy(
                QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
            )
            combo.setMinimumContentsLength(13)
            combo.addItem(placeholder, None)
            for label, value in options:
                combo.addItem(label, value)
            combo.setMinimumWidth(148)
            combo.setMaximumWidth(228)
            combo.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
            combo.currentIndexChanged.connect(self._emit)
            self._combos[key] = combo
            self._ordered.append(combo)

        self.clear_button = QPushButton("Clear filters", self)
        self.clear_button.setProperty("variant", "ghost")
        self.clear_button.setEnabled(False)
        self.clear_button.clicked.connect(self.clear)
        self._ordered.append(self.clear_button)
        self._relayout(columns=len(self._ordered))

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._relayout()

    def sizeHint(self):  # noqa: N802
        hint = super().sizeHint()
        # The natural width is one full row, but the bar is happy to be
        # narrower, so it must not report the row width as a *minimum*.
        return hint

    def minimumSizeHint(self):  # noqa: N802
        hint = super().minimumSizeHint()
        hint.setWidth(self._COMBO_WIDTH * 2 + self._SPACING)
        return hint

    def _relayout(self, *, columns: int | None = None) -> None:
        """Lay the controls out in as many columns as the width allows."""
        if columns is None:
            available = max(self.width(), self._COMBO_WIDTH)
            columns = max(
                2, (available + self._SPACING) // (self._COMBO_WIDTH + self._SPACING)
            )
            columns = min(columns, len(self._ordered))
        if columns == self._columns:
            return
        self._columns = columns

        while self._grid.count():
            self._grid.takeAt(0)
        for index, widget in enumerate(self._ordered):
            row, column = divmod(index, columns)
            self._grid.addWidget(widget, row, column)
        for column in range(columns):
            self._grid.setColumnStretch(column, 1)

    def set_options(self, options: dict[str, list[tuple[str, str]]]) -> None:
        self._suspend = True
        for key, placeholder in self._FIELDS:
            combo = self._combos[key]
            current = combo.currentData()
            combo.clear()
            combo.addItem(placeholder, None)
            for code, label in options.get(key, []):
                combo.addItem(label, code)
            if current is not None:
                index = combo.findData(current)
                combo.setCurrentIndex(max(0, index))
        self._suspend = False

    def filters(self) -> BinFilters:
        return BinFilters(
            country_code=self._combos["country"].currentData(),
            network_code=self._combos["network"].currentData(),
            card_type=self._combos["card_type"].currentData(),
            funding_type=self._combos["funding_type"].currentData(),
            region=self._combos["region"].currentData(),
            is_current=self._combos["standing"].currentData(),
            prefix_length=self._combos["length"].currentData(),
        )

    def clear(self) -> None:
        self._suspend = True
        for combo in self._combos.values():
            combo.setCurrentIndex(0)
        self._suspend = False
        self._emit()

    def _emit(self) -> None:
        if self._suspend:
            return
        filters = self.filters()
        self.clear_button.setEnabled(filters.is_active)
        self.filters_changed.emit(filters)


class Pager(QWidget):
    """Page controls plus the ``51–100 of 137`` summary."""

    page_requested = pyqtSignal(int)
    page_size_changed = pyqtSignal(int)

    def __init__(self, page_size: int = 50, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = hbox(self, spacing=8)

        self.summary_label = QLabel("No results", self)
        self.summary_label.setProperty("role", "muted")
        layout.addWidget(self.summary_label)
        layout.addItem(expanding_spacer())

        self.size_combo = QComboBox(self)
        self.size_combo.setAccessibleName("Results per page")
        self.size_combo.setToolTip("Results per page")
        for choice in PAGE_SIZE_CHOICES:
            self.size_combo.addItem(f"{choice} per page", choice)
        index = self.size_combo.findData(page_size)
        self.size_combo.setCurrentIndex(index if index >= 0 else 1)
        self.size_combo.currentIndexChanged.connect(
            lambda: self.page_size_changed.emit(int(self.size_combo.currentData()))
        )
        layout.addWidget(self.size_combo)

        self.first_button = self._nav("chevron-left", "First page", lambda: self.page_requested.emit(1))
        self.prev_button = self._nav("chevron-left", "Previous page", self._previous)
        self.page_label = QLabel("1 / 1", self)
        self.page_label.setProperty("role", "muted")
        self.page_label.setMinimumWidth(64)
        self.page_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.next_button = self._nav("chevron-right", "Next page", self._next)
        self.last_button = self._nav("chevron-right", "Last page", self._last)

        for widget in (
            self.first_button,
            self.prev_button,
            self.page_label,
            self.next_button,
            self.last_button,
        ):
            layout.addWidget(widget)

        self._page: Page[BinRow] = Page.empty()
        self.update_page(self._page)

    def _nav(self, icon_name: str, tooltip: str, handler: object) -> IconButton:
        return IconButton(icon_name, tooltip, self, size=15, on_click=handler)  # type: ignore[arg-type]

    def _previous(self) -> None:
        if self._page.has_previous:
            self.page_requested.emit(self._page.page - 1)

    def _next(self) -> None:
        if self._page.has_next:
            self.page_requested.emit(self._page.page + 1)

    def _last(self) -> None:
        self.page_requested.emit(self._page.page_count)

    def update_page(self, page: Page[BinRow]) -> None:
        self._page = page
        self.summary_label.setText(page.summary)
        self.page_label.setText(f"{page.page} / {page.page_count}")
        self.first_button.setEnabled(page.has_previous)
        self.prev_button.setEnabled(page.has_previous)
        self.next_button.setEnabled(page.has_next)
        self.last_button.setEnabled(page.has_next)

    def refresh_icons(self) -> None:
        for button in (self.first_button, self.prev_button, self.next_button, self.last_button):
            button.refresh_icon()


class DataTable(QWidget):
    """Filter bar + table + pager, wired to a :class:`BinTableModel`."""

    page_requested = pyqtSignal(object)  # PageRequest
    filters_changed = pyqtSignal(object)  # BinFilters
    export_requested = pyqtSignal(bool)  # selection_only
    copy_requested = pyqtSignal(str)
    selection_changed = pyqtSignal(int)

    def __init__(self, parent: QWidget | None = None, *, page_size: int = 50) -> None:
        super().__init__(parent)
        self._page_size = page_size
        self._sort_by = "bin"
        self._ascending = True

        layout = vbox(self, spacing=10)

        # -- toolbar ------------------------------------------------------
        toolbar = QWidget(self)
        toolbar_layout = hbox(toolbar, spacing=8)
        self.filter_bar = FilterBar(toolbar)
        self.filter_bar.filters_changed.connect(self._on_filters_changed)
        toolbar_layout.addWidget(self.filter_bar, 1)

        self.columns_button = IconButton(
            "columns", "Choose visible columns", toolbar, on_click=self._show_column_menu
        )
        toolbar_layout.addWidget(self.columns_button)

        self.export_button = IconButton(
            "export", "Export these results", toolbar, on_click=lambda: self.export_requested.emit(False)
        )
        toolbar_layout.addWidget(self.export_button)
        layout.addWidget(toolbar)

        # -- table --------------------------------------------------------
        self.model = BinTableModel(self)
        self.view = QTableView(self)
        self.view.setModel(self.model)
        self.view.setSortingEnabled(False)  # sorting is server-side
        self.view.setAlternatingRowColors(True)
        self.view.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.view.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.view.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.view.setWordWrap(False)
        self.view.setShowGrid(False)
        self.view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.view.customContextMenuRequested.connect(self._show_context_menu)
        self.view.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.view.setAccessibleName("BIN records")
        # Enough room for roughly a dozen rows before the page scrolls, so the
        # table is never reduced to a single visible record.
        self.view.setMinimumHeight(340)

        header = self.view.horizontalHeader()
        header.setSectionsClickable(True)
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setStretchLastSection(True)
        header.setHighlightSections(False)
        header.sectionClicked.connect(self._on_header_clicked)
        vertical = self.view.verticalHeader()
        vertical.setDefaultSectionSize(30)
        vertical.setVisible(False)

        selection = self.view.selectionModel()
        if selection is not None:
            selection.selectionChanged.connect(self._on_selection_changed)

        layout.addWidget(self.view, 1)

        self.empty_state = EmptyState(
            "No matching records",
            "Adjust or clear the filters to see more results.",
            self,
        )
        self.empty_state.hide()
        layout.addWidget(self.empty_state)

        # -- pager --------------------------------------------------------
        self.pager = Pager(page_size, self)
        self.pager.page_requested.connect(self._request_page)
        self.pager.page_size_changed.connect(self._on_page_size_changed)
        layout.addWidget(self.pager)

        self._apply_default_columns()
        self._install_shortcuts()

    # -- columns ----------------------------------------------------------
    def _apply_default_columns(self) -> None:
        for index, column in enumerate(self.model.columns):
            self.view.setColumnWidth(index, column.width)
            self.view.setColumnHidden(index, column.key in DEFAULT_HIDDEN)

    def _show_column_menu(self) -> None:
        menu = QMenu(self)
        menu.setTitle("Columns")
        for index, column in enumerate(self.model.columns):
            action = QAction(column.title, menu)
            action.setCheckable(True)
            action.setChecked(not self.view.isColumnHidden(index))
            action.setEnabled(column.key != "bin")  # the BIN column always stays
            action.toggled.connect(
                lambda checked, position=index: self.view.setColumnHidden(position, not checked)
            )
            menu.addAction(action)
        menu.exec(self.columns_button.mapToGlobal(self.columns_button.rect().bottomLeft()))

    # -- data -------------------------------------------------------------
    def set_page(self, page: Page[BinRow]) -> None:
        self.model.set_page(page)
        self.pager.update_page(page)
        has_rows = page.total > 0
        self.view.setVisible(has_rows)
        self.empty_state.setVisible(not has_rows)
        if has_rows:
            self.view.scrollToTop()

    def set_filter_options(self, options: dict[str, list[tuple[str, str]]]) -> None:
        self.filter_bar.set_options(options)

    def set_page_size(self, size: int) -> None:
        self._page_size = size

    def current_request(self, page: int = 1) -> PageRequest:
        return BinTableModel.request_for(page, self._page_size, self._sort_by, self._ascending)

    def reload(self) -> None:
        self._request_page(1)

    def clear(self) -> None:
        self.model.clear()
        self.pager.update_page(self.model.page)
        self.view.hide()
        self.empty_state.show()

    def show_empty(self, title: str, message: str = "") -> None:
        self.clear()
        self.empty_state.configure(title, message)

    # -- selection --------------------------------------------------------
    def selected_rows(self) -> list[BinRow]:
        selection = self.view.selectionModel()
        if selection is None:
            return []
        rows = sorted({index.row() for index in selection.selectedRows()})
        return [row for row in (self.model.row_at(index) for index in rows) if row is not None]

    def selected_bins(self) -> list[str]:
        return [row.bin for row in self.selected_rows()]

    def _on_selection_changed(self) -> None:
        self.selection_changed.emit(len(self.selected_rows()))

    # -- interaction ------------------------------------------------------
    def _on_header_clicked(self, section: int) -> None:
        key = self.model.sort_key_for(section)
        if key is None:
            return
        if key == self._sort_by:
            self._ascending = not self._ascending
        else:
            self._sort_by = key
            self._ascending = True
        header = self.view.horizontalHeader()
        header.setSortIndicatorShown(True)
        header.setSortIndicator(
            section,
            Qt.SortOrder.AscendingOrder if self._ascending else Qt.SortOrder.DescendingOrder,
        )
        self._request_page(1)

    def _on_page_size_changed(self, size: int) -> None:
        self._page_size = size
        self._request_page(1)

    def _on_filters_changed(self, filters: BinFilters) -> None:
        self.filters_changed.emit(filters)

    def _request_page(self, page: int) -> None:
        self.page_requested.emit(self.current_request(page))

    # -- copy / context menu ----------------------------------------------
    def _install_shortcuts(self) -> None:
        copy_action = QAction("Copy", self.view)
        copy_action.setShortcut(QKeySequence.StandardKey.Copy)
        copy_action.setShortcutContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        copy_action.triggered.connect(self.copy_selected_rows)
        self.view.addAction(copy_action)

    def copy_cell(self) -> None:
        index = self.view.currentIndex()
        if not index.isValid():
            return
        text = self.model.cell_text(index.row(), index.column())
        if copy_to_clipboard(text):
            self.copy_requested.emit("Cell copied")

    def copy_selected_rows(self) -> None:
        selection = self.view.selectionModel()
        if selection is None:
            return
        rows = sorted({index.row() for index in selection.selectedRows()})
        if not rows:
            self.copy_cell()
            return
        lines = [self.model.header_text()] + [self.model.row_text(row) for row in rows]
        if copy_to_clipboard("\n".join(lines)):
            self.copy_requested.emit(f"{len(rows)} row(s) copied")

    def copy_selected_bins(self) -> None:
        bins = self.selected_bins()
        if not bins:
            return
        if copy_to_clipboard("\n".join(bins)):
            self.copy_requested.emit(f"{len(bins)} BIN(s) copied")

    def _show_context_menu(self, position: object) -> None:
        menu = QMenu(self)
        menu.addAction("Copy cell", self.copy_cell)
        menu.addAction("Copy row(s)", self.copy_selected_rows)
        menu.addAction("Copy selected BINs", self.copy_selected_bins)
        menu.addSeparator()
        menu.addAction("Export selected rows…", lambda: self.export_requested.emit(True))
        menu.addAction("Export all results…", lambda: self.export_requested.emit(False))
        viewport = self.view.viewport()
        if viewport is not None:
            menu.exec(viewport.mapToGlobal(position))  # type: ignore[arg-type]

    def refresh_theme(self) -> None:
        for button in (self.columns_button, self.export_button):
            button.refresh_icon()
        self.pager.refresh_icons()
        self.empty_state.refresh_icon()
