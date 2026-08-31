"""The global command palette.

``Ctrl+K`` from anywhere. It answers with whatever the typed text most likely
means — a BIN, an institution, a country, a saved search, a page to open, or a
command to run — and every candidate is reached from the same keyboard.

Database results are fetched on a worker thread and delivered by signal, so
typing never blocks the interface even against a multi-million-row database.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

from PyQt6.QtCore import QEvent, QObject, Qt, pyqtSignal
from PyQt6.QtGui import QKeyEvent
from PyQt6.QtWidgets import (
    QDialog,
    QFrame,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QWidget,
)

from app.ui.themes.icons import IconProvider
from app.utils.qt_helpers import debounce, hbox, vbox
from app.utils.validators import clean_digits, looks_like_bin


class ResultKind(StrEnum):
    BIN = "bin"
    INSTITUTION = "institution"
    COUNTRY = "country"
    SAVED_SEARCH = "saved_search"
    RECENT = "recent"
    PAGE = "page"
    COMMAND = "command"

    @property
    def label(self) -> str:
        return {
            ResultKind.BIN: "BIN",
            ResultKind.INSTITUTION: "Institution",
            ResultKind.COUNTRY: "Country",
            ResultKind.SAVED_SEARCH: "Saved search",
            ResultKind.RECENT: "Recent",
            ResultKind.PAGE: "Go to",
            ResultKind.COMMAND: "Command",
        }[self]

    @property
    def icon(self) -> str:
        return {
            ResultKind.BIN: "bin-lookup",
            ResultKind.INSTITUTION: "bank-lookup",
            ResultKind.COUNTRY: "globe",
            ResultKind.SAVED_SEARCH: "filter",
            ResultKind.RECENT: "refresh",
            ResultKind.PAGE: "chevron-right",
            ResultKind.COMMAND: "settings",
        }[self]


@dataclass(frozen=True, slots=True)
class PaletteResult:
    """One selectable row."""

    kind: ResultKind
    value: str
    title: str
    subtitle: str = ""
    #: Sort weight; lower appears first.
    rank: int = 50

    @property
    def accessible_text(self) -> str:
        return f"{self.kind.label}: {self.title}. {self.subtitle}".strip()


@dataclass(frozen=True, slots=True)
class Command:
    """A named action the palette can run."""

    key: str
    title: str
    subtitle: str = ""
    keywords: tuple[str, ...] = ()

    def matches(self, needle: str) -> bool:
        haystack = " ".join([self.title.lower(), self.subtitle.lower(), *self.keywords])
        return all(part in haystack for part in needle.lower().split())


class CommandPalette(QDialog):
    """Frameless overlay listing the best matches for what was typed."""

    result_chosen = pyqtSignal(object)  # PaletteResult
    query_changed = pyqtSignal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowFlags(
            Qt.WindowType.Dialog
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setModal(True)
        self.setMinimumWidth(620)
        self.setMaximumWidth(760)

        self._commands: list[Command] = []
        self._pages: list[tuple[str, str, str]] = []
        self._static: list[PaletteResult] = []
        self._dynamic: list[PaletteResult] = []
        self._provider: Callable[[str], None] | None = None

        outer = vbox(self)
        body = QFrame(self)
        body.setObjectName("DialogBody")
        outer.addWidget(body)
        layout = vbox(body, margins=(0, 0, 0, 0), spacing=0)

        search_row = QWidget(body)
        search_layout = hbox(search_row, margins=(16, 14, 16, 12), spacing=10)
        self.icon_label = QLabel(search_row)
        self.icon_label.setFixedSize(18, 18)
        search_layout.addWidget(self.icon_label)

        self.field = QLineEdit(search_row)
        self.field.setPlaceholderText(
            "Search a BIN, an institution, a country — or type a command…"
        )
        self.field.setAccessibleName("Command palette search")
        self.field.setFrame(False)
        self.field.setStyleSheet("background: transparent; border: none; font-size: 14pt;")
        self.field.textChanged.connect(self._on_text_changed)
        self.field.installEventFilter(self)
        search_layout.addWidget(self.field, 1)

        self.hint_label = QLabel("Ctrl+K", search_row)
        self.hint_label.setProperty("role", "muted")
        search_layout.addWidget(self.hint_label)
        layout.addWidget(search_row)

        divider = QFrame(body)
        divider.setObjectName("Divider")
        divider.setFixedHeight(1)
        layout.addWidget(divider)

        self.list_widget = QListWidget(body)
        self.list_widget.setFrameShape(QFrame.Shape.NoFrame)
        self.list_widget.setAccessibleName("Command palette results")
        self.list_widget.setUniformItemSizes(False)
        self.list_widget.itemActivated.connect(self._on_item_activated)
        self.list_widget.itemClicked.connect(self._on_item_activated)
        self.list_widget.setMinimumHeight(280)
        layout.addWidget(self.list_widget, 1)

        footer = QWidget(body)
        footer_layout = hbox(footer, margins=(16, 8, 16, 10), spacing=14)
        for text in ("↑↓ Navigate", "↵ Open", "Esc Close"):
            label = QLabel(text, footer)
            label.setProperty("role", "muted")
            footer_layout.addWidget(label)
        footer_layout.addStretch(1)
        self.status_label = QLabel("", footer)
        self.status_label.setProperty("role", "muted")
        footer_layout.addWidget(self.status_label)
        layout.addWidget(footer)

        self._debounce = debounce(self, 140, self._request_dynamic)
        self.refresh_theme()

    # -- configuration -----------------------------------------------------
    def set_pages(self, pages: list[tuple[str, str, str]]) -> None:
        """``(key, title, subtitle)`` navigation targets."""
        self._pages = pages

    def set_commands(self, commands: list[Command]) -> None:
        self._commands = commands

    def set_result_provider(self, provider: Callable[[str], None] | None) -> None:
        """Called on each keystroke; results arrive via :meth:`set_results`."""
        self._provider = provider

    def set_static_results(self, results: list[PaletteResult]) -> None:
        """Saved searches and recent searches, refreshed when the palette opens."""
        self._static = results

    def set_results(self, results: list[PaletteResult]) -> None:
        """Deliver database results from the worker."""
        self._dynamic = results
        self._render()

    # -- lifecycle ---------------------------------------------------------
    def open_palette(self, initial: str = "") -> None:
        self.field.setText(initial)
        self.field.selectAll()
        self._dynamic = []
        self._render()
        self._position()
        self.show()
        self.raise_()
        self.field.setFocus(Qt.FocusReason.ShortcutFocusReason)
        if initial:
            self._debounce.start()

    def _position(self) -> None:
        parent = self.parentWidget()
        self.adjustSize()
        width = min(max(620, self.width()), 760)
        self.setFixedWidth(width)
        if parent is None:
            return
        window = parent.window()
        origin = window.mapToGlobal(window.rect().topLeft())
        x = origin.x() + (window.width() - width) // 2
        y = origin.y() + max(60, int(window.height() * 0.12))
        self.move(x, y)

    # -- input -------------------------------------------------------------
    def eventFilter(self, source: QObject | None, event: QEvent | None) -> bool:  # noqa: N802
        """Route arrow keys to the list while the field keeps focus."""
        if source is self.field and isinstance(event, QKeyEvent) and event.type() == QEvent.Type.KeyPress:
            key = event.key()
            if key in (Qt.Key.Key_Down, Qt.Key.Key_Up):
                count = self.list_widget.count()
                if count:
                    step = 1 if key == Qt.Key.Key_Down else -1
                    row = (self.list_widget.currentRow() + step) % count
                    self.list_widget.setCurrentRow(row)
                return True
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                item = self.list_widget.currentItem()
                if item is not None:
                    self._on_item_activated(item)
                return True
            if key == Qt.Key.Key_Escape:
                self.reject()
                return True
        return super().eventFilter(source, event)

    def _on_text_changed(self, text: str) -> None:
        self.query_changed.emit(text)
        self._render()
        if len(text.strip()) >= 2:
            self._debounce.start()
        else:
            self._dynamic = []

    def _request_dynamic(self) -> None:
        term = self.field.text().strip()
        if self._provider is not None and len(term) >= 2:
            self._provider(term)

    def _on_item_activated(self, item: QListWidgetItem) -> None:
        result = item.data(Qt.ItemDataRole.UserRole)
        if isinstance(result, PaletteResult):
            self.accept()
            self.result_chosen.emit(result)

    # -- rendering ---------------------------------------------------------
    def _render(self) -> None:
        term = self.field.text().strip()
        results = self._compose(term)
        self.list_widget.clear()

        provider = IconProvider.instance()
        theme = provider.theme
        for result in results:
            item = QListWidgetItem(self.list_widget)
            widget = _ResultRow(result, self.list_widget)
            item.setData(Qt.ItemDataRole.UserRole, result)
            item.setSizeHint(widget.sizeHint())
            item.setToolTip(result.accessible_text)
            self.list_widget.addItem(item)
            self.list_widget.setItemWidget(item, widget)
        if self.list_widget.count():
            self.list_widget.setCurrentRow(0)

        self.status_label.setText(
            f"{len(results)} result(s)" if term else "Start typing to search"
        )
        _ = theme

    def _compose(self, term: str) -> list[PaletteResult]:
        """Merge every source, rank them, and cap the list."""
        results: list[PaletteResult] = []
        needle = term.lower()

        if not term:
            results.extend(self._static[:6])
            results.extend(
                PaletteResult(ResultKind.PAGE, key, title, subtitle, rank=70)
                for key, title, subtitle in self._pages
            )
            return results[:14]

        # A numeric query is a BIN before it is anything else.
        digits = clean_digits(term)
        if looks_like_bin(term) and len(digits) >= 4:
            results.append(
                PaletteResult(
                    ResultKind.BIN,
                    digits,
                    f"Look up BIN {digits}",
                    "Open the BIN lookup for this number",
                    rank=0,
                )
            )
        elif len(term) >= 2:
            results.append(
                PaletteResult(
                    ResultKind.INSTITUTION,
                    term,
                    f"Search institutions for “{term}”",
                    "Open the bank lookup",
                    rank=5,
                )
            )

        results.extend(self._dynamic)
        results.extend(
            item
            for item in self._static
            if needle in item.title.lower() or needle in item.subtitle.lower()
        )
        results.extend(
            PaletteResult(ResultKind.PAGE, key, title, subtitle, rank=70)
            for key, title, subtitle in self._pages
            if needle in title.lower() or needle in subtitle.lower()
        )
        results.extend(
            PaletteResult(
                ResultKind.COMMAND, command.key, command.title, command.subtitle, rank=80
            )
            for command in self._commands
            if command.matches(term)
        )

        seen: set[tuple[str, str]] = set()
        unique: list[PaletteResult] = []
        for result in sorted(results, key=lambda item: item.rank):
            key = (result.kind.value, result.value)
            if key in seen:
                continue
            seen.add(key)
            unique.append(result)
        return unique[:16]

    def refresh_theme(self) -> None:
        provider = IconProvider.instance()
        self.icon_label.setPixmap(provider.pixmap("search", provider.theme.text_muted, 18))


class _ResultRow(QWidget):
    """One palette row: icon, title, subtitle and a kind badge."""

    def __init__(self, result: PaletteResult, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        provider = IconProvider.instance()
        theme = provider.theme

        layout = hbox(self, margins=(14, 8, 14, 8), spacing=12)

        icon = QLabel(self)
        icon.setFixedSize(18, 18)
        icon.setPixmap(provider.pixmap(result.kind.icon, theme.text_secondary, 17))
        layout.addWidget(icon, 0, Qt.AlignmentFlag.AlignVCenter)

        column = vbox(spacing=1)
        title = QLabel(result.title, self)
        title.setProperty("role", "fieldValue")
        column.addWidget(title)
        if result.subtitle:
            subtitle = QLabel(result.subtitle, self)
            subtitle.setProperty("role", "muted")
            column.addWidget(subtitle)
        layout.addLayout(column, 1)

        badge = QLabel(result.kind.label, self)
        badge.setProperty("role", "muted")
        layout.addWidget(badge, 0, Qt.AlignmentFlag.AlignVCenter)

        self.setAccessibleName(result.accessible_text)
