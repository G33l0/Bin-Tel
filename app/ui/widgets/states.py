"""Empty, loading, error and success states.

Every view that can be blank has a designed state rather than an empty
rectangle, so the user always knows whether Bin-Tel is waiting, has nothing to
show, or has hit a problem.
"""

from __future__ import annotations

from collections.abc import Callable
from enum import StrEnum

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QFrame,
    QLabel,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QWidget,
)

from app.ui.themes.icons import IconProvider
from app.utils.qt_helpers import centered_paragraph, hbox, set_property, vbox


class StateKind(StrEnum):
    INFO = "info"
    SUCCESS = "success"
    WARNING = "warning"
    DANGER = "danger"

    @property
    def icon_name(self) -> str:
        return {
            StateKind.INFO: "about",
            StateKind.SUCCESS: "check",
            StateKind.WARNING: "warning",
            StateKind.DANGER: "error",
        }[self]


class EmptyState(QWidget):
    """Illustrated placeholder with a headline, a hint and an optional action."""

    action_triggered = pyqtSignal()

    def __init__(
        self,
        title: str = "Nothing to show yet",
        message: str = "",
        parent: QWidget | None = None,
        *,
        icon_name: str = "empty-box",
        action_text: str = "",
        on_action: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(parent)
        self._icon_name = icon_name
        layout = vbox(self, margins=(24, 40, 24, 40), spacing=12)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.icon_label = QLabel(self)
        self.icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.icon_label.setFixedHeight(52)
        layout.addWidget(self.icon_label)

        self.title_label = QLabel(title, self)
        self.title_label.setProperty("role", "sectionTitle")
        self.title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.title_label)

        message_block = centered_paragraph(message, self, max_width=460)
        self.message_label = message_block.label  # type: ignore[attr-defined]
        message_block.setVisible(bool(message))
        self._message_block = message_block
        layout.addWidget(message_block)

        self.action_button = QPushButton(action_text, self)
        self.action_button.setProperty("variant", "primary")
        self.action_button.setVisible(bool(action_text))
        self.action_button.clicked.connect(self.action_triggered.emit)
        if on_action is not None:
            self.action_button.clicked.connect(on_action)
        layout.addWidget(self.action_button, 0, Qt.AlignmentFlag.AlignHCenter)

        self.refresh_icon()

    def refresh_icon(self) -> None:
        provider = IconProvider.instance()
        self.icon_label.setPixmap(
            provider.pixmap(self._icon_name, provider.theme.text_muted, 48)
        )

    def configure(
        self,
        title: str,
        message: str = "",
        *,
        icon_name: str | None = None,
        action_text: str = "",
    ) -> None:
        self.title_label.setText(title)
        self.message_label.setText(message)
        self._message_block.setVisible(bool(message))
        self.action_button.setText(action_text)
        self.action_button.setVisible(bool(action_text))
        if icon_name:
            self._icon_name = icon_name
            self.refresh_icon()


class LoadingState(QWidget):
    """Indeterminate progress with a message — shown while a worker runs."""

    def __init__(self, message: str = "Working…", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = vbox(self, margins=(24, 44, 24, 44), spacing=14)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.message_label = QLabel(message, self)
        self.message_label.setProperty("role", "pageSubtitle")
        self.message_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.message_label)

        self.progress = QProgressBar(self)
        self.progress.setRange(0, 0)  # indeterminate
        self.progress.setTextVisible(False)
        self.progress.setFixedWidth(220)
        layout.addWidget(self.progress, 0, Qt.AlignmentFlag.AlignHCenter)

    def set_message(self, message: str) -> None:
        self.message_label.setText(message)


class ErrorState(QWidget):
    """Inline failure with a retry affordance — never a raw traceback."""

    retry_requested = pyqtSignal()

    def __init__(
        self,
        title: str = "Operation failed",
        message: str = "",
        parent: QWidget | None = None,
        *,
        retryable: bool = True,
    ) -> None:
        super().__init__(parent)
        layout = vbox(self, margins=(24, 40, 24, 40), spacing=12)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.icon_label = QLabel(self)
        self.icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.icon_label.setFixedHeight(44)
        layout.addWidget(self.icon_label)

        self.title_label = QLabel(title, self)
        self.title_label.setProperty("role", "sectionTitle")
        self.title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.title_label)

        message_block = centered_paragraph(message, self, max_width=460)
        self.message_label = message_block.label  # type: ignore[attr-defined]
        self._message_block = message_block
        layout.addWidget(message_block)

        self.retry_button = QPushButton("Retry", self)
        self.retry_button.setProperty("variant", "primary")
        self.retry_button.setVisible(retryable)
        self.retry_button.clicked.connect(self.retry_requested.emit)
        layout.addWidget(self.retry_button, 0, Qt.AlignmentFlag.AlignHCenter)

        self.refresh_icon()

    def refresh_icon(self) -> None:
        provider = IconProvider.instance()
        self.icon_label.setPixmap(provider.pixmap("warning", provider.theme.danger, 40))

    def configure(self, title: str, message: str, *, retryable: bool = True) -> None:
        self.title_label.setText(title)
        self.message_label.setText(message)
        self.retry_button.setVisible(retryable)
        self.refresh_icon()


class StateBanner(QFrame):
    """A slim, dismissible strip for status: offline, update available, saved."""

    action_triggered = pyqtSignal()
    dismissed = pyqtSignal()

    def __init__(
        self,
        message: str = "",
        kind: StateKind = StateKind.INFO,
        parent: QWidget | None = None,
        *,
        action_text: str = "",
        dismissible: bool = False,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("StateBanner")
        self._kind = kind
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        layout = hbox(self, margins=(14, 10, 12, 10), spacing=10)

        self.icon_label = QLabel(self)
        self.icon_label.setFixedSize(18, 18)
        layout.addWidget(self.icon_label, 0, Qt.AlignmentFlag.AlignVCenter)

        self.message_label = QLabel(message, self)
        self.message_label.setWordWrap(True)
        layout.addWidget(self.message_label, 1)

        self.action_button = QPushButton(action_text, self)
        self.action_button.setProperty("variant", "link")
        self.action_button.setVisible(bool(action_text))
        self.action_button.clicked.connect(self.action_triggered.emit)
        layout.addWidget(self.action_button, 0)

        self.close_button = QPushButton("✕", self)
        self.close_button.setProperty("variant", "ghost")
        self.close_button.setFixedWidth(26)
        self.close_button.setToolTip("Dismiss")
        self.close_button.setAccessibleName("Dismiss this message")
        self.close_button.setVisible(dismissible)
        self.close_button.clicked.connect(self._dismiss)
        layout.addWidget(self.close_button, 0)

        self.set_kind(kind)

    def _dismiss(self) -> None:
        self.hide()
        self.dismissed.emit()

    def set_kind(self, kind: StateKind) -> None:
        self._kind = kind
        set_property(self, "state", kind.value)
        provider = IconProvider.instance()
        colour = {
            StateKind.INFO: provider.theme.info,
            StateKind.SUCCESS: provider.theme.success,
            StateKind.WARNING: provider.theme.warning,
            StateKind.DANGER: provider.theme.danger,
        }[kind]
        self.icon_label.setPixmap(provider.pixmap(kind.icon_name, colour, 18))

    def show_message(
        self, message: str, kind: StateKind = StateKind.INFO, *, action_text: str = ""
    ) -> None:
        self.message_label.setText(message)
        self.action_button.setText(action_text)
        self.action_button.setVisible(bool(action_text))
        self.set_kind(kind)
        self.show()
