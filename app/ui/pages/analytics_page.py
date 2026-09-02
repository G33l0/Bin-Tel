"""Analytics — coverage, distribution and growth across the local database."""

from __future__ import annotations

from PyQt6.QtWidgets import QComboBox, QLabel, QPushButton, QWidget

from app.models.schemas import AdvancedQuery
from app.services.analytics_service import AnalyticsSnapshot
from app.services.report_service import ReportFormat
from app.ui.dialogs.export_dialog import ExportDialog
from app.ui.pages.base_page import BasePage
from app.ui.widgets.cards import Card, CardGrid, MetricCard, SectionHeader
from app.ui.widgets.charts import BarChart, DonutChart, SparkArea
from app.ui.widgets.states import LoadingState, StateBanner, StateKind
from app.utils.formatting import format_number
from app.utils.qt_helpers import expanding_spacer, hbox, vbox
from app.workers.base import Worker, run_in_background

#: Charts shown on the page, in order.
CHART_PANELS: tuple[tuple[str, str, str], ...] = (
    ("country", "By country", "bar"),
    ("network", "By network", "donut"),
    ("card_type", "By card type", "donut"),
    ("funding_type", "By funding type", "bar"),
    ("region", "By state or province", "bar"),
    ("status", "By status", "bar"),
)


