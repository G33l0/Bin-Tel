"""Shared page scaffolding.

Every page gets a consistent title block, a scrollable body and the same access
to the application context — so no page needs its own idea of layout, and none
of them touch the database directly.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QLabel, QScrollArea, QVBoxLayout, QWidget

from app.core.context import AppContext
from app.utils.qt_helpers import expanding_spacer, hbox, vbox

CONTENT_MARGIN = 26
CONTENT_MAX_WIDTH = 1180


class BasePage(QWidget):
    """A titled, scrollable page."""

    #: Key used by the sidebar and the window's page stack.
    key: str = "page"
    #: Title shown at the top of the page.
    title: str = ""
    #: One-line explanation under the title.
    subtitle: str = ""

    status_message = pyqtSignal(str)
    navigation_requested = pyqtSignal(str)
    toast_requested = pyqtSignal(str)

    def __init__(self, context: AppContext, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.context = context
        self._loaded_once = False

        outer = vbox(self)

        self.scroll = QScrollArea(self)
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        outer.addWidget(self.scroll)

        container = QWidget(self.scroll)
        container_layout = hbox(container)
        self.scroll.setWidget(container)

        self.surface = QWidget(container)
        self.surface.setMaximumWidth(CONTENT_MAX_WIDTH)
        self.content: QVBoxLayout = vbox(
            self.surface,
            margins=(CONTENT_MARGIN, CONTENT_MARGIN, CONTENT_MARGIN, CONTENT_MARGIN),
            spacing=18,
        )
        container_layout.addWidget(self.surface, 1)

        if self.title:
            self.content.addWidget(self._build_title_block())

    def _build_title_block(self) -> QWidget:
        block = QWidget(self.surface)
        layout = vbox(block, spacing=4)

        self.title_label = QLabel(self.title, block)
        self.title_label.setProperty("role", "pageTitle")
        layout.addWidget(self.title_label)

        self.subtitle_label = QLabel(self.subtitle, block)
        self.subtitle_label.setProperty("role", "pageSubtitle")
        self.subtitle_label.setWordWrap(True)
        self.subtitle_label.setVisible(bool(self.subtitle))
        layout.addWidget(self.subtitle_label)
        return block

    # -- lifecycle --------------------------------------------------------
    def add_stretch(self) -> None:
        """Push content to the top of the page."""
        self.content.addItem(expanding_spacer(horizontal=False))

    def on_shown(self) -> None:
        """Called each time the page becomes visible."""
        if not self._loaded_once:
            self._loaded_once = True
            self.on_first_show()
        self.refresh()

    def on_first_show(self) -> None:
        """Called once, the first time the page is displayed."""

    def refresh(self) -> None:
        """Reload whatever the page displays. Must never block the GUI thread."""

    def on_theme_changed(self) -> None:
        """Re-render any manually painted pixmap after a theme switch."""

    def on_database_changed(self) -> None:
        """Called after the database is replaced, moved or reopened."""
        self.refresh()

    # -- helpers ----------------------------------------------------------
    def toast(self, message: str) -> None:
        self.toast_requested.emit(message)

    def navigate(self, key: str) -> None:
        self.navigation_requested.emit(key)

    def show_error(self, exc: BaseException) -> bool:
        from app.ui.dialogs.error_dialog import ErrorDialog

        return ErrorDialog.show_for(exc, self)
