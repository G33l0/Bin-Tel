"""Watchlists — what is being tracked, and what changed about it."""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSplitter,
    QWidget,
)

from app.core.errors import ValidationError
from app.licensing.plans import Feature, Limit
from app.models.user_entities import WatchTargetType
from app.services.report_service import ReportFormat
from app.services.watchlist_service import WatchAlert, WatchedItem, WatchlistSummary
from app.ui.dialogs.confirm_dialog import ConfirmDialog
from app.ui.dialogs.export_dialog import ExportDialog
from app.ui.dialogs.watchlist_dialog import CreateWatchlistDialog
from app.ui.pages.base_page import BasePage
from app.ui.themes.icons import IconProvider
from app.ui.widgets.adaptive_stack import AdaptiveStack
from app.ui.widgets.cards import Card, Chip, SectionHeader
from app.ui.widgets.states import EmptyState, StateBanner, StateKind
from app.ui.widgets.upgrade_prompt import FeatureGate
from app.utils.formatting import format_datetime_with_relative, format_relative
from app.utils.qt_helpers import expanding_spacer, hbox, vbox
from app.workers.base import Worker, run_in_background


class WatchlistsPage(BasePage):
    """Manage watchlists, their targets and the alerts they raise."""

    key = "watchlists"
    title = "Watchlists"
    subtitle = "Track BINs, institutions and countries, and see what each update changed."

    def __init__(self, context, parent: QWidget | None = None) -> None:
        super().__init__(context, parent)
        self._watchlists: list[WatchlistSummary] = []
        self._current: WatchlistSummary | None = None

        self.banner = StateBanner("", StateKind.INFO, self.surface, dismissible=True)
        self.banner.hide()
        self.content.addWidget(self.banner)

        body = QWidget(self.surface)
        body_layout = vbox(body, spacing=14)

        toolbar = QWidget(body)
        toolbar_row = hbox(toolbar, spacing=10)
        self.new_button = QPushButton("New watchlist", toolbar)
        self.new_button.setProperty("variant", "primary")
        self.new_button.clicked.connect(self._create)
        toolbar_row.addWidget(self.new_button)

        self.scan_button = QPushButton("Check for changes now", toolbar)
        self.scan_button.clicked.connect(self._scan)
        toolbar_row.addWidget(self.scan_button)

        self.acknowledge_button = QPushButton("Mark all read", toolbar)
        self.acknowledge_button.setProperty("variant", "ghost")
        self.acknowledge_button.clicked.connect(self._acknowledge_all)
        toolbar_row.addWidget(self.acknowledge_button)

        self.export_button = QPushButton("Export activity", toolbar)
        self.export_button.setProperty("variant", "ghost")
        self.export_button.clicked.connect(self._export_activity)
        toolbar_row.addWidget(self.export_button)

        toolbar_row.addItem(expanding_spacer())
        self.quota_label = QLabel("", toolbar)
        self.quota_label.setProperty("role", "muted")
        toolbar_row.addWidget(self.quota_label)
        body_layout.addWidget(toolbar)

        splitter = QSplitter(Qt.Orientation.Horizontal, body)
        splitter.setChildrenCollapsible(False)

        # -- watchlist list --------------------------------------------------
        left = Card(splitter, padding=14, spacing=10)
        left.body.addWidget(SectionHeader("Your watchlists", parent=left))
        self.list_widget = QListWidget(left)
        self.list_widget.setAccessibleName("Watchlists")
        self.list_widget.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.list_widget.currentRowChanged.connect(self._on_selected)
        left.body.addWidget(self.list_widget, 1)

        list_actions = hbox(spacing=8)
        self.rename_button = QPushButton("Rename", left)
        self.rename_button.setProperty("variant", "ghost")
        self.rename_button.clicked.connect(self._rename)
        list_actions.addWidget(self.rename_button)
        self.delete_button = QPushButton("Delete", left)
        self.delete_button.setProperty("variant", "danger")
        self.delete_button.clicked.connect(self._delete)
        list_actions.addWidget(self.delete_button)
        list_actions.addItem(expanding_spacer())
        left.body.addLayout(list_actions)
        splitter.addWidget(left)

        # -- detail ----------------------------------------------------------
        right = QWidget(splitter)
        right_layout = vbox(right, spacing=14)

        self.detail_stack = AdaptiveStack(right)
        self.empty_state = EmptyState(
            "No watchlists yet",
            "Create a watchlist, then add BINs and institutions from their result "
            "pages. Bin-Tel will tell you what each database update changed.",
            self.detail_stack,
            icon_name="empty-box",
            action_text="Create a watchlist",
            on_action=self._create,
        )
        self.detail = QWidget(self.detail_stack)
        detail_layout = vbox(self.detail, spacing=14)

        self.items_card = Card(self.detail, padding=16, spacing=10)
        self.items_header = SectionHeader("Watched items", parent=self.items_card)
        self.items_card.body.addWidget(self.items_header)
        self.items_list = QListWidget(self.items_card)
        self.items_list.setAccessibleName("Watched items")
        self.items_list.setMinimumHeight(160)
        self.items_card.body.addWidget(self.items_list, 1)

        item_actions = hbox(spacing=8)
        self.remove_item_button = QPushButton("Remove selected", self.items_card)
        self.remove_item_button.setProperty("variant", "ghost")
        self.remove_item_button.clicked.connect(self._remove_item)
        item_actions.addWidget(self.remove_item_button)
        item_actions.addItem(expanding_spacer())
        self.items_card.body.addLayout(item_actions)
        detail_layout.addWidget(self.items_card)

        self.events_card = Card(self.detail, padding=16, spacing=10)
        self.events_header = SectionHeader(
            "Detected changes",
            "Differences found when a new database was installed.",
            self.events_card,
        )
        self.events_card.body.addWidget(self.events_header)
        self.events_list = QListWidget(self.events_card)
        self.events_list.setAccessibleName("Detected changes")
        self.events_list.setMinimumHeight(200)
        self.events_card.body.addWidget(self.events_list, 1)
        detail_layout.addWidget(self.events_card, 1)

        self.detail_stack.addWidget(self.empty_state)
        self.detail_stack.addWidget(self.detail)
        right_layout.addWidget(self.detail_stack, 1)
        splitter.addWidget(right)
        splitter.setSizes([280, 700])
        body_layout.addWidget(splitter, 1)

        self.gate = FeatureGate(body, Feature.WATCHLISTS, self.surface)
        self.gate.upgrade_requested.connect(lambda feature: self.navigate(f"license:{feature}"))
        self.content.addWidget(self.gate, 1)

    # -- lifecycle ---------------------------------------------------------
    def refresh(self) -> None:
        entitlement = self.context.entitlements.entitlement(Feature.WATCHLISTS)
        if not self.gate.apply(entitlement):
            return
        self._reload()

    def _reload(self) -> None:
        self._watchlists = self.context.watchlists.list()
        current_id = self._current.id if self._current else None

        self.list_widget.blockSignals(True)
        self.list_widget.clear()
        provider = IconProvider.instance()
        for watchlist in self._watchlists:
            item = QListWidgetItem(f"{watchlist.name}\n{watchlist.subtitle}", self.list_widget)
            item.setData(Qt.ItemDataRole.UserRole, watchlist.id)
            item.setIcon(provider.icon("bin-lookup", provider.theme.text_secondary, 16))
            if watchlist.unread_events:
                item.setToolTip(f"{watchlist.unread_events} unread alert(s)")
            self.list_widget.addItem(item)
        self.list_widget.blockSignals(False)

        limit = self.context.entitlements.limit(Limit.WATCHLISTS, 0)
        self.quota_label.setText(
            f"{len(self._watchlists)} of {'unlimited' if limit < 0 else limit} watchlist(s)"
        )
        self.new_button.setEnabled(limit < 0 or len(self._watchlists) < limit)

        has_any = bool(self._watchlists)
        self.detail_stack.setCurrentWidget(self.detail if has_any else self.empty_state)
        for button in (self.rename_button, self.delete_button, self.scan_button, self.export_button):
            button.setEnabled(has_any)
        unread = self.context.watchlists.unread_count()
        self.acknowledge_button.setEnabled(unread > 0)
        self.acknowledge_button.setText(
            f"Mark {unread} alert(s) read" if unread else "Mark all read"
        )

        if not has_any:
            self._current = None
            return
        index = next(
            (i for i, item in enumerate(self._watchlists) if item.id == current_id), 0
        )
        self.list_widget.setCurrentRow(index)

    def _on_selected(self, row: int) -> None:
        if not (0 <= row < len(self._watchlists)):
            return
        self._current = self._watchlists[row]
        self._load_detail()

    def _load_detail(self) -> None:
        if self._current is None:
            return
        watchlist = self._current
        self.items_header.title_label.setText(f"Watched items — {watchlist.name}")
        self.items_header.set_subtitle(watchlist.description or "")

        items = self.context.watchlists.items(watchlist.id)
        self.items_list.clear()
        provider = IconProvider.instance()
        for item in items:
            entry = QListWidgetItem(
                f"{item.display_label}\n{item.target_type.label} · {item.target_value}",
                self.items_list,
            )
            entry.setData(Qt.ItemDataRole.UserRole, item.id)
            entry.setIcon(
                provider.icon(_target_icon(item.target_type), provider.theme.text_secondary, 16)
            )
            if not item.has_snapshot:
                entry.setToolTip(
                    "This target is not in the current database, so a change cannot be "
                    "compared yet."
                )
            self.items_list.addItem(entry)
        if not items:
            placeholder = QListWidgetItem(
                "Nothing here yet — add BINs and institutions from their result pages.",
                self.items_list,
            )
            placeholder.setFlags(Qt.ItemFlag.NoItemFlags)

        alerts = self.context.watchlists.events(watchlist.id, limit=200)
        self.events_list.clear()
        self.events_header.set_subtitle(
            f"{len(alerts):,} change(s) recorded" if alerts else "No changes detected yet."
        )
        for alert in alerts:
            self.events_list.addItem(_alert_item(alert, self.events_list))
        self.remove_item_button.setEnabled(bool(items))

    # -- actions -----------------------------------------------------------
    def _create(self) -> None:
        entitlement = self.context.entitlements.entitlement(Feature.WATCHLISTS)
        if not entitlement.granted:
            self.navigate(f"license:{entitlement.feature.value}")
            return
        limit = self.context.entitlements.limit(Limit.WATCHLISTS, 0)
        if 0 <= limit <= len(self._watchlists):
            self.banner.show_message(
                f"Your plan includes {limit} watchlist(s).",
                StateKind.WARNING,
                action_text="See plans",
            )
            return
        result = CreateWatchlistDialog.ask(self)
        if result is None:
            return
        name, description = result
        try:
            summary = self.context.watchlists.create(name, description)
        except ValidationError as exc:
            self.banner.show_message(exc.message, StateKind.DANGER)
            return
        from app.telemetry.events import Event

        self.context.telemetry.record(Event.WATCHLIST_CREATED, {"item_count_bucket": "0"})
        self._current = summary
        self._reload()
        self.toast(f"Created “{summary.name}”")

    def _rename(self) -> None:
        if self._current is None:
            return
        dialog = CreateWatchlistDialog(
            self, name=self._current.name, description=self._current.description
        )
        from PyQt6.QtWidgets import QDialog

        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            self.context.watchlists.rename(
                self._current.id, dialog.watchlist_name, dialog.description
            )
        except ValidationError as exc:
            self.banner.show_message(exc.message, StateKind.DANGER)
            return
        self._reload()

    def _delete(self) -> None:
        if self._current is None:
            return
        if not ConfirmDialog.ask(
            self,
            f"Delete “{self._current.name}”?",
            "The watchlist, its items and its recorded changes are removed. Your "
            "database is not affected.",
            confirm_text="Delete watchlist",
            destructive=True,
        ):
            return
        self.context.watchlists.delete(self._current.id)
        self._current = None
        self._reload()
        self.toast("Watchlist deleted")

    def _remove_item(self) -> None:
        item = self.items_list.currentItem()
        if item is None:
            return
        identifier = item.data(Qt.ItemDataRole.UserRole)
        if identifier is None:
            return
        self.context.watchlists.remove_item(int(identifier))
        self._reload()

    def _scan(self) -> None:
        version = self.context.database_version()
        self.scan_button.setEnabled(False)
        self.scan_button.setText("Checking…")

        worker: Worker = Worker(
            self.context.watchlists.scan_for_changes, from_version=version, to_version=version
        )
        worker.signals.result.connect(self._on_scanned)
        worker.signals.failed.connect(lambda exc: self.show_error(exc))
        worker.signals.finished.connect(
            lambda: (
                self.scan_button.setEnabled(True),
                self.scan_button.setText("Check for changes now"),
            )
        )
        run_in_background(worker)

    def _on_scanned(self, alerts: list[WatchAlert]) -> None:
        self._reload()
        if alerts:
            self.banner.show_message(
                f"{len(alerts)} change(s) detected on your watched records.",
                StateKind.WARNING,
            )
        else:
            self.banner.show_message(
                "Everything on your watchlists matches the installed database.",
                StateKind.SUCCESS,
            )

    def _acknowledge_all(self) -> None:
        count = self.context.watchlists.acknowledge()
        self.context.workspace.mark_notifications_read()
        self._reload()
        if count:
            self.toast(f"{count} alert(s) marked read")

    def _export_activity(self) -> None:
        if self._current is None:
            return
        alerts = self.context.watchlists.events(self._current.id, limit=5000)
        if not alerts:
            self.toast("There is no activity to export yet")
            return
        content = self.context.reports.build_watchlist_report(
            self._current.name, alerts, database_version=self.context.database_version()
        )
        chosen = ExportDialog.choose(
            self,
            f"watchlist-{self._current.name}",
            title="Export watchlist activity",
            subtitle=f"Export {len(alerts):,} recorded change(s).",
        )
        if chosen is None:
            return
        path, fmt = chosen
        try:
            report_format = ReportFormat(fmt.value)
            result = self.context.reports.generate(content, report_format, path)
        except Exception as exc:  # noqa: BLE001 - shown in a dialog
            self.show_error(exc)
            return
        self.toast(f"Exported to {result.path.name}")

    def on_theme_changed(self) -> None:
        self.gate.refresh_theme()
        self.empty_state.refresh_icon()
        if self._current is not None:
            self._load_detail()


