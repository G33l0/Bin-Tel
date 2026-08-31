"""The central search experience.

A large field with an inline icon, a BIN/Bank mode selector and the keyboard
contract the whole application shares: ``Ctrl+K`` focuses, ``Enter`` searches,
``Esc`` clears.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QKeyEvent
from PyQt6.QtWidgets import (
    QCompleter,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QWidget,
)

from app.core.config import LookupMode, SearchBehavior
from app.ui.themes.icons import IconProvider
from app.utils.qt_helpers import debounce, hbox, set_property, vbox
from app.utils.validators import looks_like_bin


class ModeToggle(QWidget):
    """Segmented BIN / Bank selector."""

    mode_changed = pyqtSignal(object)  # LookupMode

    def __init__(self, mode: LookupMode = LookupMode.BIN, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._mode = mode
        layout: QHBoxLayout = hbox(self, spacing=6)

        self.bin_button = self._make_button("BIN Lookup", "bin-lookup", LookupMode.BIN)
        self.bank_button = self._make_button("Bank Lookup", "bank-lookup", LookupMode.BANK)
        layout.addWidget(self.bin_button)
        layout.addWidget(self.bank_button)
        self.set_mode(mode, notify=False)

    def _make_button(self, text: str, icon_name: str, mode: LookupMode) -> QPushButton:
        button = QPushButton(text, self)
        button.setProperty("variant", "ghost")
        button.setCheckable(True)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setAccessibleName(f"Switch to {text}")
        button.setIcon(IconProvider.instance().icon(icon_name, size=16))
        button.clicked.connect(lambda: self.set_mode(mode))
        return button

    @property
    def mode(self) -> LookupMode:
        return self._mode

    def set_mode(self, mode: LookupMode, *, notify: bool = True) -> None:
        self._mode = mode
        self.bin_button.setChecked(mode is LookupMode.BIN)
        self.bank_button.setChecked(mode is LookupMode.BANK)
        self.refresh_icons()
        if notify:
            self.mode_changed.emit(mode)

    def toggle(self) -> None:
        self.set_mode(LookupMode.BANK if self._mode is LookupMode.BIN else LookupMode.BIN)

    def refresh_icons(self) -> None:
        provider = IconProvider.instance()
        theme = provider.theme
        for button, mode, name in (
            (self.bin_button, LookupMode.BIN, "bin-lookup"),
            (self.bank_button, LookupMode.BANK, "bank-lookup"),
        ):
            active = self._mode is mode
            button.setIcon(
                provider.icon(name, theme.nav_active_fg if active else theme.text_secondary, 16)
            )


class SearchBox(QWidget):
    """Large search field with a leading icon and a clear button."""

    search_requested = pyqtSignal(str)
    text_changed = pyqtSignal(str)
    cleared = pyqtSignal()

    def __init__(
        self,
        placeholder: str = "Search…",
        parent: QWidget | None = None,
        *,
        behavior: SearchBehavior = SearchBehavior.ON_ENTER,
        delay_ms: int = 250,
    ) -> None:
        super().__init__(parent)
        self._behavior = behavior

        layout = vbox(self, spacing=6)

        row = QWidget(self)
        row_layout = hbox(row)
        self.field = _SearchLineEdit(row)
        self.field.setProperty("role", "search")
        self.field.setPlaceholderText(placeholder)
        self.field.setClearButtonEnabled(True)
        self.field.setAccessibleName(placeholder)
        self.field.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.field.returnPressed.connect(self._emit_search)
        self.field.textChanged.connect(self._on_text_changed)
        self.field.escape_pressed.connect(self.clear)
        row_layout.addWidget(self.field)

        # The magnifier sits inside the field's left padding.
        self.icon_label = QLabel(self.field)
        self.icon_label.setFixedSize(18, 18)
        self.icon_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        layout.addWidget(row)

        self.hint_label = QLabel("", self)
        self.hint_label.setProperty("role", "muted")
        self.hint_label.setVisible(False)
        layout.addWidget(self.hint_label)

        self._debounce = debounce(self, delay_ms, self._emit_search)
        self.refresh_icon()

    # -- appearance -------------------------------------------------------
    def refresh_icon(self) -> None:
        provider = IconProvider.instance()
        self.icon_label.setPixmap(provider.pixmap("search", provider.theme.text_muted, 18))
        self._position_icon()

    def _position_icon(self) -> None:
        self.icon_label.move(13, max(0, (self.field.height() - 18) // 2))

    def resizeEvent(self, event: object) -> None:  # noqa: N802 - Qt API
        super().resizeEvent(event)  # type: ignore[arg-type]
        self._position_icon()

    # -- behaviour --------------------------------------------------------
    def set_behavior(self, behavior: SearchBehavior, delay_ms: int | None = None) -> None:
        self._behavior = behavior
        if delay_ms is not None:
            self._debounce.setInterval(max(0, delay_ms))

    def set_placeholder(self, text: str) -> None:
        self.field.setPlaceholderText(text)
        self.field.setAccessibleName(text)

    def set_history(self, entries: list[str]) -> None:
        """Attach recent searches as an inline completer."""
        if not entries:
            self.field.setCompleter(None)
            return
        completer = QCompleter(entries, self.field)
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        completer.setFilterMode(Qt.MatchFlag.MatchContains)
        completer.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
        self.field.setCompleter(completer)

    @property
    def text(self) -> str:
        return self.field.text().strip()

    def set_text(self, value: str) -> None:
        self.field.setText(value)

    def focus(self) -> None:
        self.field.setFocus(Qt.FocusReason.ShortcutFocusReason)
        self.field.selectAll()

    def clear(self) -> None:
        if not self.field.text():
            return
        self.field.clear()
        self.set_error(False)
        self.set_hint("")
        self.cleared.emit()

    def set_error(self, is_error: bool) -> None:
        set_property(self.field, "state", "error" if is_error else "")

    def set_hint(self, text: str) -> None:
        self.hint_label.setText(text)
        self.hint_label.setVisible(bool(text))

    # -- internals --------------------------------------------------------
    def _on_text_changed(self, text: str) -> None:
        self.set_error(False)
        self.text_changed.emit(text)
        if self._behavior is SearchBehavior.AS_YOU_TYPE:
            if text.strip():
                self._debounce.start()
            else:
                self._debounce.stop()

    def _emit_search(self) -> None:
        self._debounce.stop()
        value = self.text
        if value:
            self.search_requested.emit(value)

    def suggested_mode(self) -> LookupMode:
        """Which lookup the current text most likely wants."""
        return LookupMode.BIN if looks_like_bin(self.text) else LookupMode.BANK


class _SearchLineEdit(QLineEdit):
    """A line edit that reports Escape so the page can clear its results."""

    escape_pressed = pyqtSignal()

    def keyPressEvent(self, event: QKeyEvent | None) -> None:  # noqa: N802 - Qt API
        if event is not None and event.key() == Qt.Key.Key_Escape:
            self.escape_pressed.emit()
            event.accept()
            return
        super().keyPressEvent(event)
