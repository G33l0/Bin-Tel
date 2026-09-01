"""The Bin-Tel main window: header, sidebar, page stack and status bar."""

from __future__ import annotations

from PyQt6.QtCore import QByteArray, Qt, QTimer
from PyQt6.QtGui import QCloseEvent, QKeySequence
from PyQt6.QtWidgets import (
    QLabel,
    QMainWindow,
    QMenu,
    QStackedWidget,
    QStatusBar,
    QSystemTrayIcon,
    QWidget,
)

from app.core.config import LookupMode, SidebarBehavior
from app.core.constants import APP_NAME, APP_VERSION
from app.core.context import AppContext
from app.core.logging_config import get_logger
from app.ui.dialogs.confirm_dialog import ConfirmDialog
from app.ui.pages.about_page import AboutPage
from app.ui.pages.admin_page import DatabaseAdminPage
from app.ui.pages.analytics_page import AnalyticsPage
from app.ui.pages.bank_lookup_page import BankLookupPage
from app.ui.pages.base_page import BasePage
from app.ui.pages.bin_lookup_page import BinLookupPage
from app.ui.pages.dashboard_page import DashboardPage
from app.ui.pages.database_page import DatabasePage
from app.ui.pages.institution_page import InstitutionIntelligencePage
from app.ui.pages.reports_page import ReportsPage
from app.ui.pages.settings_page import SettingsPage
from app.ui.pages.updates_page import UpdatesPage
from app.ui.pages.watchlists_page import WatchlistsPage
from app.ui.themes.icons import IconProvider
from app.ui.themes.theme_manager import ThemeManager
from app.ui.widgets.command_palette import Command, CommandPalette, PaletteResult, ResultKind
from app.ui.widgets.header import AppHeader
from app.ui.widgets.sidebar import NAV_ITEMS, Sidebar
from app.ui.widgets.toast import Toast
from app.workers.base import Worker, run_in_background
from app.utils.qt_helpers import hbox, shortcut, vbox
from app.utils.validators import looks_like_bin

logger = get_logger(__name__)

DEFAULT_SIZE = (1320, 860)
MINIMUM_SIZE = (1040, 680)