class AnalyticsPage(BasePage):
    """Headline counters, distribution charts and database growth."""

    key = "analytics"
    title = "Analytics"
    subtitle = "Coverage, distribution and growth across your local database."

    def __init__(self, context, parent: QWidget | None = None) -> None:
        super().__init__(context, parent)
        self._snapshot: AnalyticsSnapshot | None = None
        self._filter = AdvancedQuery()
        self._loading = False

        self.banner = StateBanner("", StateKind.INFO, self.surface, dismissible=True)
        self.banner.hide()
        self.content.addWidget(self.banner)

        body = QWidget(self.surface)
        body_layout = vbox(body, spacing=18)

        # -- filter bar ----------------------------------------------------
        filter_card = Card(body, padding=14, spacing=10)
        filter_row = hbox(spacing=10)
        label = QLabel("Scope", filter_card)
        label.setProperty("role", "fieldLabel")
        filter_row.addWidget(label)

        self.country_filter = QComboBox(filter_card)
        self.country_filter.setAccessibleName("Limit analytics to one country")
        self.country_filter.setMinimumWidth(190)
        self.country_filter.addItem("All countries", None)
        self.country_filter.currentIndexChanged.connect(self._on_filter_changed)
        filter_row.addWidget(self.country_filter)

        self.network_filter = QComboBox(filter_card)
        self.network_filter.setAccessibleName("Limit analytics to one network")
        self.network_filter.setMinimumWidth(180)
        self.network_filter.addItem("All networks", None)
        self.network_filter.currentIndexChanged.connect(self._on_filter_changed)
        filter_row.addWidget(self.network_filter)

        self.card_type_filter = QComboBox(filter_card)
        self.card_type_filter.setAccessibleName("Limit analytics to one card type")
        self.card_type_filter.setMinimumWidth(170)
        self.card_type_filter.addItem("All card types", None)
        self.card_type_filter.currentIndexChanged.connect(self._on_filter_changed)
        filter_row.addWidget(self.card_type_filter)

        filter_row.addItem(expanding_spacer())

        self.reset_button = QPushButton("Reset", filter_card)
        self.reset_button.setProperty("variant", "ghost")
        self.reset_button.setEnabled(False)
        self.reset_button.clicked.connect(self._reset_filters)
        filter_row.addWidget(self.reset_button)

        self.export_button = QPushButton("Export analytics report", filter_card)
        self.export_button.setProperty("variant", "")
        self.export_button.clicked.connect(self._export)
        filter_row.addWidget(self.export_button)
        filter_card.body.addLayout(filter_row)

        self.scope_label = QLabel("Showing the whole database.", filter_card)
        self.scope_label.setProperty("role", "muted")
        filter_card.body.addWidget(self.scope_label)
        body_layout.addWidget(filter_card)

        # -- headline metrics ------------------------------------------------
        self.metrics = CardGrid(body, minimum_width=190)
        self.cards: dict[str, MetricCard] = {}
        for key, label_text, icon_name in (
            ("total_bins", "Total BINs", "bin-lookup"),
            ("total_institutions", "Total Institutions", "bank-lookup"),
            ("total_countries", "Total Countries", "globe"),
            ("total_networks", "Total Networks", "shield"),
            ("credit", "Credit BINs", "bin-lookup"),
            ("debit", "Debit BINs", "bin-lookup"),
            ("prepaid", "Prepaid BINs", "bin-lookup"),
            ("commercial", "Commercial BINs", "bank-lookup"),
        ):
            card = MetricCard(label_text, "—", icon_name, self.metrics)
            self.cards[key] = card
            self.metrics.add_card(card)
        body_layout.addWidget(self.metrics)

        # -- charts -----------------------------------------------------------
        self.charts: dict[str, object] = {}
        for row_start in (0, 2, 4):
            row = QWidget(body)
            row_layout = hbox(row, spacing=16)
            for name, title, kind in CHART_PANELS[row_start : row_start + 2]:
                card = Card(row, padding=16, spacing=10)
                card.body.addWidget(SectionHeader(title, parent=card))
                chart = BarChart(card, max_bars=8) if kind == "bar" else DonutChart(card)
                self.charts[name] = chart
                card.body.addWidget(chart)  # type: ignore[arg-type]
                # Charts have fixed heights, so without a tail the layout would
                # spread the slack between the rows and float the heading.
                card.body.addStretch(1)
                row_layout.addWidget(card, 1)
            body_layout.addWidget(row)

        # -- growth and institutions ------------------------------------------
        lower = QWidget(body)
        lower_layout = hbox(lower, spacing=16)

        growth_card = Card(lower, padding=16, spacing=10)
        growth_card.body.addWidget(
            SectionHeader(
                "Database growth",
                "Records first seen in each period, and the running total.",
                growth_card,
            )
        )
        self.growth_chart = SparkArea(growth_card)
        growth_card.body.addWidget(self.growth_chart)
        self.growth_summary = QLabel("", growth_card)
        self.growth_summary.setProperty("role", "muted")
        self.growth_summary.setWordWrap(True)
        growth_card.body.addWidget(self.growth_summary)
        growth_card.body.addStretch(1)
        lower_layout.addWidget(growth_card, 1)

        institutions_card = Card(lower, padding=16, spacing=10)
        institutions_card.body.addWidget(
            SectionHeader("Largest institutions", "By number of associated BINs.", institutions_card)
        )
        self.institutions_chart = BarChart(institutions_card, max_bars=8, show_share=False)
        self.institutions_chart.slice_clicked.connect(self._open_institution)
        institutions_card.body.addWidget(self.institutions_chart)
        institutions_card.body.addStretch(1)
        lower_layout.addWidget(institutions_card, 1)
        body_layout.addWidget(lower)

        self.content.addWidget(body, 1)

        self.loading = LoadingState("Computing analytics…", self.surface)
        self.loading.hide()
        self.content.addWidget(self.loading)

        self.add_stretch()

    # -- lifecycle ---------------------------------------------------------
    def on_first_show(self) -> None:
        self._populate_filters()

    def refresh(self) -> None:
        if not self.context.database.is_open:
            self.banner.show_message(
                "The database is not open, so there is nothing to analyse yet.",
                StateKind.WARNING,
                action_text="Open the Database page",
            )
            return
        self.banner.hide()
        self._load()

    def _load(self) -> None:
        if self._loading:
            return
        self._loading = True
        self.loading.show()
        version = self.context.database_version()
        worker: Worker = Worker(
            self.context.analytics.snapshot,
            version=version,
            query=self._filter if not self._filter.is_empty else None,
        )
        worker.signals.result.connect(self._apply)
        worker.signals.failed.connect(self._on_failed)
        worker.signals.finished.connect(self._on_finished)
        run_in_background(worker)

    def _on_finished(self) -> None:
        self._loading = False
        self.loading.hide()

    def _apply(self, snapshot: AnalyticsSnapshot) -> None:
        self._snapshot = snapshot
        values = {
            "total_bins": snapshot.total_bins,
            "total_institutions": snapshot.total_institutions,
            "total_countries": snapshot.total_countries,
            "total_networks": snapshot.total_networks,
            "credit": snapshot.credit_bins,
            "debit": snapshot.debit_bins,
            "prepaid": snapshot.prepaid_bins,
            "commercial": snapshot.commercial_bins,
        }
        total = max(1, snapshot.total_bins)
        for key, card in self.cards.items():
            value = values.get(key, 0)
            detail = ""
            if key in ("credit", "debit", "prepaid", "commercial") and snapshot.total_bins:
                detail = f"{value / total:.1%} of BINs"
            card.set_value(format_number(value), detail)

        for name, chart in self.charts.items():
            chart.set_distribution(snapshot.distribution(name))  # type: ignore[attr-defined]

        self.growth_chart.set_points(snapshot.growth)
        if snapshot.growth:
            first, last = snapshot.growth[0], snapshot.growth[-1]
            self.growth_summary.setText(
                f"{format_number(sum(point.added for point in snapshot.growth))} record(s) "
                f"first seen between {first.period} and {last.period}. "
                f"{format_number(snapshot.recently_added)} added and "
                f"{format_number(snapshot.recently_changed)} changed in the last 90 days."
            )
        else:
            self.growth_summary.setText(
                "This database does not record when its records were first seen, so "
                "growth cannot be plotted."
            )

        from app.services.analytics_service import Distribution, Slice

        self.institutions_chart.set_distribution(
            Distribution(
                name="institutions",
                title="Largest institutions",
                slices=[
                    Slice(key=name, label=name, value=count)
                    for name, count in snapshot.top_institutions
                ],
                total=snapshot.total_bins,
            )
        )
        self.status_message.emit(
            f"Analytics computed in {snapshot.elapsed_ms:.0f} ms"
        )

    def _on_failed(self, exc: BaseException) -> None:
        self.banner.show_message("Analytics could not be computed.", StateKind.DANGER)
        self.show_error(exc)

    # -- filters -----------------------------------------------------------
    def _populate_filters(self) -> None:
        if not self.context.database.is_open:
            return
        worker: Worker = Worker(self.context.search.filter_values)
        worker.signals.result.connect(self._fill_filters)
        run_in_background(worker)

    def _fill_filters(self, values: dict) -> None:
        for combo, key, placeholder in (
            (self.country_filter, "country", "All countries"),
            (self.network_filter, "network", "All networks"),
            (self.card_type_filter, "card_type", "All card types"),
        ):
            combo.blockSignals(True)
            current = combo.currentData()
            combo.clear()
            combo.addItem(placeholder, None)
            for code, label in values.get(key, []):
                combo.addItem(label, code)
            index = combo.findData(current)
            combo.setCurrentIndex(max(0, index))
            combo.blockSignals(False)

    def _on_filter_changed(self) -> None:
        self._filter = AdvancedQuery(
            country_code=self.country_filter.currentData(),
            network_code=self.network_filter.currentData(),
            card_type=self.card_type_filter.currentData(),
        )
        self.reset_button.setEnabled(not self._filter.is_empty)
        self.scope_label.setText(
            "Showing the whole database."
            if self._filter.is_empty
            else f"Scoped to — {self._filter.describe()}"
        )
        self._load()

    def _reset_filters(self) -> None:
        for combo in (self.country_filter, self.network_filter, self.card_type_filter):
            combo.blockSignals(True)
            combo.setCurrentIndex(0)
            combo.blockSignals(False)
        self._on_filter_changed()

    # -- export ------------------------------------------------------------
    def _export(self) -> None:
        if self._snapshot is None:
            self.toast("There is nothing to export yet")
            return
        content = self.context.reports.build_analytics_report(
            self._snapshot, database_version=self.context.database_version()
        )
        chosen = ExportDialog.choose(
            self,
            "analytics",
            title="Export analytics report",
            subtitle="Choose a format for the analytics summary.",
        )
        if chosen is None:
            return
        path, fmt = chosen
        try:
            report_format = ReportFormat(fmt.value)
            result = self.context.reports.generate(content, report_format, path)
        except Exception as exc:
            self.show_error(exc)
            return
        self.context.workspace.record_report(
            content.title,
            content.report_type.value,
            report_format.value,
            str(result.path),
            row_count=result.row_count,
            size_bytes=result.size_bytes,
            database_version=content.database_version,
        )
        self.toast(f"Exported to {result.path.name}")

    def _open_institution(self, name: str) -> None:
        self.navigate("institutions")

    def on_theme_changed(self) -> None:
        for card in self.cards.values():
            card.refresh_icon()
        for chart in self.charts.values():
            chart.update()  # type: ignore[attr-defined]
        self.growth_chart.update()
        self.institutions_chart.update()

    def on_database_changed(self) -> None:
        self.context.analytics.invalidate()
        self._populate_filters()
        super().on_database_changed()
