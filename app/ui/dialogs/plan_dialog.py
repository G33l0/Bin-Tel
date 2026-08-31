"""The plan comparison dialog.

A plain table of what each plan includes. No countdown, no pre-selected
upsell, no dark patterns — the current plan is marked, the differences are
visible, and the free column is a real column.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFrame,
    QHeaderView,
    QLabel,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QWidget,
)

from app.core.constants import WEBSITE_URL
from app.licensing.plans import Feature, PlanCatalogue, Plan, comparison_matrix
from app.ui.themes.icons import IconProvider
from app.ui.widgets.cards import Card, Chip
from app.utils.qt_helpers import expanding_spacer, hbox, open_url, vbox


class PlanComparisonDialog(QDialog):
    """Side-by-side plans, then the full feature matrix."""

    def __init__(
        self,
        catalogue: PlanCatalogue,
        current: Plan,
        highlight: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Bin-Tel — Plans")
        self.setModal(True)
        self.setMinimumSize(960, 660)

        outer = vbox(self)
        body = QFrame(self)
        body.setObjectName("DialogBody")
        outer.addWidget(body)
        layout = vbox(body, margins=(24, 22, 24, 18), spacing=14)

        title = QLabel("Choose the plan that fits your work", body)
        title.setProperty("role", "sectionTitle")
        layout.addWidget(title)

        subtitle = QLabel(
            "Every plan includes the complete local database and unlimited offline "
            "lookups. Paid plans add the professional tooling around them.",
            body,
        )
        subtitle.setProperty("role", "pageSubtitle")
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)

        scroll = QScrollArea(body)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        container = QWidget(scroll)
        container_layout = vbox(container, margins=(0, 4, 0, 4), spacing=16)
        scroll.setWidget(container)

        # -- plan cards -------------------------------------------------------
        cards_row = QWidget(container)
        cards_layout = hbox(cards_row, spacing=14)
        for definition in catalogue.ordered():
            cards_layout.addWidget(
                _plan_card(definition, definition.plan is current, cards_row), 1
            )
        container_layout.addWidget(cards_row)

        # -- feature matrix ---------------------------------------------------
        matrix_card = Card(container, padding=16, spacing=10)
        matrix_title = QLabel("Feature comparison", matrix_card)
        matrix_title.setProperty("role", "sectionTitle")
        matrix_card.body.addWidget(matrix_title)

        plans = catalogue.ordered()
        rows = comparison_matrix(catalogue)
        table = QTableWidget(len(rows), len(plans) + 1, matrix_card)
        table.setHorizontalHeaderLabels(["Feature", *(item.name for item in plans)])
        table.verticalHeader().setVisible(False)
        table.setAlternatingRowColors(True)
        table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.setShowGrid(False)
        table.setAccessibleName("Plan feature comparison")

        provider = IconProvider.instance()
        theme = provider.theme
        for row_index, (feature, availability) in enumerate(rows):
            name_item = QTableWidgetItem(feature.label)
            name_item.setToolTip(feature.description or feature.label)
            if highlight and feature.value == highlight:
                font = name_item.font()
                font.setBold(True)
                name_item.setFont(font)
            table.setItem(row_index, 0, name_item)
            for column, definition in enumerate(plans, start=1):
                included = availability.get(definition.plan, False)
                cell = QTableWidgetItem("Included" if included else "—")
                cell.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                cell.setForeground(
                    provider.theme_color(theme.success)
                    if included
                    else provider.theme_color(theme.text_muted)
                )
                table.setItem(row_index, column, cell)
        header = table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column in range(1, len(plans) + 1):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        table.setMinimumHeight(360)
        matrix_card.body.addWidget(table)
        container_layout.addWidget(matrix_card)

        note = QLabel(
            "Bin-Tel never processes payment-card details in the desktop application. "
            "Subscriptions are managed in your Bin-Tel account.",
            container,
        )
        note.setProperty("role", "muted")
        note.setWordWrap(True)
        container_layout.addWidget(note)
        layout.addWidget(scroll, 1)

        buttons = QDialogButtonBox(body)
        close = buttons.addButton("Close", QDialogButtonBox.ButtonRole.RejectRole)
        close.clicked.connect(self.reject)
        website = buttons.addButton("Open pricing page", QDialogButtonBox.ButtonRole.ActionRole)
        website.setProperty("variant", "primary")
        website.clicked.connect(lambda: open_url(f"{WEBSITE_URL}/pricing"))
        layout.addWidget(buttons)

    @classmethod
    def show_plans(
        cls,
        parent: QWidget | None,
        catalogue: PlanCatalogue,
        current: Plan,
        highlight: str = "",
    ) -> None:
        dialog = cls(catalogue, current, highlight, parent)
        dialog.exec()


def _plan_card(definition, is_current: bool, parent: QWidget) -> Card:
    card = Card(parent, padding=16, spacing=10)

    header = hbox(spacing=8)
    name = QLabel(definition.name, card)
    name.setProperty("role", "sectionTitle")
    header.addWidget(name)
    if is_current:
        header.addWidget(Chip("Your plan", card))
    elif definition.popular:
        header.addWidget(Chip("Most popular", card))
    header.addItem(expanding_spacer())
    card.body.addLayout(header)

    price = QLabel(definition.price_display, card)
    price.setProperty("role", "metricValue")
    price.setStyleSheet("font-size: 17pt;")
    card.body.addWidget(price)

    billing = QLabel(definition.billing_note, card)
    billing.setProperty("role", "muted")
    billing.setWordWrap(True)
    card.body.addWidget(billing)

    tagline = QLabel(definition.tagline, card)
    tagline.setProperty("role", "pageSubtitle")
    tagline.setWordWrap(True)
    card.body.addWidget(tagline)

    provider = IconProvider.instance()
    for highlight in definition.highlights:
        row = QWidget(card)
        row_layout = hbox(row, spacing=8)
        icon = QLabel(row)
        icon.setFixedSize(14, 14)
        icon.setPixmap(provider.pixmap("check", provider.theme.success, 13))
        row_layout.addWidget(icon, 0, Qt.AlignmentFlag.AlignTop)
        label = QLabel(highlight, row)
        label.setWordWrap(True)
        row_layout.addWidget(label, 1)
        card.body.addWidget(row)

    card.body.addStretch(1)
    return card
