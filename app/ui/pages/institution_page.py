"""Institution Intelligence — the full profile of a financial institution."""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QComboBox, QLabel, QPushButton, QWidget

from app.core.errors import ValidationError
from app.models.schemas import (
    BankLookupResult,
    BinFilters,
    BinRow,
    InstitutionDetail,
    InstitutionStats,
    Page,
    PageRequest,
)
from app.models.user_entities import FavoriteKind, WatchTargetType
from app.services.report_service import ReportFormat
from app.ui.dialogs.export_dialog import ExportDialog
from app.ui.dialogs.watchlist_dialog import AddToWatchlistDialog
from app.ui.pages.base_page import BasePage
from app.ui.widgets.adaptive_stack import AdaptiveStack
from app.ui.widgets.bank_result_view import BankResultView
from app.ui.widgets.cards import Card, SectionHeader
from app.ui.widgets.charts import BarChart, DonutChart
from app.ui.widgets.search_box import SearchBox
from app.ui.widgets.states import EmptyState, ErrorState, LoadingState
from app.utils.qt_helpers import expanding_spacer, hbox, shortcut, vbox
from app.workers.base import Worker, run_in_background
from app.workers.search_worker import (
    AllBinsWorker,
    BankSearchWorker,
    BinPageWorker,
    FilterOptionsWorker,
    InstitutionStatsWorker,
)

PLACEHOLDER = "Search an institution by name, legal name or alias…"


