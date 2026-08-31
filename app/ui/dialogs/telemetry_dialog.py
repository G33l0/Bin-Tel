"""Shows exactly what telemetry is queued.

A privacy claim the user cannot inspect is worth very little, so the queue is
readable in full, verbatim, before anything is ever sent.
"""

from __future__ import annotations

import json

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFrame,
    QLabel,
    QPlainTextEdit,
    QWidget,
)

from app.telemetry.service import TelemetryService
from app.utils.formatting import format_datetime
from app.utils.qt_helpers import vbox


class TelemetryQueueDialog(QDialog):
    """The queued events and the local counters, rendered as text."""

    def __init__(self, telemetry: TelemetryService, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Bin-Tel — Queued telemetry")
        self.setModal(True)
        self.setMinimumSize(680, 520)
        self._telemetry = telemetry

        outer = vbox(self)
        body = QFrame(self)
        body.setObjectName("DialogBody")
        outer.addWidget(body)
        layout = vbox(body, margins=(22, 20, 22, 16), spacing=12)

        title = QLabel("Everything queued on this machine", body)
        title.setProperty("role", "sectionTitle")
        layout.addWidget(title)

        subtitle = QLabel(
            "This is the complete contents of the telemetry queue. Nothing is sent "
            "unless telemetry is enabled, and nothing outside this list is ever "
            "collected.",
            body,
        )
        subtitle.setProperty("role", "pageSubtitle")
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)

        self.view = QPlainTextEdit(body)
        self.view.setReadOnly(True)
        self.view.setAccessibleName("Queued telemetry events")
        self.view.setStyleSheet(
            "font-family: 'JetBrains Mono','SF Mono','Consolas','DejaVu Sans Mono',monospace;"
            " font-size: 9pt;"
        )
        self.view.setPlainText(self._render(telemetry))
        layout.addWidget(self.view, 1)

        buttons = QDialogButtonBox(body)
        close = buttons.addButton("Close", QDialogButtonBox.ButtonRole.RejectRole)
        close.clicked.connect(self.reject)
        clear = buttons.addButton("Delete everything", QDialogButtonBox.ButtonRole.DestructiveRole)
        clear.setProperty("variant", "danger")
        clear.clicked.connect(self._clear)
        layout.addWidget(buttons, 0, Qt.AlignmentFlag.AlignRight)

    @staticmethod
    def _render(telemetry: TelemetryService) -> str:
        lines: list[str] = []
        status = "enabled" if telemetry.enabled else "disabled (nothing will be sent)"
        lines.append(f"Telemetry is {status}.")
        lines.append(f"Installation identifier: {telemetry.install_id}")
        lines.append("")

        counters = telemetry.counters()
        lines.append("LOCAL USAGE COUNTERS")
        if counters:
            width = max(len(name) for name in counters) + 2
            lines.extend(
                f"  {name:<{width}}{value:,}" for name, value in sorted(counters.items())
            )
        else:
            lines.append("  (none recorded)")
        lines.append("")

        events = telemetry.queued_events(limit=200)
        lines.append(f"QUEUED EVENTS ({len(events)})")
        if not events:
            lines.append("  (the queue is empty)")
        for event in events:
            try:
                payload = json.loads(event.payload) if event.payload else {}
            except json.JSONDecodeError:  # pragma: no cover - defensive
                payload = {}
            lines.append(f"  {format_datetime(event.created_at)}  {event.name}")
            lines.append(
                f"      app {event.app_version} · database {event.database_version or 'none'}"
                f" · plan {event.plan} · {event.platform}"
            )
            if payload:
                for key, value in sorted(payload.items()):
                    lines.append(f"      {key} = {value}")
        return "\n".join(lines) + "\n"

    def _clear(self) -> None:
        self._telemetry.clear_all()
        self.view.setPlainText(self._render(self._telemetry))

    @classmethod
    def show_queue(cls, parent: QWidget | None, telemetry: TelemetryService) -> None:
        dialog = cls(telemetry, parent)
        dialog.exec()