class MainWindow(QMainWindow):
    """Assembles the application shell and routes navigation between pages."""

    def __init__(self, context: AppContext, themes: ThemeManager) -> None:
        super().__init__()
        self.context = context
        self.themes = themes
        self._tray: QSystemTrayIcon | None = None
        self._force_close = False

        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(IconProvider.instance().app_icon())
        self.setMinimumSize(*MINIMUM_SIZE)
        self.resize(*DEFAULT_SIZE)

        central = QWidget(self)
        layout = vbox(central)
        self.setCentralWidget(central)

        # -- header ---------------------------------------------------------
        self.header = AppHeader(central)
        self.header.search_requested.connect(self._on_global_search)
        self.header.theme_cycle_requested.connect(self.cycle_theme)
        self.header.settings_requested.connect(lambda: self.navigate("settings"))
        self.header.sidebar_toggle_requested.connect(self.toggle_sidebar)
        layout.addWidget(self.header)

        # -- body -----------------------------------------------------------
        body = QWidget(central)
        body_layout = hbox(body)
        layout.addWidget(body, 1)

        state = context.config.state
        appearance = context.config.settings.appearance
        collapsed = {
            SidebarBehavior.EXPANDED: False,
            SidebarBehavior.COLLAPSED: True,
            SidebarBehavior.REMEMBER: state.sidebar_collapsed,
        }[appearance.sidebar_behavior]

        self.sidebar = Sidebar(body, collapsed=collapsed)
        self.sidebar.navigated.connect(self.navigate)
        self.sidebar.collapse_toggled.connect(self._on_sidebar_toggled)
        body_layout.addWidget(self.sidebar)

        self.stack = QStackedWidget(body)
        body_layout.addWidget(self.stack, 1)

        # -- pages -----------------------------------------------------------
        self.pages: dict[str, BasePage] = {}
        for page_class in (
            DashboardPage,
            BinLookupPage,
            BankLookupPage,
            InstitutionIntelligencePage,
            AnalyticsPage,
            WatchlistsPage,
            ReportsPage,
            DatabasePage,
            DatabaseAdminPage,
            UpdatesPage,
            SettingsPage,
            AboutPage,
        ):
            page = page_class(context, self.stack)
            page.navigation_requested.connect(self.navigate)
            page.status_message.connect(self.show_status)
            page.toast_requested.connect(self.show_toast)
            self.pages[page.key] = page
            self.stack.addWidget(page)

        settings_page = self.settings_page
        settings_page.appearance_changed.connect(self.apply_appearance)
        settings_page.database_path_changed.connect(self.on_database_changed)
        settings_page.search_settings_changed.connect(self._apply_search_settings)
        settings_page.set_theme_catalogue(self.themes.themes)

        self.updates_page.navigation_requested.connect(self._handle_special_navigation)
        self.admin_page.navigation_requested.connect(self._handle_special_navigation)

        # -- command palette -------------------------------------------------
        self.palette = CommandPalette(self)
        self.palette.set_pages(
            [(item.key, item.label, item.tooltip) for item in NAV_ITEMS]
        )
        self.palette.set_commands(self._build_commands())
        self.palette.set_result_provider(self._palette_search)
        self.palette.result_chosen.connect(self._on_palette_result)

        # -- status bar -------------------------------------------------------
        status = QStatusBar(self)
        status.setSizeGripEnabled(True)
        self.setStatusBar(status)

        self.status_label = QLabel("", status)
        status.addWidget(self.status_label, 1)

        self._status_timer = QTimer(self)
        self._status_timer.setSingleShot(True)
        self._status_timer.timeout.connect(lambda: self.status_label.setText(""))

        self.database_label = QLabel("", status)
        self.database_label.setProperty("role", "muted")
        status.addPermanentWidget(self.database_label)

        self._install_shortcuts()
        self._restore_geometry()
        self._configure_tray()

        start_page = state.active_page if state.active_page in self.pages else "dashboard"
        self.navigate(start_page)
        self.themes.theme_changed.connect(lambda _: self.on_theme_changed())
        self.refresh_database_status()

        self.refresh_alert_badges()

        QTimer.singleShot(1500, self._maybe_check_for_updates)

    # -- convenience accessors --------------------------------------------
    @property
    def settings_page(self) -> SettingsPage:
        return self.pages["settings"]  # type: ignore[return-value]

    @property
    def updates_page(self) -> UpdatesPage:
        return self.pages["updates"]  # type: ignore[return-value]

    @property
    def bin_page(self) -> BinLookupPage:
        return self.pages["bin_lookup"]  # type: ignore[return-value]

    @property
    def bank_page(self) -> BankLookupPage:
        return self.pages["bank_lookup"]  # type: ignore[return-value]

    @property
    def admin_page(self) -> DatabaseAdminPage:
        return self.pages["admin"]  # type: ignore[return-value]

    @property
    def watchlists_page(self) -> WatchlistsPage:
        return self.pages["watchlists"]  # type: ignore[return-value]

    # -- navigation --------------------------------------------------------
    def navigate(self, key: str) -> None:
        """Switch pages. ``bank_lookup:42`` also opens that institution."""
        target, _, argument = key.partition(":")
        page = self.pages.get(target)
        if page is None:
            return
        self.sidebar.select(target)
        self.stack.setCurrentWidget(page)
        page.on_shown()
        self.context.config.state.active_page = target
        self.setWindowTitle(f"{APP_NAME} — {page.title}" if page.title else APP_NAME)

        if target == "bank_lookup" and argument.isdigit():
            self.bank_page.select_institution(int(argument))
        elif target == "institutions" and argument.isdigit():
            self.pages["institutions"].select_institution(int(argument))  # type: ignore[attr-defined]

    def _handle_special_navigation(self, key: str) -> None:
        if key == "__database_reloaded__":
            self.on_database_changed()

    def _on_global_search(self, query: str, mode: LookupMode) -> None:
        """The header's search field routes to whichever page fits the query."""
        if mode is LookupMode.BIN or looks_like_bin(query):
            self.navigate("bin_lookup")
            self.bin_page.perform_search(query)
        else:
            self.navigate("bank_lookup")
            self.bank_page.perform_search(query)
        self.header.search.field.clear()
        self.refresh_alert_badges()

    def focus_search(self) -> None:
        current = self.stack.currentWidget()
        if isinstance(current, BinLookupPage | BankLookupPage):
            current.focus_search()
            return
        default = self.context.config.settings.search.default_lookup
        self.navigate("bin_lookup" if default is LookupMode.BIN else "bank_lookup")
        page = self.stack.currentWidget()
        if isinstance(page, BinLookupPage | BankLookupPage):
            page.focus_search()

    # -- appearance --------------------------------------------------------
    def toggle_sidebar(self) -> None:
        self.sidebar.toggle_collapsed()

    def _on_sidebar_toggled(self, collapsed: bool) -> None:
        self.context.config.state.sidebar_collapsed = collapsed
        self.context.config.save_state()

    def cycle_theme(self) -> None:
        theme = self.themes.next_theme()
        self.show_toast(f"Theme: {theme.display_name}")
        self.settings_page.load()

    def apply_appearance(self) -> None:
        """Re-apply theme, scale and compact mode after a settings change."""
        self.themes.apply(self.context.config.settings.appearance.theme, persist=False)
        behavior = self.context.config.settings.appearance.sidebar_behavior
        if behavior is SidebarBehavior.EXPANDED:
            self.sidebar.set_collapsed(False)
        elif behavior is SidebarBehavior.COLLAPSED:
            self.sidebar.set_collapsed(True)

    def on_theme_changed(self) -> None:
        self.header.refresh_theme()
        self.sidebar.refresh_icons()
        self.palette.refresh_theme()
        self.setWindowIcon(IconProvider.instance().app_icon())
        for page in self.pages.values():
            page.on_theme_changed()

    def _apply_search_settings(self) -> None:
        settings = self.context.config.settings.search
        self.bin_page.set_search_behavior(settings.behavior, settings.search_delay_ms)
        self.bank_page.search.set_behavior(settings.behavior, settings.search_delay_ms)
        self.bank_page.result_view.table.set_page_size(settings.results_per_page)

    # -- status ------------------------------------------------------------
    def show_status(self, message: str, timeout_ms: int = 6000) -> None:
        self.status_label.setText(message)
        # The timer is a child of the window, so closing the window destroys it
        # rather than leaving it to fire into a deleted label.
        self._status_timer.stop()
        if message:
            self._status_timer.start(timeout_ms)

    def show_toast(self, message: str) -> None:
        Toast.show_message(self, message)

    def refresh_database_status(self) -> None:
        if not self.context.database.is_open:
            self.database_label.setText("Database: not installed")
            return
        info = self.context.stats.info()
        self.database_label.setText(
            f"Database {info.version or 'unknown'} · {info.stats.bins:,} BINs"
        )

    def on_database_changed(self) -> None:
        self.refresh_database_status()
        self.refresh_alert_badges()
        for page in self.pages.values():
            page.on_database_changed()
        unread = self.context.watchlists.unread_count()
        if unread:
            self.show_toast(f"{unread} watchlist alert(s) after this update")

    # -- updates -----------------------------------------------------------
    def _maybe_check_for_updates(self) -> None:
        """Silent, scheduled check — never blocks and never nags on failure."""
        config = self.context.config
        database = config.settings.database
        if not database.automatic_updates:
            return
        from app.core.config import UpdateCheckMode

        if database.check_mode is UpdateCheckMode.MANUAL:
            return
        due = config.is_update_check_due() or config.should_check_on_startup()
        if not due:
            return
        logger.info("Running the scheduled update check")
        self.updates_page.check_for_updates(silent=True)

    # -- shortcuts ---------------------------------------------------------
    def _install_shortcuts(self) -> None:
        shortcut(self, "Ctrl+K", self.open_palette)
        shortcut(self, "Ctrl+P", self.open_palette)
        shortcut(self, "Ctrl+F", self.focus_search)
        shortcut(self, "Ctrl+1", lambda: self.navigate("dashboard"))
        shortcut(self, "Ctrl+2", lambda: self.navigate("bin_lookup"))
        shortcut(self, "Ctrl+3", lambda: self.navigate("bank_lookup"))
        shortcut(self, "Ctrl+4", lambda: self.navigate("institutions"))
        shortcut(self, "Ctrl+5", lambda: self.navigate("analytics"))
        shortcut(self, "Ctrl+6", lambda: self.navigate("watchlists"))
        shortcut(self, "Ctrl+7", lambda: self.navigate("reports"))
        shortcut(self, "Ctrl+D", lambda: self.navigate("database"))
        shortcut(self, "Ctrl+U", lambda: self.navigate("updates"))
        shortcut(self, "Ctrl+,", lambda: self.navigate("settings"))
        shortcut(self, "Ctrl+B", self.toggle_sidebar)
        shortcut(self, "Ctrl+T", self.cycle_theme)
        shortcut(self, "F5", self._refresh_current)
        shortcut(self, QKeySequence.StandardKey.Quit.name, self.close)

    # -- command palette ----------------------------------------------------
    def _build_commands(self) -> list[Command]:
        return [
            Command("cycle_theme", "Switch theme", "Cycle the Bin-Tel themes", ("appearance", "dark", "light")),
            Command("toggle_sidebar", "Toggle the sidebar", "Collapse or expand navigation", ("hide",)),
            Command("check_updates", "Check for database updates", "", ("update", "download")),
            Command("verify_database", "Verify the database", "", ("integrity", "health", "check")),
            Command("create_backup", "Create a database backup", "", ("snapshot", "save")),
            Command("scan_watchlists", "Check watchlists for changes", "", ("alerts", "diff")),
            Command("new_report", "Build a report", "", ("export", "pdf", "csv")),
            Command("open_data_folder", "Open the application data folder", "", ("files", "storage")),
        ]

    def open_palette(self) -> None:
        """Ctrl+K — the global search and command surface."""
        static: list[PaletteResult] = []
        for saved in self.context.workspace.saved_searches()[:8]:
            static.append(
                PaletteResult(
                    ResultKind.SAVED_SEARCH,
                    str(saved.id),
                    saved.name,
                    saved.subtitle,
                    rank=20,
                )
            )
        for term in self.context.workspace.recent_terms(limit=6):
            static.append(
                PaletteResult(ResultKind.RECENT, term, term, "Recent search", rank=30)
            )
        self.palette.set_static_results(static)
        self.palette.refresh_theme()
        self.palette.open_palette()

    def _palette_search(self, term: str) -> None:
        """Fetch palette suggestions off the GUI thread."""
        if not self.context.database.is_open:
            return
        worker: Worker = Worker(self.context.search.suggest, term, 8)
        worker.signals.result.connect(self._on_palette_results)
        run_in_background(worker)

    def _on_palette_results(self, suggestions: list) -> None:
        results = []
        for kind, value, label in suggestions:
            if kind == "bin":
                results.append(
                    PaletteResult(ResultKind.BIN, value, label, "Open this BIN record", rank=10)
                )
            else:
                results.append(
                    PaletteResult(
                        ResultKind.INSTITUTION, value, label, "Open this institution", rank=12
                    )
                )
        self.palette.set_results(results)

    def _on_palette_result(self, result: PaletteResult) -> None:
        if result.kind is ResultKind.BIN:
            self.navigate("bin_lookup")
            self.bin_page.perform_search(result.value)
        elif result.kind is ResultKind.INSTITUTION:
            institution = (
                self.context.banks.get_by_uid(result.value)
                if result.value.startswith("inst_")
                else None
            )
            self.navigate("institutions")
            page = self.pages["institutions"]
            if institution is not None:
                page.select_institution(institution.id)  # type: ignore[attr-defined]
            else:
                page.perform_search(result.value)  # type: ignore[attr-defined]
        elif result.kind is ResultKind.RECENT:
            self._on_global_search(result.value, self.header.suggested_mode())
        elif result.kind is ResultKind.SAVED_SEARCH:
            self.navigate("bank_lookup")
            saved = next(
                (
                    item
                    for item in self.context.workspace.saved_searches()
                    if str(item.id) == result.value
                ),
                None,
            )
            if saved is not None and saved.query:
                self._on_global_search(saved.query, self.header.suggested_mode())
        elif result.kind is ResultKind.PAGE:
            self.navigate(result.value)
        elif result.kind is ResultKind.COMMAND:
            self._run_command(result.value)

    def _run_command(self, key: str) -> None:
        actions = {
            "cycle_theme": self.cycle_theme,
            "toggle_sidebar": self.toggle_sidebar,
            "check_updates": lambda: (
                self.navigate("updates"),
                self.updates_page.check_for_updates(),
            ),
            "verify_database": lambda: (self.navigate("admin"), self.admin_page._verify()),
            "create_backup": lambda: (
                self.navigate("admin"),
                self.admin_page._create_backup(),
            ),
            "scan_watchlists": lambda: (
                self.navigate("watchlists"),
                self.watchlists_page._scan(),
            ),
            "new_report": lambda: self.navigate("reports"),
            "open_data_folder": self._open_data_folder,
        }
        action = actions.get(key)
        if action is not None:
            action()

    def _open_data_folder(self) -> None:
        from app.utils.qt_helpers import reveal_in_file_manager

        reveal_in_file_manager(self.context.paths.data_dir)

    def refresh_alert_badges(self) -> None:
        """Show unread watchlist alerts on the navigation entry."""
        try:
            unread = self.context.watchlists.unread_count()
        except Exception:  # noqa: BLE001 - a badge must never break the window
            unread = 0
        self.sidebar.set_badge_count("watchlists", unread)

    def _refresh_current(self) -> None:
        page = self.stack.currentWidget()
        if isinstance(page, BasePage):
            page.refresh()
            self.show_status("Refreshed")

    # -- tray --------------------------------------------------------------
    def _configure_tray(self) -> None:
        if not self.context.config.settings.general.minimize_to_tray:
            return
        if not QSystemTrayIcon.isSystemTrayAvailable():
            logger.info("System tray is unavailable on this desktop")
            return
        tray = QSystemTrayIcon(IconProvider.instance().app_icon(), self)
        tray.setToolTip(f"{APP_NAME} {APP_VERSION}")
        menu = QMenu()
        menu.addAction("Open Bin-Tel", self._restore_from_tray)
        menu.addAction("BIN Lookup", lambda: self._restore_from_tray("bin_lookup"))
        menu.addAction("Bank Lookup", lambda: self._restore_from_tray("bank_lookup"))
        menu.addSeparator()
        menu.addAction("Quit", self._quit_from_tray)
        tray.setContextMenu(menu)
        tray.activated.connect(
            lambda reason: self._restore_from_tray()
            if reason == QSystemTrayIcon.ActivationReason.Trigger
            else None
        )
        tray.show()
        self._tray = tray

    def _restore_from_tray(self, page: str = "") -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()
        if page:
            self.navigate(page)

    def _quit_from_tray(self) -> None:
        self._force_close = True
        self.close()

    # -- geometry ------------------------------------------------------------
    def _restore_geometry(self) -> None:
        general = self.context.config.settings.general
        state = self.context.config.state
        if not (general.remember_window_size or general.remember_window_position):
            return
        if state.window_geometry:
            try:
                self.restoreGeometry(QByteArray.fromBase64(state.window_geometry.encode()))
            except (ValueError, TypeError):  # pragma: no cover - corrupted state
                logger.debug("Stored window geometry could not be restored")
        if state.window_state:
            try:
                self.restoreState(QByteArray.fromBase64(state.window_state.encode()))
            except (ValueError, TypeError):  # pragma: no cover
                logger.debug("Stored window state could not be restored")

    def _save_geometry(self) -> None:
        general = self.context.config.settings.general
        state = self.context.config.state
        if general.remember_window_size or general.remember_window_position:
            state.window_geometry = bytes(self.saveGeometry().toBase64()).decode()
            state.window_state = bytes(self.saveState().toBase64()).decode()
        else:
            state.window_geometry = ""
            state.window_state = ""
        state.sidebar_collapsed = self.sidebar.collapsed
        self.context.config.save_state()

    # -- lifecycle -----------------------------------------------------------
    def closeEvent(self, event: QCloseEvent | None) -> None:  # noqa: N802 - Qt API
        if event is None:  # pragma: no cover - defensive
            return
        general = self.context.config.settings.general

        if not self._force_close and general.minimize_to_tray and self._tray is not None:
            event.ignore()
            self.hide()
            self._tray.showMessage(
                APP_NAME,
                "Bin-Tel is still running in the notification area.",
                QSystemTrayIcon.MessageIcon.Information,
                2500,
            )
            return

        if not self._force_close and general.confirm_before_closing:
            if not ConfirmDialog.ask(
                self, f"Close {APP_NAME}?", "Any background download will be cancelled.",
                confirm_text="Close",
            ):
                event.ignore()
                return

        self._save_geometry()
        if self._tray is not None:
            self._tray.hide()
        logger.info("Main window closing")
        event.accept()
