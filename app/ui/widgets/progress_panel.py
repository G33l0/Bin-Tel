"""Download/installation progress display shared by first-run and Updates."""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QLabel, QProgressBar, QWidget

from app.services.update_service import UpdateProgress, UpdateState
from app.utils.formatting import format_bytes, format_duration, format_speed
from app.utils.qt_helpers import expanding_spacer, hbox, vbox


class ProgressPanel(QWidget):
    """Stage, percentage, transferred bytes, speed and estimated time left."""

    def __init__(self, parent: QWidget | None = None, *, tall: bool = False) -> None:
        super().__init__(parent)
        layout = vbox(self, spacing=8)

        top = hbox(spacing=10)
        self.stage_label = QLabel("Ready", self)
        self.stage_label.setProperty("role", "fieldValue")
        top.addWidget(self.stage_label, 1)

        self.percent_label = QLabel("", self)
        self.percent_label.setProperty("role", "muted")
        self.percent_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        top.addWidget(self.percent_label)
        layout.addLayout(top)

        self.bar = QProgressBar(self)
        self.bar.setTextVisible(False)
        self.bar.setRange(0, 100)
        self.bar.setValue(0)
        self.bar.setAccessibleName("Progress")
        if tall:
            self.bar.setProperty("variant", "tall")
        layout.addWidget(self.bar)

        details = hbox(spacing=16)
        self.transferred_label = QLabel("", self)
        self.transferred_label.setProperty("role", "muted")
        details.addWidget(self.transferred_label)

        self.speed_label = QLabel("", self)
        self.speed_label.setProperty("role", "muted")
        details.addWidget(self.speed_label)

        details.addItem(expanding_spacer())

        self.eta_label = QLabel("", self)
        self.eta_label.setProperty("role", "muted")
        details.addWidget(self.eta_label)
        layout.addLayout(details)

        self.reset()

    # -- state ------------------------------------------------------------
    def reset(self, message: str = "Ready") -> None:
        self.stage_label.setText(message)
        self.percent_label.setText("")
        self.bar.setRange(0, 100)
        self.bar.setValue(0)
        self.transferred_label.setText("")
        self.speed_label.setText("")
        self.eta_label.setText("")

    def set_indeterminate(self, message: str) -> None:
        self.stage_label.setText(message)
        self.percent_label.setText("")
        self.bar.setRange(0, 0)
        self.transferred_label.setText("")
        self.speed_label.setText("")
        self.eta_label.setText("")

    def update_progress(self, progress: UpdateProgress) -> None:
        self.stage_label.setText(progress.message or progress.state.label)

        if progress.indeterminate:
            self.bar.setRange(0, 0)
            self.percent_label.setText("")
        else:
            self.bar.setRange(0, 100)
            self.bar.setValue(progress.percent)
            self.percent_label.setText(f"{progress.percent}%")

        if progress.state is UpdateState.DOWNLOADING and progress.total:
            self.transferred_label.setText(
                f"{format_bytes(progress.received)} of {format_bytes(progress.total)}"
            )
            self.speed_label.setText(format_speed(progress.speed))
            self.eta_label.setText(
                f"{format_duration(progress.eta_seconds)} remaining"
                if progress.eta_seconds is not None
                else ""
            )
        elif progress.state is UpdateState.VERIFYING and progress.total:
            self.transferred_label.setText(
                f"{format_bytes(progress.received)} of {format_bytes(progress.total)} checked"
            )
            self.speed_label.setText("")
            self.eta_label.setText("")
        else:
            self.transferred_label.setText("")
            self.speed_label.setText("")
            self.eta_label.setText("")

        if progress.state is UpdateState.COMPLETE:
            self.bar.setRange(0, 100)
            self.bar.setValue(100)
            self.percent_label.setText("100%")

    def set_error(self, message: str) -> None:
        self.stage_label.setText(message)
        self.bar.setRange(0, 100)
        self.bar.setValue(0)
        self.percent_label.setText("")
        self.transferred_label.setText("")
        self.speed_label.setText("")
        self.eta_label.setText("")
