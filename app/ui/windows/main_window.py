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
from app.ui.pages.bank_lookup_page import BankLookupPage
from app.ui.pages.base_page import BasePage
from app.ui.pages.bin_lookup_page import BinLookupPage
from app.ui.pages.dashboard_page import DashboardPage
from app.ui.pages.database_page import DatabasePage
from app.ui.pages.settings_page import SettingsPage
from app.ui.pages.updates_page import UpdatesPage
from app.ui.themes.icons import IconProvider
from app.ui.themes.theme_manager import ThemeManager
from app.ui.widgets.header import AppHeader
from app.ui.widgets.sidebar import Sidebar
from app.ui.widgets.toast import Toast
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
            DatabasePage,
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

        # -- status bar -------------------------------------------------------
        status = QStatusBar(self)
        status.setSizeGripEnabled(True)
        self.setStatusBar(status)

        self.status_label = QLabel("", status)
        status.addWidget(self.status_label, 1)

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
        if message:
            QTimer.singleShot(timeout_ms, lambda: self.status_label.setText(""))

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
        for page in self.pages.values():
            page.on_database_changed()

    # -- updates -----------------------------------------------------------
    def _maybe_check_for_updates(self) -> None:
        """Silent, scheduled check — never blocks and never nags on failure."""
        config = self.context.config
        if not config.settings.database.automatic_updates:
            return
        if not config.is_update_check_due():
            return
        logger.info("Running the scheduled update check")
        self.updates_page.check_for_updates(silent=True)

    # -- shortcuts ---------------------------------------------------------
    def _install_shortcuts(self) -> None:
        shortcut(self, "Ctrl+K", self.focus_search)
        shortcut(self, "Ctrl+F", self.focus_search)
        shortcut(self, "Ctrl+1", lambda: self.navigate("dashboard"))
        shortcut(self, "Ctrl+2", lambda: self.navigate("bin_lookup"))
        shortcut(self, "Ctrl+3", lambda: self.navigate("bank_lookup"))
        shortcut(self, "Ctrl+4", lambda: self.navigate("database"))
        shortcut(self, "Ctrl+5", lambda: self.navigate("updates"))
        shortcut(self, "Ctrl+,", lambda: self.navigate("settings"))
        shortcut(self, "Ctrl+B", self.toggle_sidebar)
        shortcut(self, "Ctrl+T", self.cycle_theme)
        shortcut(self, "F5", self._refresh_current)
        shortcut(self, QKeySequence.StandardKey.Quit.name, self.close)

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
