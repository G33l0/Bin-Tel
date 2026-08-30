"""Application header: branding, global search, status and quick actions."""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QFrame, QLabel, QWidget

from app.core.config import LookupMode
from app.ui.themes.icons import IconProvider
from app.ui.widgets.brand import BrandLockup
from app.ui.widgets.cards import IconButton
from app.ui.widgets.search_box import SearchBox
from app.utils.qt_helpers import expanding_spacer, hbox


class AppHeader(QFrame):
    """The top bar. Its search field is the application-wide entry point."""

    search_requested = pyqtSignal(str, object)  # query, LookupMode
    theme_cycle_requested = pyqtSignal()
    settings_requested = pyqtSignal()
    sidebar_toggle_requested = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("AppHeader")
        self.setFixedHeight(62)

        layout = hbox(self, margins=(12, 0, 14, 0), spacing=12)

        self.menu_button = IconButton(
            "menu", "Show or hide the sidebar (Ctrl+B)", self, on_click=self.sidebar_toggle_requested.emit
        )
        layout.addWidget(self.menu_button)

        self.brand = BrandLockup(self, mark_size=28)
        layout.addWidget(self.brand)

        layout.addSpacing(8)

        self.search = SearchBox("Search a BIN or an institution…   Ctrl+K", self)
        self.search.field.setProperty("role", "searchCompact")
        self.search.field.setFixedHeight(34)
        self.search.setMaximumWidth(520)
        self.search.search_requested.connect(self._on_search)
        layout.addWidget(self.search, 1)

        layout.addItem(expanding_spacer())

        self.status_label = QLabel("", self)
        self.status_label.setProperty("role", "muted")
        self.status_label.setVisible(False)
        layout.addWidget(self.status_label)

        self.offline_icon = QLabel(self)
        self.offline_icon.setFixedSize(18, 18)
        self.offline_icon.setToolTip("Bin-Tel is offline. Local lookups still work.")
        self.offline_icon.setAccessibleName("Offline")
        self.offline_icon.setVisible(False)
        layout.addWidget(self.offline_icon)

        self.theme_button = IconButton(
            "theme", "Switch theme (Ctrl+T)", self, on_click=self.theme_cycle_requested.emit
        )
        layout.addWidget(self.theme_button)

        self.settings_button = IconButton(
            "settings", "Settings (Ctrl+,)", self, on_click=self.settings_requested.emit
        )
        layout.addWidget(self.settings_button)

        self.refresh_theme()

    # -- behaviour --------------------------------------------------------
    def _on_search(self, query: str) -> None:
        self.search_requested.emit(query, self.search.suggested_mode())

    def focus_search(self) -> None:
        self.search.focus()

    def set_offline(self, offline: bool) -> None:
        self.offline_icon.setVisible(offline)

    def set_status(self, text: str) -> None:
        self.status_label.setText(text)
        self.status_label.setVisible(bool(text))

    def suggested_mode(self) -> LookupMode:
        return self.search.suggested_mode()

    def refresh_theme(self) -> None:
        provider = IconProvider.instance()
        self.brand.refresh()
        self.search.refresh_icon()
        for button in (self.menu_button, self.theme_button, self.settings_button):
            button.refresh_icon()
        self.offline_icon.setPixmap(provider.pixmap("offline", provider.theme.warning, 18))
