"""The application error dialog.

Normal users see a headline, a plain explanation and their options. Technical
detail is available behind a disclosure and is written to the log — a raw
traceback is never rendered as the primary message.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFrame,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QWidget,
)

from app.core.errors import friendly_message, friendly_title, is_retryable
from app.core.logging_config import get_logger
from app.ui.themes.icons import IconProvider
from app.utils.qt_helpers import copy_to_clipboard, hbox, vbox

logger = get_logger(__name__)


class ErrorDialog(QDialog):
    """``Operation failed`` → explanation → ``Retry`` / ``Close``."""

    def __init__(
        self,
        title: str,
        message: str,
        parent: QWidget | None = None,
        *,
        detail: str = "",
        retryable: bool = False,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Bin-Tel")
        self.setModal(True)
        self.setMinimumWidth(480)
        self._retry = False

        outer = vbox(self, margins=(0, 0, 0, 0))
        body = QFrame(self)
        body.setObjectName("DialogBody")
        outer.addWidget(body)

        layout = vbox(body, margins=(24, 24, 24, 20), spacing=14)

        header = hbox(spacing=12)
        provider = IconProvider.instance()
        icon_label = QLabel(body)
        icon_label.setFixedSize(28, 28)
        icon_label.setPixmap(provider.pixmap("warning", provider.theme.danger, 28))
        header.addWidget(icon_label, 0, Qt.AlignmentFlag.AlignTop)

        text_column = vbox(spacing=6)
        title_label = QLabel(title, body)
        title_label.setProperty("role", "sectionTitle")
        title_label.setWordWrap(True)
        text_column.addWidget(title_label)

        message_label = QLabel(message, body)
        message_label.setProperty("role", "pageSubtitle")
        message_label.setWordWrap(True)
        message_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        text_column.addWidget(message_label)
        header.addLayout(text_column, 1)
        layout.addLayout(header)

        self.detail_view = QPlainTextEdit(detail, body)
        self.detail_view.setReadOnly(True)
        self.detail_view.setMaximumHeight(140)
        self.detail_view.setVisible(False)
        self.detail_view.setAccessibleName("Technical detail")
        layout.addWidget(self.detail_view)

        footer = hbox(spacing=8)
        self.detail_button = QPushButton("Show technical detail", body)
        self.detail_button.setProperty("variant", "link")
        self.detail_button.setVisible(bool(detail))
        self.detail_button.clicked.connect(self._toggle_detail)
        footer.addWidget(self.detail_button)

        self.copy_button = QPushButton("Copy detail", body)
        self.copy_button.setProperty("variant", "link")
        self.copy_button.setVisible(False)
        self.copy_button.clicked.connect(lambda: copy_to_clipboard(detail))
        footer.addWidget(self.copy_button)
        footer.addStretch(1)

        buttons = QDialogButtonBox(body)
        close_button = buttons.addButton("Close", QDialogButtonBox.ButtonRole.RejectRole)
        close_button.clicked.connect(self.reject)
        if retryable:
            retry_button = buttons.addButton("Retry", QDialogButtonBox.ButtonRole.AcceptRole)
            retry_button.setProperty("variant", "primary")
            retry_button.setDefault(True)
            retry_button.clicked.connect(self._on_retry)
        footer.addWidget(buttons)
        layout.addLayout(footer)

    def _toggle_detail(self) -> None:
        visible = not self.detail_view.isVisible()
        self.detail_view.setVisible(visible)
        self.copy_button.setVisible(visible)
        self.detail_button.setText("Hide technical detail" if visible else "Show technical detail")
        self.adjustSize()

    def _on_retry(self) -> None:
        self._retry = True
        self.accept()

    @property
    def retry_requested(self) -> bool:
        return self._retry

    # -- convenience -------------------------------------------------------
    @classmethod
    def from_exception(cls, exc: BaseException, parent: QWidget | None = None) -> ErrorDialog:
        """Build a dialog from any exception, without leaking a traceback."""
        from app.core.errors import BinTelError

        detail = exc.detail or "" if isinstance(exc, BinTelError) else f"{type(exc).__name__}: {exc}"
        logger.error(
            "Presenting error to the user",
            extra={"context": {"type": type(exc).__name__, "detail": detail}},
        )
        return cls(
            friendly_title(exc),
            friendly_message(exc),
            parent,
            detail=detail,
            retryable=is_retryable(exc),
        )

    @classmethod
    def show_for(cls, exc: BaseException, parent: QWidget | None = None) -> bool:
        """Show the dialog; returns True when the user asked to retry."""
        dialog = cls.from_exception(exc, parent)
        dialog.exec()
        return dialog.retry_requested