class InstitutionIntelligencePage(BasePage):
    """A searchable institution profile with its portfolio analytics."""

    key = "institutions"
    title = "Institution Intelligence"
    subtitle = "Profile an institution and analyse its complete BIN portfolio."

    def __init__(self, context, parent: QWidget | None = None) -> None:
        super().__init__(context, parent)
        self._matches: list[InstitutionDetail] = []
        self._current: InstitutionDetail | None = None
        self._filters = BinFilters()
        self._last_query = ""

        search_card = Card(self.surface, padding=20, spacing=10)
        self.search = SearchBox(PLACEHOLDER, search_card)
        self.search.search_requested.connect(self.perform_search)
        self.search.cleared.connect(self.clear)
        search_card.body.addWidget(self.search)

        row = hbox(spacing=10)
        match_label = QLabel("Match", search_card)
        match_label.setProperty("role", "fieldLabel")
        row.addWidget(match_label)
        self.match_mode = QComboBox(search_card)
        self.match_mode.setAccessibleName("How the institution name is matched")
        from app.models.schemas import MatchMode

        for mode in MatchMode:
            self.match_mode.addItem(mode.label, mode.value)
        self.match_mode.setCurrentIndex(self.match_mode.findData(MatchMode.CONTAINS.value))
        row.addWidget(self.match_mode)
        row.addItem(expanding_spacer())
        search_card.body.addLayout(row)
        self.content.addWidget(search_card)

        self.stack = AdaptiveStack(self.surface)
        self.content.addWidget(self.stack, 1)

        self.idle_state = EmptyState(
            "Search for an institution",
            "Bin-Tel matches the display name, legal name and every recorded alias, "
            "with exact, prefix, contains and fuzzy matching.",
            self.stack,
            icon_name="bank-lookup",
        )
        self.loading_state = LoadingState("Searching institutions…", self.stack)
        self.empty_state = EmptyState(
            "No institution found", "", self.stack, action_text="Clear search", on_action=self.clear
        )
        self.error_state = ErrorState(parent=self.stack)
        self.error_state.retry_requested.connect(lambda: self.perform_search(self._last_query))

        self.profile = QWidget(self.stack)
        profile_layout = vbox(self.profile, spacing=16)

        actions = QWidget(self.profile)
        action_row = hbox(actions, spacing=10)
        self.watch_button = QPushButton("Add to watchlist", actions)
        self.watch_button.clicked.connect(self._add_to_watchlist)
        action_row.addWidget(self.watch_button)
        self.favorite_button = QPushButton("Add to favourites", actions)
        self.favorite_button.setProperty("variant", "ghost")
        self.favorite_button.clicked.connect(self._toggle_favorite)
        action_row.addWidget(self.favorite_button)
        self.report_button = QPushButton("Generate profile report", actions)
        self.report_button.setProperty("variant", "primary")
        self.report_button.clicked.connect(self._generate_report)
        action_row.addWidget(self.report_button)
        action_row.addItem(expanding_spacer())
        profile_layout.addWidget(actions)

        self.result_view = BankResultView(
            self.profile, page_size=context.config.settings.search.results_per_page
        )
        self.result_view.page_requested.connect(self._load_page)
        self.result_view.filters_changed.connect(self._on_filters_changed)
        self.result_view.export_requested.connect(self._export_table)
        self.result_view.match_selected.connect(self.select_institution)
        self.result_view.copied.connect(self.toast)
        profile_layout.addWidget(self.result_view, 1)

        # -- portfolio analytics -------------------------------------------
        analytics_card = Card(self.profile, padding=16, spacing=12)
        analytics_card.body.addWidget(
            SectionHeader(
                "Portfolio analytics",
                "How this institution's BINs are distributed.",
                analytics_card,
            )
        )
        charts_row = hbox(spacing=16)
        self.charts: dict[str, object] = {}
        for name, is_donut in (("network", True), ("card_type", True), ("country", False)):
            holder = Card(analytics_card, padding=12, spacing=6)
            holder.body.addWidget(
                SectionHeader(
                    {
                        "network": "Networks",
                        "card_type": "Card types",
                        "country": "Countries",
                    }[name],
                    parent=holder,
                )
            )
            chart = DonutChart(holder, max_slices=5) if is_donut else BarChart(holder, max_bars=6)
            self.charts[name] = chart
            holder.body.addWidget(chart)  # type: ignore[arg-type]
            charts_row.addWidget(holder, 1)
        analytics_card.body.addLayout(charts_row)

        profile_layout.addWidget(analytics_card)

        for widget in (
            self.idle_state,
            self.loading_state,
            self.empty_state,
            self.error_state,
            self.profile,
        ):
            self.stack.addWidget(widget)
        self.stack.setCurrentWidget(self.idle_state)

        shortcut(self, "Ctrl+K", self.focus_search)

    # -- lifecycle ---------------------------------------------------------
    def on_shown(self) -> None:
        super().on_shown()
        settings = self.context.config.settings.search
        self.search.set_behavior(settings.behavior, settings.search_delay_ms)
        self.search.set_history(
            self.context.workspace.recent_terms(limit=settings.max_history)
        )
        if self.stack.currentWidget() is self.idle_state:
            self.focus_search()

    def focus_search(self) -> None:
        self.search.focus()

    def clear(self) -> None:
        self._matches = []
        self._current = None
        self._last_query = ""
        self.stack.setCurrentWidget(self.idle_state)

    # -- search ------------------------------------------------------------
    def perform_search(self, query: str) -> None:
        query = (query or "").strip()
        if not query:
            self.clear()
            return
        self._last_query = query
        self.search.set_text(query)

        if not self.context.database.is_open:
            self.error_state.configure(
                "Database not available",
                "The Bin-Tel database is not open yet. Install or verify it from the "
                "Database page, then try again.",
                retryable=False,
            )
            self.stack.setCurrentWidget(self.error_state)
            return

        self.stack.setCurrentWidget(self.loading_state)
        self.loading_state.set_message(f"Searching for “{query}”…")
        worker = BankSearchWorker(self.context.banks, query, limit=40)
        worker.signals.result.connect(self._on_result)
        worker.signals.failed.connect(self._on_failed)
        run_in_background(worker)

    def _on_result(self, result: BankLookupResult) -> None:
        if not result.found:
            self.empty_state.configure(
                "No institution found",
                f"Nothing in your database matches “{result.query}”. Try a shorter name "
                "or a different match mode.",
                action_text="Clear search",
            )
            self.stack.setCurrentWidget(self.empty_state)
            return
        self._matches = list(result.matches)
        settings = self.context.config.settings
        self.context.workspace.record_search(
            result.query,
            enabled=settings.privacy.remember_search_history,
            keep=settings.search.max_history,
            result_count=len(result.matches),
            elapsed_ms=result.elapsed_ms,
        )
        self.select_institution(self._matches[0].id)

    def _on_failed(self, exc: BaseException) -> None:
        if isinstance(exc, ValidationError):
            self.search.set_error(True)
            self.empty_state.configure(
                "Search term too short", exc.message, action_text="Clear search"
            )
            self.stack.setCurrentWidget(self.empty_state)
            return
        from app.core.errors import friendly_message, friendly_title, is_retryable

        self.error_state.configure(
            friendly_title(exc), friendly_message(exc), retryable=is_retryable(exc)
        )
        self.stack.setCurrentWidget(self.error_state)

    # -- institution -------------------------------------------------------
    def select_institution(self, institution_id: int) -> None:
        match = next((item for item in self._matches if item.id == institution_id), None)
        if match is None:
            match = self.context.banks.get(institution_id)
            if match is None:
                self.empty_state.configure(
                    "Institution not found",
                    "That institution is no longer in the database.",
                    action_text="Clear search",
                )
                self.stack.setCurrentWidget(self.empty_state)
                return
            self._matches = [match, *self._matches]

        self._current = match
        self._filters = BinFilters()
        self.result_view.show_institution(match, self._matches)
        self.stack.setCurrentWidget(self.profile)
        self._update_action_labels()

        stats_worker = InstitutionStatsWorker(self.context.banks, match.id)
        stats_worker.signals.result.connect(self.result_view.show_stats)
        run_in_background(stats_worker)

        # The portfolio spans related institutions, history and ranges, so it
        # is counted separately from the per-institution statistics.
        portfolio_worker: Worker = Worker(
            lambda identifier=match.id: self.context.banks.portfolio(identifier)
        )
        portfolio_worker.signals.result.connect(self.result_view.show_portfolio)
        run_in_background(portfolio_worker)

        options_worker = FilterOptionsWorker(self.context.banks, match.id)
        options_worker.signals.result.connect(self.result_view.set_filter_options)
        run_in_background(options_worker)

        analytics_worker: Worker = Worker(
            self.context.analytics.institution_analytics, match.id
        )
        analytics_worker.signals.result.connect(self._apply_analytics)
        run_in_background(analytics_worker)

        self._load_page(match.id, self.result_view.current_request(1))

    def _apply_analytics(self, distributions: dict) -> None:
        for name, chart in self.charts.items():
            chart.set_distribution(distributions.get(name))  # type: ignore[attr-defined]

    def _load_page(self, institution_id: int, request: PageRequest) -> None:
        worker = BinPageWorker(self.context.banks, institution_id, request, self._filters)
        worker.signals.result.connect(self._on_page)
        worker.signals.failed.connect(self._on_failed)
        run_in_background(worker)

    def _on_page(self, page: Page[BinRow]) -> None:
        self.result_view.show_page(page)

    def _on_filters_changed(self, institution_id: int, filters: BinFilters) -> None:
        self._filters = filters
        self._load_page(institution_id, self.result_view.current_request(1))

    # -- actions -----------------------------------------------------------
    def _update_action_labels(self) -> None:
        if self._current is None:
            return
        uid = self._institution_uid()
        is_favorite = self.context.workspace.is_favorite(FavoriteKind.INSTITUTION, uid)
        self.favorite_button.setText(
            "Remove from favourites" if is_favorite else "Add to favourites"
        )
        watched = self.context.watchlists.is_watched(WatchTargetType.INSTITUTION, uid)
        self.watch_button.setText("On a watchlist" if watched else "Add to watchlist")

    def _institution_uid(self) -> str:
        """The stable uid a watchlist or favourite references."""
        if self._current is None:
            return ""
        uid = self.context.institutions.uid_for(self._current.id)
        return uid or str(self._current.id)

    def _add_to_watchlist(self) -> None:
        if self._current is None:
            return
        AddToWatchlistDialog.add(
            self,
            self.context,
            WatchTargetType.INSTITUTION,
            self._institution_uid(),
            self._current.display_name,
            on_added=lambda name: (self.toast(f"Added to “{name}”"), self._update_action_labels()),
        )

    def _toggle_favorite(self) -> None:
        if self._current is None:
            return
        added = self.context.workspace.toggle_favorite(
            FavoriteKind.INSTITUTION,
            self._institution_uid(),
            self._current.display_name,
            self._current.country.display_name if self._current.country else "",
        )
        self._update_action_labels()
        self.toast("Added to favourites" if added else "Removed from favourites")

    def _generate_report(self) -> None:
        if self._current is None:
            return
        institution = self._current

        def build(rows: list[BinRow]) -> None:
            stats = self.context.banks.stats(institution.id)
            content = self.context.reports.build_institution_report(
                institution,
                rows,
                stats=stats.model_dump(),
                database_version=self.context.database_version(),
            )
            chosen = ExportDialog.choose(
                self,
                institution.display_name,
                title="Institution profile report",
                subtitle=f"Export the profile and {len(rows):,} BIN record(s).",
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
            self.context.workspace.record_report(
                content.title,
                content.report_type.value,
                report_format.value,
                str(result.path),
                row_count=result.row_count,
                size_bytes=result.size_bytes,
                database_version=content.database_version,
            )
            self.toast(f"Report written to {result.path.name}")

        worker = AllBinsWorker(self.context.banks, institution.id)
        worker.signals.result.connect(build)
        worker.signals.failed.connect(lambda exc: self.show_error(exc))
        run_in_background(worker)

    def _export_table(self, institution_id: int, selection_only: bool) -> None:
        rows = self.result_view.selected_rows() if selection_only else None
        if selection_only and not rows:
            self.toast("Select one or more rows first")
            return
        if rows is not None:
            self._write_rows(rows)
            return
        worker = AllBinsWorker(self.context.banks, institution_id)
        worker.signals.result.connect(self._write_rows)
        worker.signals.failed.connect(lambda exc: self.show_error(exc))
        run_in_background(worker)

    def _write_rows(self, rows: list[BinRow]) -> None:
        if self._current is None or not rows:
            self.toast("There is nothing to export")
            return
        chosen = ExportDialog.choose(
            self,
            self._current.display_name,
            title="Export BIN records",
            subtitle=f"Export {len(rows):,} record(s).",
        )
        if chosen is None:
            return
        path, fmt = chosen
        try:
            self.context.exports.export_rows(
                rows, path, fmt, title=f"{self._current.display_name} — BIN records"
            )
        except Exception as exc:  # noqa: BLE001 - shown in a dialog
            self.show_error(exc)
            return
        self.toast(f"Exported {len(rows):,} record(s)")

    def on_theme_changed(self) -> None:
        self.search.refresh_icon()
        for state in (self.idle_state, self.empty_state, self.error_state):
            state.refresh_icon()
        self.result_view.refresh_theme()
        for chart in self.charts.values():
            chart.update()  # type: ignore[attr-defined]
