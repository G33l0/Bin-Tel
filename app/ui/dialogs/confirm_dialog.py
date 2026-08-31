"""A themed confirmation dialog used before anything destructive."""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QCheckBox, QDialog, QDialogButtonBox, QFrame, QLabel, QWidget

from app.ui.themes.icons import IconProvider
from app.utils.qt_helpers import hbox, vbox


class ConfirmDialog(QDialog):
    """``Are you sure?`` with an explicit, named confirm action."""

    def __init__(
        self,
        title: str,
        message: str,
        parent: QWidget | None = None,
        *,
        confirm_text: str = "Continue",
        cancel_text: str = "Cancel",
        destructive: bool = False,
        checkbox_text: str = "",
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Bin-Tel")
        self.setModal(True)
        self.setMinimumWidth(440)

        outer = vbox(self)
        body = QFrame(self)
        body.setObjectName("DialogBody")
        outer.addWidget(body)
        layout = vbox(body, margins=(24, 24, 24, 20), spacing=14)

        header = hbox(spacing=12)
        provider = IconProvider.instance()
        icon_label = QLabel(body)
        icon_label.setFixedSize(26, 26)
        icon_label.setPixmap(
            provider.pixmap(
                "warning" if destructive else "about",
                provider.theme.warning if destructive else provider.theme.info,
                26,
            )
        )
        header.addWidget(icon_label, 0, Qt.AlignmentFlag.AlignTop)

        column = vbox(spacing=6)
        title_label = QLabel(title, body)
        title_label.setProperty("role", "sectionTitle")
        title_label.setWordWrap(True)
        column.addWidget(title_label)

        message_label = QLabel(message, body)
        message_label.setProperty("role", "pageSubtitle")
        message_label.setWordWrap(True)
        column.addWidget(message_label)
        header.addLayout(column, 1)
        layout.addLayout(header)

        self.checkbox = QCheckBox(checkbox_text, body)
        self.checkbox.setVisible(bool(checkbox_text))
        layout.addWidget(self.checkbox)

        buttons = QDialogButtonBox(body)
        cancel = buttons.addButton(cancel_text, QDialogButtonBox.ButtonRole.RejectRole)
        cancel.clicked.connect(self.reject)
        confirm = buttons.addButton(confirm_text, QDialogButtonBox.ButtonRole.AcceptRole)
        confirm.setProperty("variant", "danger" if destructive else "primary")
        confirm.setDefault(not destructive)
        confirm.clicked.connect(self.accept)
        layout.addWidget(buttons)

    @property
    def checkbox_checked(self) -> bool:
        return self.checkbox.isChecked()

    @classmethod
    def ask(
        cls,
        parent: QWidget | None,
        title: str,
        message: str,
        *,
        confirm_text: str = "Continue",
        destructive: bool = False,
    ) -> bool:
        dialog = cls(
            title, message, parent, confirm_text=confirm_text, destructive=destructive
        )
        return dialog.exec() == QDialog.DialogCode.Accepted
