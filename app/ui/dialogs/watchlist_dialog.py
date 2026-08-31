"""Dialogs for creating watchlists and adding targets to them."""

from __future__ import annotations

from collections.abc import Callable

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QWidget,
)

from app.core.errors import ValidationError
from app.models.user_entities import WatchTargetType
from app.utils.qt_helpers import vbox


class CreateWatchlistDialog(QDialog):
    """Name and describe a new watchlist."""

    def __init__(self, parent: QWidget | None = None, *, name: str = "", description: str = "") -> None:
        super().__init__(parent)
        self.setWindowTitle("Bin-Tel — Watchlist")
        self.setModal(True)
        self.setMinimumWidth(460)

        outer = vbox(self)
        body = QFrame(self)
        body.setObjectName("DialogBody")
        outer.addWidget(body)
        layout = vbox(body, margins=(24, 22, 24, 18), spacing=12)

        title = QLabel("Rename watchlist" if name else "New watchlist", body)
        title.setProperty("role", "sectionTitle")
        layout.addWidget(title)

        subtitle = QLabel(
            "Bin-Tel compares everything on a watchlist against each new database "
            "release and tells you what changed.",
            body,
        )
        subtitle.setProperty("role", "pageSubtitle")
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)

        name_label = QLabel("Name", body)
        name_label.setProperty("role", "fieldLabel")
        layout.addWidget(name_label)
        self.name_field = QLineEdit(name, body)
        self.name_field.setPlaceholderText("US banking")
        self.name_field.setAccessibleName("Watchlist name")
        layout.addWidget(self.name_field)

        description_label = QLabel("Description (optional)", body)
        description_label.setProperty("role", "fieldLabel")
        layout.addWidget(description_label)
        self.description_field = QPlainTextEdit(description, body)
        self.description_field.setAccessibleName("Watchlist description")
        self.description_field.setMaximumHeight(70)
        layout.addWidget(self.description_field)

        buttons = QDialogButtonBox(body)
        cancel = buttons.addButton("Cancel", QDialogButtonBox.ButtonRole.RejectRole)
        cancel.clicked.connect(self.reject)
        confirm = buttons.addButton(
            "Save" if name else "Create watchlist", QDialogButtonBox.ButtonRole.AcceptRole
        )
        confirm.setProperty("variant", "primary")
        confirm.setDefault(True)
        confirm.clicked.connect(self.accept)
        layout.addWidget(buttons, 0, Qt.AlignmentFlag.AlignRight)

    @property
    def watchlist_name(self) -> str:
        return self.name_field.text().strip()

    @property
    def description(self) -> str:
        return self.description_field.toPlainText().strip()

    @classmethod
    def ask(cls, parent: QWidget | None) -> tuple[str, str] | None:
        dialog = cls(parent)
        if dialog.exec() != QDialog.DialogCode.Accepted or not dialog.watchlist_name:
            return None
        return dialog.watchlist_name, dialog.description


class AddToWatchlistDialog(QDialog):
    """Choose which watchlist a target joins, creating one if there are none."""

    def __init__(
        self,
        context,
        target_type: WatchTargetType,
        target_value: str,
        label: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.context = context
        self.target_type = target_type
        self.target_value = target_value
        self.label = label

        self.setWindowTitle("Bin-Tel — Add to watchlist")
        self.setModal(True)
        self.setMinimumWidth(460)

        outer = vbox(self)
        body = QFrame(self)
        body.setObjectName("DialogBody")
        outer.addWidget(body)
        layout = vbox(body, margins=(24, 22, 24, 18), spacing=12)

        title = QLabel("Add to watchlist", body)
        title.setProperty("role", "sectionTitle")
        layout.addWidget(title)

        target = QLabel(f"{target_type.label}: {label or target_value}", body)
        target.setProperty("role", "pageSubtitle")
        target.setWordWrap(True)
        layout.addWidget(target)

        chooser_label = QLabel("Watchlist", body)
        chooser_label.setProperty("role", "fieldLabel")
        layout.addWidget(chooser_label)

        self.chooser = QComboBox(body)
        self.chooser.setAccessibleName("Choose a watchlist")
        for watchlist in context.watchlists.list():
            self.chooser.addItem(f"{watchlist.name}  ({watchlist.item_count} items)", watchlist.id)
        self.chooser.addItem("Create a new watchlist…", -1)
        layout.addWidget(self.chooser)

        self.new_name = QLineEdit(body)
        self.new_name.setPlaceholderText("New watchlist name")
        self.new_name.setAccessibleName("New watchlist name")
        self.new_name.setVisible(self.chooser.currentData() == -1)
        self.chooser.currentIndexChanged.connect(
            lambda: self.new_name.setVisible(self.chooser.currentData() == -1)
        )
        layout.addWidget(self.new_name)

        self.error_label = QLabel("", body)
        self.error_label.setProperty("state", "danger")
        self.error_label.setWordWrap(True)
        self.error_label.setVisible(False)
        layout.addWidget(self.error_label)

        buttons = QDialogButtonBox(body)
        cancel = buttons.addButton("Cancel", QDialogButtonBox.ButtonRole.RejectRole)
        cancel.clicked.connect(self.reject)
        confirm = buttons.addButton("Add", QDialogButtonBox.ButtonRole.AcceptRole)
        confirm.setProperty("variant", "primary")
        confirm.setDefault(True)
        confirm.clicked.connect(self._confirm)
        layout.addWidget(buttons, 0, Qt.AlignmentFlag.AlignRight)

        self.added_to = ""

    def _confirm(self) -> None:
        try:
            watchlist_id = self.chooser.currentData()
            if watchlist_id == -1:
                name = self.new_name.text().strip()
                if not name:
                    raise ValidationError("Give the new watchlist a name.")
                summary = self.context.watchlists.create(name)
                watchlist_id = summary.id
                self.added_to = summary.name
            else:
                self.added_to = self.chooser.currentText().split("  (")[0]

            self.context.watchlists.add_item(
                int(watchlist_id),
                self.target_type,
                self.target_value,
                self.label,
                database_version=self.context.database_version(),
            )
        except ValidationError as exc:
            self.error_label.setText(exc.message)
            self.error_label.setVisible(True)
            return
        self.accept()

    @classmethod
    def add(
        cls,
        parent: QWidget | None,
        context,
        target_type: WatchTargetType,
        target_value: str,
        label: str = "",
        *,
        on_added: Callable[[str], None] | None = None,
    ) -> bool:
        """Run the dialog. Returns whether the target was added."""
        dialog = cls(context, target_type, target_value, label, parent)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return False
        if on_added is not None:
            on_added(dialog.added_to)
        return True
