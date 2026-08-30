"""Format picker and save flow for exporting results."""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QButtonGroup,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QLabel,
    QRadioButton,
    QWidget,
)

from app.core.paths import get_paths
from app.services.export_service import ExportFormat
from app.utils.qt_helpers import vbox


class ExportDialog(QDialog):
    """Choose JSON, CSV or plain text, then pick a destination."""

    _CHOICES: tuple[tuple[ExportFormat, str, str], ...] = (
        (ExportFormat.JSON, "JSON", "Structured, ideal for feeding another system."),
        (ExportFormat.CSV, "CSV", "Opens directly in a spreadsheet."),
        (ExportFormat.TXT, "Plain text", "Human-readable summary for a report or ticket."),
    )

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        title: str = "Export result",
        subtitle: str = "",
        default: ExportFormat = ExportFormat.JSON,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Bin-Tel — Export")
        self.setModal(True)
        self.setMinimumWidth(420)

        outer = vbox(self)
        body = QFrame(self)
        body.setObjectName("DialogBody")
        outer.addWidget(body)
        layout = vbox(body, margins=(24, 24, 24, 20), spacing=14)

        title_label = QLabel(title, body)
        title_label.setProperty("role", "sectionTitle")
        layout.addWidget(title_label)

        if subtitle:
            subtitle_label = QLabel(subtitle, body)
            subtitle_label.setProperty("role", "pageSubtitle")
            subtitle_label.setWordWrap(True)
            layout.addWidget(subtitle_label)

        note = QLabel(
            "Exports contain BIN and issuer metadata only.",
            body,
        )
        note.setProperty("role", "muted")
        note.setWordWrap(True)
        layout.addWidget(note)

        self._group = QButtonGroup(self)
        self._buttons: dict[ExportFormat, QRadioButton] = {}
        for fmt, label, description in self._CHOICES:
            option = QRadioButton(f"{label} — {description}", body)
            option.setAccessibleName(f"Export as {label}")
            option.setChecked(fmt is default)
            self._group.addButton(option)
            self._buttons[fmt] = option
            layout.addWidget(option)

        buttons = QDialogButtonBox(body)
        cancel = buttons.addButton("Cancel", QDialogButtonBox.ButtonRole.RejectRole)
        cancel.clicked.connect(self.reject)
        save = buttons.addButton("Choose location…", QDialogButtonBox.ButtonRole.AcceptRole)
        save.setProperty("variant", "primary")
        save.setDefault(True)
        save.clicked.connect(self.accept)
        layout.addWidget(buttons, 0, Qt.AlignmentFlag.AlignRight)

    @property
    def selected_format(self) -> ExportFormat:
        for fmt, button in self._buttons.items():
            if button.isChecked():
                return fmt
        return ExportFormat.JSON

    @classmethod
    def choose(
        cls,
        parent: QWidget | None,
        stem: str,
        *,
        title: str = "Export result",
        subtitle: str = "",
    ) -> tuple[Path, ExportFormat] | None:
        """Run the format picker followed by a save dialog."""
        from PyQt6.QtWidgets import QFileDialog

        from app.services.export_service import ExportService

        dialog = cls(parent, title=title, subtitle=subtitle)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None
        fmt = dialog.selected_format
        suggested = get_paths().exports_dir / ExportService.suggested_filename(stem, fmt)
        path, _ = QFileDialog.getSaveFileName(
            parent, "Export", str(suggested), fmt.label
        )
        if not path:
            return None
        destination = Path(path)
        if not destination.suffix:
            destination = destination.with_suffix(fmt.extension)
        return destination, fmt
