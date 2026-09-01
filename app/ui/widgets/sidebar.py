"""Collapsible primary navigation."""

from __future__ import annotations

from dataclasses import dataclass

from PyQt6.QtCore import QSize, Qt, pyqtSignal
from PyQt6.QtGui import QFontMetrics
from PyQt6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QLabel,
    QPushButton,
    QSizePolicy,
    QWidget,
)

from app.core.constants import APP_VERSION
from app.ui.themes.icons import IconProvider
from app.utils.qt_helpers import expanding_spacer, hbox, vbox


@dataclass(frozen=True, slots=True)
class NavItem:
    key: str
    label: str
    icon: str
    tooltip: str = ""
    section: str = ""


#: The application's primary navigation, in order.
NAV_ITEMS: tuple[NavItem, ...] = (
    NavItem("dashboard", "Dashboard", "dashboard", "Database health and coverage (Ctrl+1)", "Intelligence"),
    NavItem("bin_lookup", "BIN Lookup", "bin-lookup", "Look up a BIN or IIN (Ctrl+2)", "Intelligence"),
    NavItem("bank_lookup", "Bank Lookup", "bank-lookup", "Find an institution and its BINs (Ctrl+3)", "Intelligence"),
    NavItem(
        "institutions",
        "Institution Intelligence",
        "shield",
        "Profile an institution and its portfolio (Ctrl+4)",
        "Intelligence",
    ),
    NavItem(
        "analytics",
        "Analytics",
        "columns",
        "Coverage, distribution and growth (Ctrl+5)",
        "Intelligence",
    ),
    NavItem(
        "watchlists",
        "Watchlists",
        "filter",
        "Track records and see what changed",
        "Intelligence",
    ),
    NavItem("reports", "Reports", "export", "Build and export reports", "Intelligence"),
    NavItem("database", "Database", "database", "Database status and backups", "Maintenance"),
    NavItem("admin", "Administration", "shield", "Health, integrity and maintenance", "Maintenance"),
    NavItem("updates", "Updates", "updates", "Check for and install database updates", "Maintenance"),
    NavItem("settings", "Settings", "settings", "Preferences (Ctrl+,)", "Application"),
    NavItem("about", "About", "about", f"About Bin-Tel {APP_VERSION}", "Application"),
)

EXPANDED_WIDTH = 228
COLLAPSED_WIDTH = 60


def _escape_mnemonic(text: str) -> str:
    """Keep a literal ampersand visible — Qt would treat it as an accelerator."""
    return text.replace("&", "&&")


class NavButton(QPushButton):
    """One navigation entry; hides its label when the sidebar collapses."""

    def __init__(self, item: NavItem, parent: QWidget | None = None) -> None:
        super().__init__(item.label, parent)
        self.item = item
        self._collapsed = False
        self._count = 0
        self.setObjectName("NavButton")
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(item.tooltip or item.label)
        self.setAccessibleName(item.label)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setIconSize(QSize(19, 19))
        self.refresh_icon()
        self._apply_text()

    def refresh_icon(self) -> None:
        provider = IconProvider.instance()
        theme = provider.theme
        colour = theme.nav_active_fg if self.isChecked() else theme.nav_fg
        self.setIcon(provider.icon(self.item.icon, colour, 19))

    def set_count(self, count: int) -> None:
        """An unread count — new watchlist alerts, for instance."""
        self._count = max(0, count)
        self._apply_text()

    def set_collapsed(self, collapsed: bool) -> None:
        self._collapsed = collapsed
        self._apply_text()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._apply_text()

    def _text_width(self) -> int:
        """Room the label has once the icon and the QSS padding are taken."""
        return max(48, self.width() - self.iconSize().width() - 46)

    def _apply_text(self) -> None:
        suffix = str(self._count) if self._count else ""
        if self._collapsed:
            self.setText("")
        elif suffix:
            # The count always survives; the label is what gets elided, so a
            # long entry never truncates the number away.
            metrics = QFontMetrics(self.font())
            room = self._text_width() - metrics.horizontalAdvance(f"   {suffix}")
            label = metrics.elidedText(
                self.item.label, Qt.TextElideMode.ElideRight, max(32, room)
            )
            self.setText(_escape_mnemonic(f"{label}   {suffix}"))
        else:
            self.setText(_escape_mnemonic(self.item.label))

        tooltip = self.item.tooltip or self.item.label
        accessible = self.item.label
        if self._count:
            tooltip = f"{tooltip} — {self._count} new"
            accessible = f"{accessible}, {self._count} new"
        self.setToolTip(tooltip)
        self.setAccessibleName(accessible)


