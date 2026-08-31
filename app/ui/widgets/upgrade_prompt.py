"""How a locked feature is presented.

A paid feature is never hidden and never breaks the application: the surface
explains what the feature does, which plan includes it, and offers a way to
upgrade. No countdowns, no fake scarcity, no interstitials — the rest of the
application stays fully usable behind it.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QLabel, QPushButton, QSizePolicy, QWidget

from app.licensing.entitlements import Entitlement
from app.licensing.plans import Feature, Plan
from app.ui.themes.icons import IconProvider
from app.ui.widgets.adaptive_stack import AdaptiveStack
from app.ui.widgets.cards import Card, Chip
from app.utils.qt_helpers import centered_paragraph, expanding_spacer, hbox, vbox


class PlanBadge(Chip):
    """A small ``Pro`` / ``Business`` marker for navigation and headings."""

    def __init__(self, plan: Plan, parent: QWidget | None = None) -> None:
        theme = IconProvider.instance().theme
        super().__init__(plan.label.upper(), parent, accent=theme.primary)
        self.plan = plan
        self.setToolTip(f"Included with {plan.label}")
        self.setAccessibleName(f"{plan.label} feature")

    def refresh_theme(self) -> None:
        theme = IconProvider.instance().theme
        self.text_label.setStyleSheet(f"color: {theme.primary};")


class UpgradePrompt(QWidget):
    """Shown in place of a locked feature's content."""

    upgrade_requested = pyqtSignal(str)  # feature value

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        compact: bool = False,
    ) -> None:
        super().__init__(parent)
        self._entitlement: Entitlement | None = None
        self._compact = compact

        layout = vbox(self, spacing=0)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.card = Card(self, padding=16 if compact else 30, spacing=12)
        self.card.setMaximumWidth(560)
        self.card.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)

        badge_row = hbox(spacing=8)
        badge_row.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.badge = Chip("Pro feature", self.card)
        badge_row.addWidget(self.badge)
        self.card.body.addLayout(badge_row)

        self.icon_label = QLabel(self.card)
        self.icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.icon_label.setFixedHeight(0 if compact else 40)
        self.icon_label.setVisible(not compact)
        self.card.body.addWidget(self.icon_label)

        self.title_label = QLabel("This is a Pro feature", self.card)
        self.title_label.setProperty("role", "sectionTitle")
        self.title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.card.body.addWidget(self.title_label)

        self.description_block = centered_paragraph("", self.card, max_width=460)
        self.description_label = self.description_block.label  # type: ignore[attr-defined]
        self.card.body.addWidget(self.description_block)

        self.plan_label = QLabel("", self.card)
        self.plan_label.setProperty("role", "muted")
        self.plan_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.card.body.addWidget(self.plan_label)

        button_row = hbox(spacing=10)
        button_row.addItem(expanding_spacer())
        self.upgrade_button = QPushButton("See plans", self.card)
        self.upgrade_button.setProperty("variant", "primary")
        self.upgrade_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.upgrade_button.setMinimumWidth(160)
        self.upgrade_button.clicked.connect(self._on_upgrade)
        button_row.addWidget(self.upgrade_button)
        button_row.addItem(expanding_spacer())
        self.card.body.addLayout(button_row)

        layout.addWidget(self.card, 0, Qt.AlignmentFlag.AlignCenter)
        self.refresh_icon()

    # -- content ----------------------------------------------------------
    def show_entitlement(self, entitlement: Entitlement) -> None:
        """Describe the locked feature and the plan that unlocks it."""
        self._entitlement = entitlement
        feature = entitlement.feature
        required = entitlement.required_plan or Plan.PRO

        self.badge.set_text(f"{required.label} feature")
        self.title_label.setText(feature.label)
        self.description_label.setText(
            feature.description
            or f"{feature.label} is included with the {required.label} plan."
        )
        self.plan_label.setText(
            f"You are on the {entitlement.plan.label} plan. "
            f"{feature.label} is included with {required.label}."
        )
        self.upgrade_button.setText(f"Upgrade to {required.label}")
        self.upgrade_button.setAccessibleName(
            f"See plans that include {feature.label}"
        )
        self.refresh_icon()

    def _on_upgrade(self) -> None:
        feature = self._entitlement.feature.value if self._entitlement else ""
        self.upgrade_requested.emit(feature)

    def refresh_icon(self) -> None:
        if self._compact:
            return
        provider = IconProvider.instance()
        self.icon_label.setPixmap(provider.pixmap("shield", provider.theme.primary, 36))

    def refresh_theme(self) -> None:
        self.refresh_icon()


class FeatureGate(QWidget):
    """Shows real content, or an upgrade prompt, depending on entitlement.

    Pages hold one of these instead of scattering plan checks: they hand it
    their content widget and a feature, and it decides what is displayed.
    """

    upgrade_requested = pyqtSignal(str)

    def __init__(
        self,
        content: QWidget,
        feature: Feature,
        parent: QWidget | None = None,
        *,
        compact: bool = False,
    ) -> None:
        super().__init__(parent)
        self.feature = feature
        self._stack = AdaptiveStack(self)
        layout = vbox(self)
        layout.addWidget(self._stack)

        self.content = content
        self.prompt = UpgradePrompt(self._stack, compact=compact)
        self.prompt.upgrade_requested.connect(self.upgrade_requested.emit)

        self._stack.addWidget(self.content)
        self._stack.addWidget(self.prompt)

    def apply(self, entitlement: Entitlement) -> bool:
        """Show the content when granted, the prompt when not."""
        if entitlement.granted:
            self._stack.show_widget(self.content)
            return True
        self.prompt.show_entitlement(entitlement)
        self._stack.show_widget(self.prompt)
        return False

    @property
    def unlocked(self) -> bool:
        return self._stack.currentWidget() is self.content

    def refresh_theme(self) -> None:
        self.prompt.refresh_theme()
