"""Dashboard — the health and scale of the local Bin-Tel database at a glance."""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QLabel, QPushButton, QWidget

from app.core.constants import UNKNOWN_DISPLAY
from app.models.schemas import DatabaseInfo
from app.ui.pages.base_page import BasePage
from app.ui.widgets.cards import Card, CardGrid, MetricCard, SectionHeader
from app.ui.widgets.states import StateBanner, StateKind
from app.utils.formatting import (
    format_bytes,
    format_datetime,
    format_datetime_with_relative,
    format_number,
    format_relative,
)
from app.utils.qt_helpers import grid, hbox, vbox
from app.workers.base import Worker, run_in_background


class DashboardPage(BasePage):
    """Eight headline metrics plus coverage highlights. Deliberately uncluttered."""

    key = "dashboard"
    title = "Dashboard"
    subtitle = "The scale, coverage and freshness of your local intelligence database."

    def __init__(self, context, parent: QWidget | None = None) -> None:
        super().__init__(context, parent)
        self._info: DatabaseInfo | None = None

        self.banner = StateBanner("", StateKind.INFO, self.surface, dismissible=True)
        self.banner.hide()
        self.content.addWidget(self.banner)

        # -- headline metrics ---------------------------------------------
        self.metrics = CardGrid(self.surface, minimum_width=205)
        self.cards: dict[str, MetricCard] = {}
        for key, label, icon_name in (
            ("bins", "Total BINs", "bin-lookup"),
            ("institutions", "Total Institutions", "bank-lookup"),
            ("countries", "Countries", "globe"),
            ("networks", "Networks", "shield"),
            ("version", "Database Version", "database"),
            ("size", "Database Size", "database"),
            ("updated", "Last Database Update", "updates"),
            ("next", "Next Update", "refresh"),
        ):
            card = MetricCard(label, "—", icon_name, self.metrics)
            self.cards[key] = card
            self.metrics.add_card(card)
        self.content.addWidget(self.metrics)

        # -- coverage ------------------------------------------------------
        coverage_row = QWidget(self.surface)
        coverage_layout = hbox(coverage_row, spacing=16)

        self.countries_card = Card(coverage_row, padding=18, spacing=12)
        self.countries_card.body.addWidget(
            SectionHeader("Top countries", "Where the BINs in your database are issued.", self.countries_card)
        )
        self._countries_holder = QWidget(self.countries_card)
        self._countries_grid = grid(self._countries_holder, spacing=8)
        self.countries_card.body.addWidget(self._countries_holder)
        coverage_layout.addWidget(self.countries_card, 1)

        self.networks_card = Card(coverage_row, padding=18, spacing=12)
        self.networks_card.body.addWidget(
            SectionHeader("Top networks", "Card schemes represented in your database.", self.networks_card)
        )
        self._networks_holder = QWidget(self.networks_card)
        self._networks_grid = grid(self._networks_holder, spacing=8)
        self.networks_card.body.addWidget(self._networks_holder)
        coverage_layout.addWidget(self.networks_card, 1)

        self.content.addWidget(coverage_row)

        # -- quick actions --------------------------------------------------
        actions = Card(self.surface, padding=18, spacing=12)
        actions.body.addWidget(SectionHeader("Quick actions", parent=actions))
        action_row = hbox(spacing=10)
        for label, target in (
            ("Look up a BIN", "bin_lookup"),
            ("Find an institution", "bank_lookup"),
            ("Manage the database", "database"),
            ("Check for updates", "updates"),
        ):
            button = QPushButton(label, actions)
            button.setProperty("variant", "ghost")
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(lambda _=False, key=target: self.navigate(key))
            action_row.addWidget(button)
        action_row.addStretch(1)
        actions.body.addLayout(action_row)
        self.content.addWidget(actions)

        self.add_stretch()

    # -- data -------------------------------------------------------------
    def refresh(self) -> None:
        if not self.context.database.is_open:
            self._show_not_ready()
            return
        worker: Worker = Worker(self._collect)
        worker.signals.result.connect(self._apply)
        worker.signals.failed.connect(lambda exc: self._show_not_ready(str(exc)))
        run_in_background(worker)

    def _collect(self) -> tuple[DatabaseInfo, list[tuple[str, int]], list[tuple[str, int]]]:
        return (
            self.context.stats.info(),
            self.context.stats.top_countries(6),
            self.context.stats.top_networks(6),
        )

    def _apply(self, payload: tuple[DatabaseInfo, list, list]) -> None:
        info, countries, networks = payload
        self._info = info
        stats = info.stats

        self.cards["bins"].set_value(format_number(stats.bins))
        self.cards["institutions"].set_value(format_number(stats.institutions))
        self.cards["countries"].set_value(
            format_number(stats.countries), "with issued BIN ranges"
        )
        self.cards["networks"].set_value(format_number(stats.networks))
        self.cards["version"].set_value(
            info.version or UNKNOWN_DISPLAY,
            format_datetime(info.release_date, with_time=False)
            if info.release_date
            else "",
        )
        self.cards["size"].set_value(
            format_bytes(info.size_bytes),
            f"{format_number(stats.bin_ranges)} ranges · {format_number(stats.aliases)} aliases",
        )
        self.cards["updated"].set_value(
            format_relative(info.installed_at) if info.installed_at else "Never",
            format_datetime(info.installed_at) if info.installed_at else "",
        )

        due = self.context.config.next_update_due()
        settings = self.context.config.settings.database
        if not settings.automatic_updates or due is None:
            self.cards["next"].set_value("Manual", "Automatic checks are off")
        else:
            self.cards["next"].set_value(
                format_relative(due), settings.update_frequency.label
            )

        self._fill_breakdown(self._countries_grid, countries, stats.bins)
        self._fill_breakdown(self._networks_grid, networks, stats.bins)

        if stats.bins == 0:
            self.banner.show_message(
                "Your database is installed but contains no BIN records.",
                StateKind.WARNING,
                action_text="Check for updates",
            )
        else:
            self.banner.hide()

    def _fill_breakdown(self, layout, rows: list[tuple[str, int]], total: int) -> None:
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        if not rows:
            empty = QLabel("No data yet.", self.surface)
            empty.setProperty("role", "muted")
            layout.addWidget(empty, 0, 0)
            return
        for index, (name, count) in enumerate(rows):
            share = f"{(count / total * 100):.1f}%" if total else "—"
            name_label = QLabel(name, self.surface)
            count_label = QLabel(f"{format_number(count)}   ({share})", self.surface)
            count_label.setProperty("role", "muted")
            count_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            layout.addWidget(name_label, index, 0)
            layout.addWidget(count_label, index, 1)
        layout.setColumnStretch(0, 1)

    def _show_not_ready(self, detail: str = "") -> None:
        for card in self.cards.values():
            card.set_value("—")
        self.banner.show_message(
            "The database is not open yet." + (f" {detail}" if detail else ""),
            StateKind.WARNING,
            action_text="Open the Database page",
        )
        self.banner.action_triggered.connect(lambda: self.navigate("database"))

    def on_theme_changed(self) -> None:
        for card in self.cards.values():
            card.refresh_icon()
        self.banner.set_kind(StateKind.INFO)

    def summary_line(self) -> str:
        if self._info is None:
            return ""
        return (
            f"{format_number(self._info.stats.bins)} BINs · "
            f"database {self._info.version or UNKNOWN_DISPLAY} · "
            f"updated {format_datetime_with_relative(self._info.installed_at)}"
        )
