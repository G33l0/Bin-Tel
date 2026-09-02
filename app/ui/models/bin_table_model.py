"""Table model over a page of :class:`~app.models.schemas.BinRow` records.

The model only ever holds *one page*. Sorting and filtering are pushed down to
SQLite through :class:`~app.models.schemas.PageRequest`, so the application
stays responsive with millions of records — nothing is loaded speculatively.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from PyQt6.QtCore import QAbstractTableModel, QModelIndex, Qt, pyqtSignal

from app.core.constants import UNKNOWN_DISPLAY
from app.models.schemas import BinRow, Page, PageRequest, SortDirection


@dataclass(frozen=True, slots=True)
class Column:
    key: str
    title: str
    width: int = 120
    sortable: bool = True
    monospace: bool = False
    optional: bool = False


COLUMNS: tuple[Column, ...] = (
    Column("bin", "BIN", 108, monospace=True),
    # Length and standing are shown by default, because "which BIN is this and
    # does it still apply" is the reader's first question about a portfolio row.
    Column("length", "Length", 88, sortable=False),
    Column("standing", "Standing", 100, sortable=False),
    Column("network", "Network", 122),
    Column("brand", "Card Brand", 150, sortable=False, optional=True),
    Column("card_type", "Card Type", 108),
    Column("funding_type", "Funding", 100),
    Column("institution", "Issuer", 200, sortable=False, optional=True),
    Column("country", "Country", 140),
    Column("region", "State / Province", 140, sortable=False, optional=True),
    Column("city", "City", 130, sortable=False, optional=True),
    Column("postal_code", "ZIP / Postal", 110, sortable=False, optional=True),
    Column("status", "Status", 96),
)

#: Columns hidden by default to keep the table readable at typical widths.
DEFAULT_HIDDEN = ("brand", "postal_code")


class BinTableModel(QAbstractTableModel):
    """Read-only model over one page of BIN rows."""

    page_changed = pyqtSignal(object)  # Page[BinRow]

    def __init__(self, parent: object | None = None) -> None:
        super().__init__(parent)
        self._rows: list[BinRow] = []
        self._page: Page[BinRow] = Page.empty()
        self._columns: tuple[Column, ...] = COLUMNS

    # -- Qt model interface ----------------------------------------------
    def rowCount(self, parent: QModelIndex | None = None) -> int:
        if parent is not None and parent.isValid():
            return 0
        return len(self._rows)

    def columnCount(self, parent: QModelIndex | None = None) -> int:
        if parent is not None and parent.isValid():
            return 0
        return len(self._columns)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid() or not (0 <= index.row() < len(self._rows)):
            return None
        row = self._rows[index.row()]
        column = self._columns[index.column()]

        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.ToolTipRole):
            return row.cell(column.key)
        if role == Qt.ItemDataRole.TextAlignmentRole:
            if column.key in ("bin", "status", "card_type", "funding_type"):
                return int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            return int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        if role == Qt.ItemDataRole.AccessibleTextRole:
            return f"{column.title}: {row.cell(column.key)}"
        if role == Qt.ItemDataRole.UserRole:
            return row
        return None

    def headerData(
        self, section: int, orientation: Qt.Orientation, role: int = Qt.ItemDataRole.DisplayRole
    ) -> Any:
        if orientation is Qt.Orientation.Horizontal and 0 <= section < len(self._columns):
            if role == Qt.ItemDataRole.DisplayRole:
                return self._columns[section].title
            if role == Qt.ItemDataRole.ToolTipRole:
                return self._columns[section].title
        if orientation is Qt.Orientation.Vertical and role == Qt.ItemDataRole.DisplayRole:
            # Continue the numbering across pages so row 51 reads as 51.
            return str(self._page.first_index + section)
        return None

    def flags(self, index: QModelIndex) -> Qt.ItemFlag:
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags
        return Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable

    # -- content ----------------------------------------------------------
    def set_page(self, page: Page[BinRow]) -> None:
        self.beginResetModel()
        self._page = page
        self._rows = list(page.items)
        self.endResetModel()
        self.page_changed.emit(page)

    def clear(self) -> None:
        self.set_page(Page.empty())

    @property
    def page(self) -> Page[BinRow]:
        return self._page

    @property
    def columns(self) -> tuple[Column, ...]:
        return self._columns

    def row_at(self, row: int) -> BinRow | None:
        return self._rows[row] if 0 <= row < len(self._rows) else None

    def rows(self) -> list[BinRow]:
        return list(self._rows)

    def column_at(self, index: int) -> Column | None:
        return self._columns[index] if 0 <= index < len(self._columns) else None

    def column_index(self, key: str) -> int:
        for index, column in enumerate(self._columns):
            if column.key == key:
                return index
        return -1

    def sort_key_for(self, section: int) -> str | None:
        column = self.column_at(section)
        return column.key if column and column.sortable else None

    def cell_text(self, row: int, column: int) -> str:
        record = self.row_at(row)
        info = self.column_at(column)
        if record is None or info is None:
            return UNKNOWN_DISPLAY
        return record.cell(info.key)

    def row_text(self, row: int, separator: str = "\t") -> str:
        record = self.row_at(row)
        if record is None:
            return ""
        return separator.join(record.cell(column.key) for column in self._columns)

    def header_text(self, separator: str = "\t") -> str:
        return separator.join(column.title for column in self._columns)

    @staticmethod
    def request_for(
        page: int, page_size: int, sort_by: str, ascending: bool
    ) -> PageRequest:
        return PageRequest(
            page=max(1, page),
            page_size=page_size,
            sort_by=sort_by,
            direction=SortDirection.ASC if ascending else SortDirection.DESC,
        )
