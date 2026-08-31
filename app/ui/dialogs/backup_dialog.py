"""Browse and restore database backups."""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFrame,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QWidget,
)

from app.database.backup import BackupInfo
from app.utils.formatting import format_bytes, format_datetime_with_relative
from app.utils.qt_helpers import vbox


class BackupDialog(QDialog):
    """Pick a snapshot to restore. Restoring is confirmed separately."""

    def __init__(self, backups: list[BackupInfo], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Bin-Tel — Database backups")
        self.setModal(True)
        self.setMinimumSize(520, 360)
        self._backups = backups

        outer = vbox(self)
        body = QFrame(self)
        body.setObjectName("DialogBody")
        outer.addWidget(body)
        layout = vbox(body, margins=(22, 22, 22, 18), spacing=12)

        title = QLabel("Restore a database backup", body)
        title.setProperty("role", "sectionTitle")
        layout.addWidget(title)

        subtitle = QLabel(
            "Bin-Tel verifies a backup before it replaces your working database, so a "
            "damaged snapshot can never take out a healthy installation.",
            body,
        )
        subtitle.setProperty("role", "pageSubtitle")
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)

        self.list_widget = QListWidget(body)
        self.list_widget.setAccessibleName("Available backups")
        self.list_widget.setAlternatingRowColors(True)
        for backup in backups:
            label = (
                f"{format_datetime_with_relative(backup.created_at)}"
                f"   ·   {format_bytes(backup.size_bytes)}"
                + (f"   ·   v{backup.version}" if backup.version else "")
            )
            item = QListWidgetItem(label, self.list_widget)
            item.setData(Qt.ItemDataRole.UserRole, str(backup.path))
            item.setToolTip(backup.name)
        if backups:
            self.list_widget.setCurrentRow(0)
        layout.addWidget(self.list_widget, 1)

        if not backups:
            empty = QLabel("There are no backups yet.", body)
            empty.setProperty("role", "muted")
            layout.addWidget(empty)

        buttons = QDialogButtonBox(body)
        cancel = buttons.addButton("Close", QDialogButtonBox.ButtonRole.RejectRole)
        cancel.clicked.connect(self.reject)
        self.restore_button = buttons.addButton(
            "Restore selected", QDialogButtonBox.ButtonRole.AcceptRole
        )
        self.restore_button.setProperty("variant", "primary")
        self.restore_button.setEnabled(bool(backups))
        self.restore_button.clicked.connect(self.accept)
        layout.addWidget(buttons)

    @property
    def selected_backup(self) -> Path | None:
        item = self.list_widget.currentItem()
        if item is None:
            return None
        return Path(str(item.data(Qt.ItemDataRole.UserRole)))

    @classmethod
    def choose(cls, parent: QWidget | None, backups: list[BackupInfo]) -> Path | None:
        dialog = cls(backups, parent)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None
        return dialog.selected_backup
