"""Licence and plans — status, activation, devices and a plan comparison."""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QWidget,
)

from app.core.errors import BinTelError
from app.licensing.models import DeviceRecord, LicenseSnapshot, redact_key
from app.licensing.plans import Feature, Plan, PlanDefinition
from app.ui.dialogs.confirm_dialog import ConfirmDialog
from app.ui.dialogs.plan_dialog import PlanComparisonDialog
from app.ui.pages.base_page import BasePage
from app.ui.themes.icons import IconProvider
from app.ui.widgets.cards import Card, Chip, FieldRow, SectionHeader
from app.ui.widgets.states import StateBanner, StateKind
from app.utils.formatting import format_datetime
from app.utils.qt_helpers import expanding_spacer, grid, hbox, open_url, vbox
from app.workers.base import Worker, run_in_background


class LicensePage(BasePage):
    """Everything about the current plan, in one honest place."""

    key = "license"
    title = "Plan & Licence"
    subtitle = "Your plan, what it includes, and the devices it is activated on."

    def __init__(self, context, parent: QWidget | None = None) -> None:
        super().__init__(context, parent)
        self._busy = False

        self.banner = StateBanner("", StateKind.INFO, self.surface, dismissible=True)
        self.banner.hide()
        self.content.addWidget(self.banner)

        # -- current plan ----------------------------------------------------
        self.status_card = Card(self.surface, padding=22, spacing=14)
        header_row = hbox(spacing=14)

        column = vbox(spacing=6)
        self.plan_label = QLabel("Free", self.status_card)
        self.plan_label.setProperty("role", "pageTitle")
        column.addWidget(self.plan_label)

        self.plan_tagline = QLabel("", self.status_card)
        self.plan_tagline.setProperty("role", "pageSubtitle")
        self.plan_tagline.setWordWrap(True)
        column.addWidget(self.plan_tagline)

        self.chip_row = hbox(spacing=6)
        column.addLayout(self.chip_row)
        header_row.addLayout(column, 1)

        actions = vbox(spacing=6)
        actions.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.upgrade_button = QPushButton("Compare plans", self.status_card)
        self.upgrade_button.setProperty("variant", "primary")
        self.upgrade_button.setMinimumWidth(170)
        self.upgrade_button.clicked.connect(self.show_plans)
        actions.addWidget(self.upgrade_button)

        self.manage_button = QPushButton("Manage subscription", self.status_card)
        self.manage_button.setMinimumWidth(170)
        self.manage_button.clicked.connect(self._open_account)
        actions.addWidget(self.manage_button)
        header_row.addLayout(actions)
        self.status_card.body.addLayout(header_row)

        self.message_label = QLabel("", self.status_card)
        self.message_label.setWordWrap(True)
        self.message_label.setVisible(False)
        self.status_card.body.addWidget(self.message_label)
        self.content.addWidget(self.status_card)

        # -- activation --------------------------------------------------------
        self.activation_card = Card(self.surface, padding=18, spacing=12)
        self.activation_card.body.addWidget(
            SectionHeader(
                "Activate a licence",
                "Enter the licence key from your Bin-Tel account.",
                self.activation_card,
            )
        )
        key_row = hbox(spacing=10)
        self.key_field = QLineEdit(self.activation_card)
        self.key_field.setPlaceholderText("BINTEL-XXXX-XXXX-XXXX")
        self.key_field.setAccessibleName("Licence key")
        self.key_field.returnPressed.connect(self._activate)
        key_row.addWidget(self.key_field, 1)

        self.activate_button = QPushButton("Activate", self.activation_card)
        self.activate_button.setProperty("variant", "primary")
        self.activate_button.setMinimumWidth(130)
        self.activate_button.clicked.connect(self._activate)
        key_row.addWidget(self.activate_button)
        self.activation_card.body.addLayout(key_row)

        self.activation_hint = QLabel("", self.activation_card)
        self.activation_hint.setProperty("role", "muted")
        self.activation_hint.setWordWrap(True)
        self.activation_card.body.addWidget(self.activation_hint)
        self.content.addWidget(self.activation_card)

        # -- licence details ---------------------------------------------------
        self.details_card = Card(self.surface, padding=18, spacing=12)
        self.details_card.body.addWidget(
            SectionHeader("Licence details", parent=self.details_card)
        )
        self._details_holder = QWidget(self.details_card)
        self._details_grid = grid(self._details_holder, spacing=14)
        self.details_card.body.addWidget(self._details_holder)

        detail_actions = hbox(spacing=8)
        self.revalidate_button = QPushButton("Verify now", self.details_card)
        self.revalidate_button.clicked.connect(self._revalidate)
        detail_actions.addWidget(self.revalidate_button)
        self.deactivate_button = QPushButton("Deactivate this device", self.details_card)
        self.deactivate_button.setProperty("variant", "danger")
        self.deactivate_button.clicked.connect(self._deactivate)
        detail_actions.addWidget(self.deactivate_button)
        detail_actions.addItem(expanding_spacer())
        self.details_card.body.addLayout(detail_actions)
        self.content.addWidget(self.details_card)

        # -- devices -----------------------------------------------------------
        self.devices_card = Card(self.surface, padding=18, spacing=10)
        self.devices_card.body.addWidget(
            SectionHeader(
                "Activated devices",
                "Bin-Tel identifies a device with a random installation identifier — "
                "never a hardware fingerprint.",
                self.devices_card,
            )
        )
        self.devices_list = QListWidget(self.devices_card)
        self.devices_list.setAccessibleName("Activated devices")
        self.devices_list.setMaximumHeight(160)
        self.devices_card.body.addWidget(self.devices_list)
        self.content.addWidget(self.devices_card)

        # -- what your plan includes -------------------------------------------
        self.features_card = Card(self.surface, padding=18, spacing=10)
        self.features_card.body.addWidget(
            SectionHeader("What your plan includes", parent=self.features_card)
        )
        self._features_holder = QWidget(self.features_card)
        self._features_grid = grid(self._features_holder, spacing=10)
        self.features_card.body.addWidget(self._features_holder)
        self.content.addWidget(self.features_card)

        self.add_stretch()

    # -- lifecycle ---------------------------------------------------------
    def refresh(self) -> None:
        snapshot = self.context.licenses.snapshot
        definition = self.context.entitlements.definition
        self._render_status(snapshot, definition)
        self._render_details(snapshot)
        self._render_features(definition)
        self._render_devices()

        mode = self.context.config.settings.license.service_mode
        from app.core.config import LicenseServiceMode

        if mode is LicenseServiceMode.DEVELOPMENT:
            self.activation_hint.setText(
                "The local development licensing service is selected. It issues real, "
                "signed licences for testing — try BINTEL-DEV-PRO, BINTEL-DEV-BUSINESS "
                "or BINTEL-DEV-ENTERPRISE."
            )
        elif not self.context.activation.available:
            self.activation_hint.setText(
                "No licensing service is configured for this build, so activation is "
                "unavailable. Everything on the Free plan continues to work. Choose "
                "the development service in Settings → Licence to try paid features."
            )
        else:
            self.activation_hint.setText(
                "Your licence is verified with the Bin-Tel licensing service and then "
                "kept locally, so Bin-Tel keeps working offline."
            )
        self.activate_button.setEnabled(
            self.context.activation.available and not self._busy
        )

    def _render_status(self, snapshot: LicenseSnapshot, definition: PlanDefinition) -> None:
        self.plan_label.setText(f"{definition.name} plan")
        self.plan_tagline.setText(definition.tagline)

        while self.chip_row.count():
            item = self.chip_row.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        theme = IconProvider.instance().theme
        state_colour = {
            "success": theme.success,
            "warning": theme.warning,
            "danger": theme.danger,
        }.get(snapshot.state.badge_state, theme.info)
        self.chip_row.addWidget(Chip(snapshot.state.label, self.status_card, accent=state_colour))
        self.chip_row.addWidget(
            Chip(f"{definition.database_edition.title()} database", self.status_card)
        )
        if snapshot.days_remaining is not None:
            self.chip_row.addWidget(
                Chip(f"{snapshot.days_remaining} day(s) remaining", self.status_card)
            )
        self.chip_row.addItem(expanding_spacer())

        self.message_label.setText(snapshot.message)
        self.message_label.setVisible(bool(snapshot.message))
        self.message_label.setProperty("state", snapshot.state.badge_state)

        activated = snapshot.is_activated
        self.activation_card.setVisible(not activated or snapshot.state.name == "INVALID")
        self.details_card.setVisible(activated)
        self.devices_card.setVisible(activated)
        self.deactivate_button.setEnabled(activated and not self._busy)
        self.revalidate_button.setEnabled(activated and not self._busy)
        self.upgrade_button.setText(
            "Compare plans" if activated else "See plans and pricing"
        )

    def _render_details(self, snapshot: LicenseSnapshot) -> None:
        while self._details_grid.count():
            item = self._details_grid.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        rows = list(snapshot.describe())
        if snapshot.license_key:
            rows.insert(3, ("Licence key", redact_key(snapshot.license_key)))
        if snapshot.grace_until is not None:
            rows.append(("Offline until", format_datetime(snapshot.grace_until, with_time=False)))
        for index, (label, value) in enumerate(rows):
            self._details_grid.addWidget(
                FieldRow(label, value, self._details_holder), index // 3, index % 3
            )
        for column in range(3):
            self._details_grid.setColumnStretch(column, 1)

    def _render_features(self, definition: PlanDefinition) -> None:
        while self._features_grid.count():
            item = self._features_grid.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        provider = IconProvider.instance()
        theme = provider.theme
        features = [feature for feature in Feature if definition.has(feature)]
        locked = [feature for feature in Feature if not definition.has(feature)][:6]

        for index, feature in enumerate(features):
            row = QWidget(self._features_holder)
            layout = hbox(row, spacing=8)
            icon = QLabel(row)
            icon.setFixedSize(15, 15)
            icon.setPixmap(provider.pixmap("check", theme.success, 14))
            layout.addWidget(icon)
            label = QLabel(feature.label, row)
            layout.addWidget(label, 1)
            self._features_grid.addWidget(row, index // 3, index % 3)

        offset = len(features)
        for index, feature in enumerate(locked):
            row = QWidget(self._features_holder)
            layout = hbox(row, spacing=8)
            icon = QLabel(row)
            icon.setFixedSize(15, 15)
            icon.setPixmap(provider.pixmap("shield", theme.text_muted, 14))
            layout.addWidget(icon)
            label = QLabel(feature.label, row)
            label.setProperty("role", "muted")
            layout.addWidget(label, 1)
            position = offset + index
            self._features_grid.addWidget(row, position // 3, position % 3)
        for column in range(3):
            self._features_grid.setColumnStretch(column, 1)

    def _render_devices(self) -> None:
        self.devices_list.clear()
        if not self.context.licenses.snapshot.is_activated:
            return
        worker: Worker = Worker(self.context.licenses.devices)
        worker.signals.result.connect(self._fill_devices)
        run_in_background(worker)

    def _fill_devices(self, devices: list[DeviceRecord]) -> None:
        self.devices_list.clear()
        provider = IconProvider.instance()
        for device in devices:
            suffix = "  ·  this device" if device.current else ""
            item = QListWidgetItem(
                f"{device.display_name}{suffix}\n"
                f"{device.platform or 'Unknown platform'} · "
                f"activated {format_datetime(device.activated_at, with_time=False)}",
                self.devices_list,
            )
            item.setIcon(
                provider.icon(
                    "check" if device.current else "shield",
                    provider.theme.success if device.current else provider.theme.text_secondary,
                    15,
                )
            )
            self.devices_list.addItem(item)
        if not devices:
            self.devices_list.addItem("No devices are activated for this licence.")

    # -- actions -----------------------------------------------------------
    def _activate(self) -> None:
        key = self.key_field.text().strip()
        if not key:
            self.banner.show_message("Enter your licence key to activate.", StateKind.WARNING)
            return
        self._set_busy(True, "Activating…")

        worker: Worker = Worker(self.context.licenses.activate, key)
        worker.signals.result.connect(self._on_activated)
        worker.signals.failed.connect(self._on_license_error)
        worker.signals.finished.connect(lambda: self._set_busy(False))
        run_in_background(worker)

    def _on_activated(self, snapshot: LicenseSnapshot) -> None:
        self.key_field.clear()
        self.banner.show_message(
            f"{snapshot.plan.label} activated on this device.", StateKind.SUCCESS
        )
        from app.telemetry.events import Event

        self.context.telemetry.record(
            Event.LICENSE_ACTIVATED,
            {"plan": snapshot.plan.value, "edition": snapshot.edition},
        )
        self.context.entitlements.notify()
        self.refresh()
        self.toast(f"{snapshot.plan.label} plan activated")

    def _revalidate(self) -> None:
        self._set_busy(True, "Verifying…")
        worker: Worker = Worker(self.context.licenses.revalidate, force=True)
        worker.signals.result.connect(self._on_revalidated)
        worker.signals.failed.connect(self._on_license_error)
        worker.signals.finished.connect(lambda: self._set_busy(False))
        run_in_background(worker)

    def _on_revalidated(self, snapshot: LicenseSnapshot) -> None:
        self.banner.show_message(
            snapshot.message or f"Your {snapshot.plan.label} licence is valid.",
            StateKind.SUCCESS if snapshot.state.is_entitled else StateKind.WARNING,
        )
        self.context.entitlements.notify()
        self.refresh()

    def _deactivate(self) -> None:
        if not ConfirmDialog.ask(
            self,
            "Deactivate this device?",
            "Bin-Tel returns to the Free plan on this machine and the seat is released "
            "for another device. Your database, watchlists and settings are untouched.",
            confirm_text="Deactivate",
            destructive=True,
        ):
            return
        self._set_busy(True, "Deactivating…")
        worker: Worker = Worker(self.context.licenses.deactivate)
        worker.signals.result.connect(self._on_deactivated)
        worker.signals.failed.connect(self._on_license_error)
        worker.signals.finished.connect(lambda: self._set_busy(False))
        run_in_background(worker)

    def _on_deactivated(self, snapshot: LicenseSnapshot) -> None:
        from app.telemetry.events import Event

        self.context.telemetry.record(Event.LICENSE_DEACTIVATED, {"plan": "free"})
        self.banner.show_message(
            "This device has been deactivated and Bin-Tel is on the Free plan.",
            StateKind.INFO,
        )
        self.context.entitlements.notify()
        self.refresh()

    def _on_license_error(self, exc: BaseException) -> None:
        message = exc.message if isinstance(exc, BinTelError) else str(exc)
        self.banner.show_message(message, StateKind.DANGER)

    def _set_busy(self, busy: bool, label: str = "") -> None:
        self._busy = busy
        self.activate_button.setEnabled(not busy and self.context.activation.available)
        self.activate_button.setText(label if busy else "Activate")
        self.revalidate_button.setEnabled(not busy)
        self.deactivate_button.setEnabled(not busy)

    def show_plans(self, highlight: str = "") -> None:
        """Open the plan comparison, optionally highlighting a feature."""
        from app.telemetry.events import Event

        self.context.telemetry.record(
            Event.UPGRADE_PAGE_OPENED, {"source": "license", "feature": highlight}
        )
        PlanComparisonDialog.show_plans(
            self, self.context.plans, self.context.entitlements.plan, highlight
        )

    def highlight_feature(self, feature: str) -> None:
        """Called when a locked feature sent the user here."""
        if not feature:
            return
        try:
            resolved = Feature(feature)
        except ValueError:
            return
        required = self.context.plans.plan_for_feature(resolved)
        self.banner.show_message(
            f"{resolved.label} is included with the "
            f"{(required.name if required else Plan.PRO.label)} plan.",
            StateKind.INFO,
            action_text="Compare plans",
        )
        try:
            self.banner.action_triggered.disconnect()
        except TypeError:
            pass
        self.banner.action_triggered.connect(lambda: self.show_plans(feature))

    def _open_account(self) -> None:
        from app.core.constants import WEBSITE_URL

        open_url(f"{WEBSITE_URL}/account")

    def on_theme_changed(self) -> None:
        self.refresh()
