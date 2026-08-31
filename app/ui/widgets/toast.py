"""Transient confirmations ("Copied", "Exported") that never block the user."""

from __future__ import annotations

from PyQt6.QtCore import QPoint, Qt, QTimer
from PyQt6.QtWidgets import QFrame, QLabel, QWidget

from app.ui.themes.icons import IconProvider
from app.utils.qt_helpers import hbox


class Toast(QFrame):
    """A small floating panel that fades out on a timer."""

    def __init__(self, parent: QWidget, message: str, *, icon_name: str = "check") -> None:
        super().__init__(parent)
        self.setObjectName("Toast")
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setWindowFlags(Qt.WindowType.ToolTip | Qt.WindowType.FramelessWindowHint)

        layout = hbox(self, margins=(14, 10, 16, 10), spacing=9)
        provider = IconProvider.instance()

        self.icon_label = QLabel(self)
        self.icon_label.setPixmap(provider.pixmap(icon_name, provider.theme.success, 16))
        self.icon_label.setFixedSize(16, 16)
        layout.addWidget(self.icon_label)

        self.message_label = QLabel(message, self)
        layout.addWidget(self.message_label)

        self.adjustSize()

    @classmethod
    def show_message(
        cls,
        parent: QWidget,
        message: str,
        *,
        icon_name: str = "check",
        duration_ms: int = 2200,
    ) -> Toast:
        """Show *message* near the bottom-centre of *parent*."""
        toast = cls(parent, message, icon_name=icon_name)
        toast.adjustSize()
        anchor = parent.window()
        top_left = anchor.mapToGlobal(QPoint(0, 0))
        x = top_left.x() + (anchor.width() - toast.width()) // 2
        y = top_left.y() + anchor.height() - toast.height() - 42
        toast.move(x, max(top_left.y() + 20, y))
        toast.show()
        toast.raise_()
        QTimer.singleShot(duration_ms, toast.close)
        return toast