class Sidebar(QFrame):
    """The navigation rail. Emits :attr:`navigated` with the item key."""

    navigated = pyqtSignal(str)
    collapse_toggled = pyqtSignal(bool)

    def __init__(self, parent: QWidget | None = None, *, collapsed: bool = False) -> None:
        super().__init__(parent)
        self.setObjectName("Sidebar")
        self._collapsed = collapsed
        self._buttons: dict[str, NavButton] = {}
        self._section_labels: list[QLabel] = []

        layout = vbox(self, margins=(0, 10, 0, 10), spacing=2)
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)

        current_section = ""
        for item in NAV_ITEMS:
            if item.section and item.section != current_section:
                current_section = item.section
                label = QLabel(item.section.upper(), self)
                label.setObjectName("SidebarSectionLabel")
                layout.addWidget(label)
                self._section_labels.append(label)

            button = NavButton(item, self)
            button.clicked.connect(lambda _=False, key=item.key: self._on_click(key))
            self._group.addButton(button)
            self._buttons[item.key] = button
            layout.addWidget(button)

        layout.addItem(expanding_spacer(horizontal=False))

        footer = QWidget(self)
        footer_layout = hbox(footer, margins=(10, 4, 10, 0), spacing=8)
        self.collapse_button = QPushButton("", footer)
        self.collapse_button.setProperty("variant", "ghost")
        self.collapse_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.collapse_button.setAccessibleName("Collapse the sidebar")
        self.collapse_button.clicked.connect(self.toggle_collapsed)
        footer_layout.addWidget(self.collapse_button)
        footer_layout.addItem(expanding_spacer())

        self.version_label = QLabel(f"v{APP_VERSION}", footer)
        self.version_label.setProperty("role", "muted")
        footer_layout.addWidget(self.version_label)
        layout.addWidget(footer)

        self.set_collapsed(collapsed)

    # -- selection --------------------------------------------------------
    def _on_click(self, key: str) -> None:
        self.refresh_icons()
        self.navigated.emit(key)

    def select(self, key: str, *, notify: bool = False) -> None:
        button = self._buttons.get(key)
        if button is None:
            return
        button.setChecked(True)
        self.refresh_icons()
        if notify:
            self.navigated.emit(key)

    @property
    def current_key(self) -> str:
        for key, button in self._buttons.items():
            if button.isChecked():
                return key
        return NAV_ITEMS[0].key

    def keys(self) -> list[str]:
        return [item.key for item in NAV_ITEMS]

    # -- collapsing -------------------------------------------------------
    @property
    def collapsed(self) -> bool:
        return self._collapsed

    def set_collapsed(self, collapsed: bool, *, notify: bool = False) -> None:
        self._collapsed = collapsed
        self.setFixedWidth(COLLAPSED_WIDTH if collapsed else EXPANDED_WIDTH)
        for button in self._buttons.values():
            button.set_collapsed(collapsed)
        for label in self._section_labels:
            label.setVisible(not collapsed)
        self.version_label.setVisible(not collapsed)
        self.collapse_button.setAccessibleName(
            "Expand the sidebar" if collapsed else "Collapse the sidebar"
        )
        self.collapse_button.setToolTip(
            "Expand the sidebar (Ctrl+B)" if collapsed else "Collapse the sidebar (Ctrl+B)"
        )
        self.refresh_icons()
        if notify:
            self.collapse_toggled.emit(collapsed)

    def toggle_collapsed(self) -> None:
        self.set_collapsed(not self._collapsed, notify=True)

    # -- badges -----------------------------------------------------------
    def set_badge_count(self, key: str, count: int) -> None:
        button = self._buttons.get(key)
        if button is not None:
            button.set_count(count)

    def refresh_icons(self) -> None:
        provider = IconProvider.instance()
        for button in self._buttons.values():
            button.refresh_icon()
        self.collapse_button.setIcon(
            provider.icon(
                "chevron-right" if self._collapsed else "chevron-left",
                provider.theme.text_muted,
                16,
            )
        )
