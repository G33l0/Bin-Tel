"""Card surfaces: the building blocks of every page."""

from __future__ import annotations

from collections.abc import Callable

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QMouseEvent
from PyQt6.QtWidgets import (
    QFrame,
    QGraphicsDropShadowEffect,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from app.core.constants import UNKNOWN_DISPLAY
from app.ui.themes.icons import IconProvider, color, icon
from app.utils.formatting import display
from app.utils.qt_helpers import hbox, horizontal_rule, vbox


class Card(QFrame):
    """A bordered surface with a consistent inner layout."""

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        padding: int = 18,
        spacing: int = 12,
        object_name: str = "Card",
        shadow: bool = False,
    ) -> None:
        super().__init__(parent)
        self.setObjectName(object_name)
        self.body = vbox(self, margins=(padding, padding, padding, padding), spacing=spacing)
        if shadow:
            self.apply_shadow()

    def apply_shadow(self, blur: int = 22, alpha: int = 42) -> None:
        """A subtle elevation shadow — used sparingly, never on every card."""
        effect = QGraphicsDropShadowEffect(self)
        effect.setBlurRadius(blur)
        effect.setOffset(0, 3)
        effect.setColor(color("#000000", alpha))
        self.setGraphicsEffect(effect)

    def add(self, widget: QWidget, stretch: int = 0) -> QWidget:
        self.body.addWidget(widget, stretch)
        return widget

    def add_rule(self) -> None:
        self.body.addWidget(horizontal_rule(self))


class SectionHeader(QWidget):
    """A title, an optional subtitle, and an optional right-hand action."""

    def __init__(
        self,
        title: str,
        subtitle: str = "",
        parent: QWidget | None = None,
        *,
        action: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        row = hbox(self, spacing=12)

        column = vbox(spacing=2)
        self.title_label = QLabel(title, self)
        self.title_label.setProperty("role", "sectionTitle")
        column.addWidget(self.title_label)

        self.subtitle_label = QLabel(subtitle, self)
        self.subtitle_label.setProperty("role", "pageSubtitle")
        self.subtitle_label.setWordWrap(True)
        self.subtitle_label.setVisible(bool(subtitle))
        column.addWidget(self.subtitle_label)

        row.addLayout(column, 1)
        if action is not None:
            row.addWidget(action, 0, Qt.AlignmentFlag.AlignTop)

    def set_subtitle(self, text: str) -> None:
        self.subtitle_label.setText(text)
        self.subtitle_label.setVisible(bool(text))


class MetricCard(Card):
    """A single headline number with a label and optional trailing detail."""

    clicked = pyqtSignal()

    def __init__(
        self,
        label: str,
        value: str = "—",
        icon_name: str | None = None,
        parent: QWidget | None = None,
        *,
        detail: str = "",
        clickable: bool = False,
    ) -> None:
        super().__init__(parent, padding=16, spacing=6, object_name="MetricCard")
        self._clickable = clickable
        self._icon_name = icon_name
        # Expanding vertically makes every card in a grid row share the row's
        # height, so a card with a detail line does not sit taller than its
        # neighbours.
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.MinimumExpanding)
        if clickable:
            self.setCursor(Qt.CursorShape.PointingHandCursor)

        top = hbox(spacing=8)
        self.icon_label = QLabel(self)
        self.icon_label.setFixedSize(18, 18)
        self.icon_label.setVisible(bool(icon_name))
        top.addWidget(self.icon_label, 0, Qt.AlignmentFlag.AlignVCenter)

        self.label_widget = QLabel(label, self)
        self.label_widget.setProperty("role", "metricLabel")
        top.addWidget(self.label_widget, 1)
        self.body.addLayout(top)

        self.value_label = QLabel(value, self)
        self.value_label.setProperty("role", "metricValue")
        self.value_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.body.addWidget(self.value_label)

        self.detail_label = QLabel(detail, self)
        self.detail_label.setProperty("role", "muted")
        self.detail_label.setWordWrap(True)
        self.detail_label.setVisible(bool(detail))
        self.body.addWidget(self.detail_label)
        self.body.addStretch(1)

        self.setAccessibleName(f"{label}: {value}")
        self.refresh_icon()

    def refresh_icon(self) -> None:
        if not self._icon_name:
            return
        provider = IconProvider.instance()
        self.icon_label.setPixmap(provider.pixmap(self._icon_name, provider.theme.primary, 18))

    def set_value(self, value: str, detail: str = "") -> None:
        self.value_label.setText(value or UNKNOWN_DISPLAY)
        self.detail_label.setText(detail)
        self.detail_label.setVisible(bool(detail))
        self.setAccessibleName(f"{self.label_widget.text()}: {self.value_label.text()}")

    def mouseReleaseEvent(self, event: QMouseEvent | None) -> None:
        if self._clickable and event is not None and event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mouseReleaseEvent(event)


class FieldRow(QWidget):
    """A labelled value, the unit the BIN result card is built from."""

    def __init__(
        self,
        label: str,
        value: str = UNKNOWN_DISPLAY,
        parent: QWidget | None = None,
        *,
        mono: bool = False,
        selectable: bool = True,
    ) -> None:
        super().__init__(parent)
        layout = vbox(self, spacing=3)

        self.label_widget = QLabel(label.upper(), self)
        self.label_widget.setProperty("role", "fieldLabel")
        layout.addWidget(self.label_widget)

        self.value_widget = QLabel(display(value), self)
        self.value_widget.setProperty("role", "mono" if mono else "fieldValue")
        self.value_widget.setWordWrap(True)
        if selectable:
            self.value_widget.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextSelectableByMouse
            )
        layout.addWidget(self.value_widget)

        self.setAccessibleName(f"{label}: {self.value_widget.text()}")

    @property
    def value(self) -> str:
        return self.value_widget.text()

    def set_value(self, value: str) -> None:
        self.value_widget.setText(display(value))
        self.setAccessibleName(f"{self.label_widget.text()}: {self.value_widget.text()}")

    def is_unknown(self) -> bool:
        return self.value_widget.text() == UNKNOWN_DISPLAY


