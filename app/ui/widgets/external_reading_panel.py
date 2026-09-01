"""A second opinion from outside, presented as exactly that.

The design problem this widget exists to solve: an external answer must be
easy to read and impossible to mistake for Bin-Tel's own data. So it lives in
its own card, is labelled with the service that produced it, states plainly
that nothing has been saved, and offers its content as a *row you could paste*
rather than as something already applied.

Where the reading disagrees with what Bin-Tel holds, the disagreement is
listed. It is never resolved here — your list stays the authority, and a
disagreement is a prompt to go and check.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QLabel, QPushButton, QWidget

from app.core.constants import UNKNOWN_DISPLAY
from app.providers.binlist import ExternalReading
from app.ui.themes.icons import IconProvider
from app.ui.widgets.cards import Card, SectionHeader
from app.ui.widgets.states import StateBanner, StateKind
from app.utils.qt_helpers import expanding_spacer, hbox, vbox


class ExternalReadingPanel(QWidget):
    """Shows one external reading, and what it disagrees with."""

    copy_requested = pyqtSignal(str)
    dismissed = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._reading: ExternalReading | None = None

        layout = vbox(self, spacing=0)
        self.card = Card(self, padding=18, spacing=12)
        self.header = SectionHeader(
            "Second opinion — binlist.net",
            "An external reading. Nothing here has been saved to your database.",
            parent=self.card,
        )
        self.card.body.addWidget(self.header)

        self.banner = StateBanner("", StateKind.INFO, self.card, dismissible=False)
        self.banner.hide()
        self.card.body.addWidget(self.banner)

        self.fields_label = QLabel("", self.card)
        self.fields_label.setWordWrap(True)
        self.fields_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self.card.body.addWidget(self.fields_label)

        self.differences_label = QLabel("", self.card)
        self.differences_label.setWordWrap(True)
        self.differences_label.hide()
        self.card.body.addWidget(self.differences_label)

        self.row_label = QLabel("", self.card)
        self.row_label.setProperty("role", "mono")
        self.row_label.setStyleSheet("font-size: 10pt;")
        self.row_label.setWordWrap(True)
        self.row_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self.row_label.hide()
        self.card.body.addWidget(self.row_label)

        actions = hbox(spacing=10)
        self.copy_button = QPushButton("Copy as a BIN list row", self.card)
        self.copy_button.setProperty("variant", "primary")
        self.copy_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.copy_button.clicked.connect(self._copy)
        actions.addWidget(self.copy_button)

        self.close_button = QPushButton("Dismiss", self.card)
        self.close_button.setProperty("variant", "ghost")
        self.close_button.clicked.connect(self._dismiss)
        actions.addWidget(self.close_button)

        self.allowance_label = QLabel("", self.card)
        self.allowance_label.setProperty("role", "muted")
        actions.addWidget(self.allowance_label)
        actions.addItem(expanding_spacer())
        self.card.body.addLayout(actions)

        layout.addWidget(self.card)
        self.hide()

    # -- content ------------------------------------------------------------
    def show_reading(
        self,
        reading: ExternalReading,
        *,
        differences: list[str] | None = None,
        allowance: str = "",
    ) -> None:
        """Present a reading, with any disagreements listed beneath it."""
        self._reading = reading
        provider = IconProvider.instance()

        pairs = [
            ("Bank", reading.bank_name),
            ("Scheme", reading.scheme),
            ("Card type", reading.card_type),
            ("Brand", reading.brand),
            ("Country", reading.country_alpha2),
            ("Currency", reading.country_currency),
            ("City", reading.bank_city),
            ("Website", reading.bank_url),
            ("Phone", reading.bank_phone),
        ]
        shown = [(label, value) for label, value in pairs if (value or "").strip()]
        if shown:
            width = max(len(label) for label, _ in shown) + 2
            self.fields_label.setText(
                "\n".join(f"{label + ':':<{width}}{value}" for label, value in shown)
            )
        else:
            self.fields_label.setText(
                f"binlist.net returned a record for {reading.query} but every field "
                "in it was empty."
            )

        if differences:
            self.differences_label.setText(
                "This disagrees with what you hold:\n"
                + "\n".join(f"  •  {line}" for line in differences)
                + "\nYour list is unchanged. Neither reading has been discarded."
            )
            self.differences_label.setStyleSheet(f"color: {provider.theme.warning};")
            self.differences_label.show()
        else:
            self.differences_label.hide()

        self.row_label.setText(reading.as_list_row())
        self.row_label.setVisible(reading.has_content)
        self.copy_button.setVisible(reading.has_content)
        self.allowance_label.setText(allowance)
        self.banner.hide()
        self.show()

    def show_message(self, message: str, kind: StateKind = StateKind.INFO) -> None:
        """Say why there is no reading — not found, rate limited, offline."""
        self._reading = None
        self.fields_label.setText("")
        self.differences_label.hide()
        self.row_label.hide()
        self.copy_button.hide()
        self.allowance_label.setText("")
        self.banner.set_kind(kind)
        self.banner.message_label.setText(message)
        self.banner.show()
        self.show()

    def clear(self) -> None:
        self._reading = None
        self.hide()

    # -- actions ------------------------------------------------------------
    def _copy(self) -> None:
        if self._reading is not None:
            self.copy_requested.emit(self._reading.as_list_row())

    def _dismiss(self) -> None:
        self.clear()
        self.dismissed.emit()

    def refresh_theme(self) -> None:
        if self.differences_label.isVisible():
            self.differences_label.setStyleSheet(
                f"color: {IconProvider.instance().theme.warning};"
            )


__all__ = ["ExternalReadingPanel"]