def _target_icon(target_type: WatchTargetType) -> str:
    return {
        WatchTargetType.BIN: "bin-lookup",
        WatchTargetType.INSTITUTION: "bank-lookup",
        WatchTargetType.COUNTRY: "globe",
        WatchTargetType.SAVED_SEARCH: "filter",
    }[target_type]


def _alert_item(alert: WatchAlert, parent: QListWidget) -> QListWidgetItem:
    provider = IconProvider.instance()
    theme = provider.theme
    colour = {
        "success": theme.success,
        "warning": theme.warning,
        "danger": theme.danger,
    }.get(alert.severity, theme.info)

    prefix = "" if alert.acknowledged else "● "
    version = (
        f"  ·  {alert.from_version} → {alert.to_version}"
        if alert.from_version and alert.to_version
        else ""
    )
    item = QListWidgetItem(
        f"{prefix}{alert.summary}\n"
        f"{alert.change_type.label}  ·  {format_relative(alert.detected_at)}{version}",
        parent,
    )
    item.setIcon(provider.icon("warning" if alert.severity == "warning" else "about", colour, 15))
    item.setToolTip(
        f"{alert.summary}\nDetected {format_datetime_with_relative(alert.detected_at)}"
    )
    item.setData(Qt.ItemDataRole.UserRole, alert.id)
    return item