class Chip(QFrame):
    """A small pill for a network, status or count."""

    def __init__(self, text: str, parent: QWidget | None = None, *, accent: str | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Chip")
        layout = hbox(self, margins=(10, 3, 10, 3), spacing=6)
        self.text_label = QLabel(text, self)
        self.text_label.setObjectName("ChipText")
        if accent:
            self.text_label.setStyleSheet(f"color: {accent};")
        layout.addWidget(self.text_label)
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)

    def set_text(self, text: str) -> None:
        self.text_label.setText(text)


class IconButton(QPushButton):
    """A compact, icon-only button with a mandatory accessible label."""

    def __init__(
        self,
        icon_name: str,
        tooltip: str,
        parent: QWidget | None = None,
        *,
        size: int = 18,
        checkable: bool = False,
        on_click: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(parent)
        self._icon_name = icon_name
        self._icon_size = size
        self.setProperty("variant", "ghost")
        self.setToolTip(tooltip)
        self.setAccessibleName(tooltip)
        self.setCheckable(checkable)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(size + 14)
        self.setMinimumWidth(size + 14)
        if on_click is not None:
            self.clicked.connect(on_click)
        self.refresh_icon()

    def refresh_icon(self, accent: bool = False) -> None:
        provider = IconProvider.instance()
        colour = provider.theme.primary if accent else provider.theme.text_secondary
        self.setIcon(icon(self._icon_name, colour, self._icon_size))
        self.setIconSize(self.iconSize().expandedTo(self.iconSize()))


class CardGrid(QWidget):
    """Responsive grid of cards that reflows as the window is resized."""

    def __init__(self, parent: QWidget | None = None, *, minimum_width: int = 210, spacing: int = 14) -> None:
        super().__init__(parent)
        self._minimum_width = minimum_width
        self._cards: list[QWidget] = []
        self._columns = 0
        from app.utils.qt_helpers import grid

        self._layout = grid(self, spacing=spacing)

    def add_card(self, card: QWidget) -> QWidget:
        self._cards.append(card)
        self._relayout(force=True)
        return card

    def clear(self) -> None:
        for card in self._cards:
            self._layout.removeWidget(card)
            card.setParent(None)
            card.deleteLater()
        self._cards.clear()
        self._columns = 0

    def resizeEvent(self, event: object) -> None:
        super().resizeEvent(event)  # type: ignore[arg-type]
        self._relayout()

    def _relayout(self, *, force: bool = False) -> None:
        if not self._cards:
            return
        available = max(self._minimum_width, self.width())
        columns = max(1, min(len(self._cards), available // self._minimum_width))
        if columns == self._columns and not force:
            return
        self._columns = columns
        while self._layout.count():
            self._layout.takeAt(0)
        for index, card in enumerate(self._cards):
            self._layout.addWidget(card, index // columns, index % columns)
        for column in range(columns):
            self._layout.setColumnStretch(column, 1)


class KeyValueCard(Card):
    """A card of ``FieldRow`` entries in a responsive column layout."""

    def __init__(
        self,
        title: str = "",
        parent: QWidget | None = None,
        *,
        columns: int = 2,
    ) -> None:
        super().__init__(parent, padding=18, spacing=14)
        self._columns = max(1, columns)
        self._rows: dict[str, FieldRow] = {}

        if title:
            self.body.addWidget(SectionHeader(title, parent=self))

        from app.utils.qt_helpers import grid

        self._grid_container = QWidget(self)
        self._grid = grid(self._grid_container, spacing=14)
        self.body.addWidget(self._grid_container)

    def set_fields(self, pairs: list[tuple[str, str]], *, hide_unknown: bool = True) -> None:
        """Replace the displayed fields.

        Fields with no data are omitted rather than shown as empty rows; the
        BIN card keeps a small set of always-visible fields regardless.
        """
        while self._grid.count():
            item = self._grid.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        self._rows.clear()

        visible = [
            (label, value)
            for label, value in pairs
            if not hide_unknown or value != UNKNOWN_DISPLAY
        ]
        for index, (label, value) in enumerate(visible):
            row = FieldRow(label, value, self._grid_container, mono=label in ("BIN", "IIN"))
            self._rows[label] = row
            self._grid.addWidget(row, index // self._columns, index % self._columns)
        for column in range(self._columns):
            self._grid.setColumnStretch(column, 1)

    def value_of(self, label: str) -> str | None:
        row = self._rows.get(label)
        return row.value if row else None

    @property
    def field_count(self) -> int:
        return len(self._rows)


class TitledCard(Card):
    """A card with a header row and a caller-supplied body layout."""

    def __init__(
        self,
        title: str,
        subtitle: str = "",
        parent: QWidget | None = None,
        *,
        action: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.header = SectionHeader(title, subtitle, self, action=action)
        self.body.addWidget(self.header)
        self.content = QVBoxLayout()
        self.content.setSpacing(10)
        self.body.addLayout(self.content)
