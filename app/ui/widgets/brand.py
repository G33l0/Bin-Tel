"""Bin-Tel brand marks as widgets."""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QLabel, QWidget

from app.core.constants import APP_NAME, APP_TAGLINE
from app.ui.themes.icons import IconProvider
from app.utils.qt_helpers import hbox, vbox


class BrandMark(QLabel):
    """The hexagonal Bin-Tel badge, rendered from the vector source."""

    def __init__(self, size: int = 28, parent: QWidget | None = None, *, small: bool = False) -> None:
        super().__init__(parent)
        self._size = size
        self._asset = "bintel-mark-small" if small or size <= 32 else "bintel-mark"
        self.setFixedSize(size, size)
        self.setAccessibleName(f"{APP_NAME} logo")
        self.refresh()

    def refresh(self) -> None:
        self.setPixmap(IconProvider.instance().brand_pixmap(self._asset, self._size))


class BrandLockup(QWidget):
    """Badge plus wordmark, used in the header and the About page."""

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        mark_size: int = 30,
        show_tagline: bool = False,
    ) -> None:
        super().__init__(parent)
        self._show_tagline = show_tagline

        layout = hbox(self, spacing=10)
        self.mark = BrandMark(mark_size, self)
        layout.addWidget(self.mark, 0, Qt.AlignmentFlag.AlignVCenter)

        text_column = vbox(spacing=0)
        self.name_label = QLabel(self)
        self.name_label.setTextFormat(Qt.TextFormat.RichText)
        self.name_label.setAccessibleName(APP_NAME)
        text_column.addWidget(self.name_label)

        self.tagline_label = QLabel(APP_TAGLINE, self)
        self.tagline_label.setProperty("role", "muted")
        self.tagline_label.setVisible(show_tagline)
        text_column.addWidget(self.tagline_label)

        layout.addLayout(text_column)
        self.refresh()

    def refresh(self, accent: str | None = None) -> None:
        """Re-render for the active theme."""
        theme = IconProvider.instance().theme
        colour = accent or theme.primary
        size = max(12, int(self.font().pointSize() * 1.45)) if self.font().pointSize() > 0 else 17
        self.name_label.setText(
            f'<span style="font-size:{size}pt;font-weight:700;letter-spacing:-0.4px;">'
            f'Bin<span style="color:{colour};">-</span>Tel</span>'
        )
        self.mark.refresh()


class BrandSplash(QLabel):
    """Large splash artwork for the first-run window."""

    def __init__(self, width: int = 460, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._width = width
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.refresh()

    def refresh(self) -> None:
        height = int(self._width * 300 / 520)
        self.setPixmap(
            IconProvider.instance().brand_wide_pixmap("bintel-splash", self._width, height)
        )
        self.setFixedHeight(height)
