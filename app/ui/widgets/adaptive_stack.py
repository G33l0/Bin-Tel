"""A stacked container that sizes itself to the page currently on show.

A plain :class:`QStackedWidget` reserves room for its tallest page: its
:class:`QStackedLayout` reports the largest hint of everything it holds, and a
parent layout asks that layout directly rather than the widget. That is fine
when the pages are about the same size, but these stacks routinely mix a tall
result surface with a short empty, loading or upgrade state — and the short one
would then be stranded in the middle of a large blank scroll area instead of
sitting where content begins.

So the layout itself is the thing that has to answer for the visible page, and
this module supplies one, wrapped in the small slice of the stacked-widget API
the application uses.
"""

from __future__ import annotations

from PyQt6.QtCore import QSize, pyqtSignal
from PyQt6.QtWidgets import QLayout, QStackedLayout, QWidget


class _AdaptiveStackedLayout(QStackedLayout):
    """Stacked layout that measures only the page on show."""

    def sizeHint(self) -> QSize:  # noqa: N802
        widget = self.currentWidget()
        return widget.sizeHint() if widget is not None else super().sizeHint()

    def minimumSize(self) -> QSize:  # noqa: N802
        widget = self.currentWidget()
        return widget.minimumSizeHint() if widget is not None else super().minimumSize()

    def hasHeightForWidth(self) -> bool:  # noqa: N802
        widget = self.currentWidget()
        return widget.hasHeightForWidth() if widget is not None else False

    def heightForWidth(self, width: int) -> int:  # noqa: N802
        widget = self.currentWidget()
        if widget is None:
            return super().heightForWidth(width)
        if widget.hasHeightForWidth():
            return widget.heightForWidth(width)
        return widget.sizeHint().height()


class AdaptiveStack(QWidget):
    """One page at a time, sized to the page on show rather than the tallest."""

    currentChanged = pyqtSignal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._layout = _AdaptiveStackedLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
        self._layout.currentChanged.connect(self._on_current_changed)

    # -- stacked-widget API ------------------------------------------------
    def addWidget(self, widget: QWidget) -> int:  # noqa: N802
        index = self._layout.addWidget(widget)
        self.updateGeometry()
        return index

    def count(self) -> int:
        return self._layout.count()

    def widget(self, index: int) -> QWidget | None:
        return self._layout.widget(index)

    def currentWidget(self) -> QWidget | None:  # noqa: N802
        return self._layout.currentWidget()

    def currentIndex(self) -> int:  # noqa: N802
        return self._layout.currentIndex()

    def setCurrentWidget(self, widget: QWidget) -> None:  # noqa: N802
        self._layout.setCurrentWidget(widget)

    def setCurrentIndex(self, index: int) -> None:  # noqa: N802
        self._layout.setCurrentIndex(index)

    def show_widget(self, widget: QWidget) -> None:
        """Switch to *widget* — a named alternative to ``setCurrentWidget``."""
        self.setCurrentWidget(widget)

    def _on_current_changed(self, index: int) -> None:
        self.updateGeometry()
        self.currentChanged.emit(index)
