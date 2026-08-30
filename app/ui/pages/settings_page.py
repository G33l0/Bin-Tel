"""Settings — General, Database, Appearance, Search and Advanced.

Every control writes straight through to :class:`~app.core.config.Settings`
and persists immediately, so nothing is lost if the application is closed
without an explicit save.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QLabel,
    QPushButton,
    QSlider,
    QSpinBox,
    QTabWidget,
    QWidget,
)

from app.core.config import (
    LogLevel,
    LookupMode,
    SearchBehavior,
    SidebarBehavior,
    UpdateFrequency,
)
from app.core.logging_config import set_level
from app.ui.dialogs.confirm_dialog import ConfirmDialog
from app.ui.pages.base_page import BasePage
from app.ui.widgets.cards import Card, SectionHeader
from app.ui.widgets.states import StateBanner, StateKind
from app.utils.formatting import format_bytes
from app.utils.qt_helpers import (
    expanding_spacer,
    grid,
    hbox,
    horizontal_rule,
    open_path,
    reveal_in_file_manager,
    vbox,
)


class SettingRow(QWidget):
    """A labelled setting with an explanation and its control."""

    def __init__(
        self,
        label: str,
        control: QWidget,
        description: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        layout = hbox(self, spacing=18)

        text = vbox(spacing=2)
        title = QLabel(label, self)
        title.setProperty("role", "fieldValue")
        text.addWidget(title)
        if description:
            hint = QLabel(description, self)
            hint.setProperty("role", "muted")
            hint.setWordWrap(True)
            text.addWidget(hint)
        layout.addLayout(text, 1)

        control.setAccessibleName(label)
        layout.addWidget(control, 0, Qt.AlignmentFlag.AlignTop)
        self.control = control


class SettingsSection(Card):
    """A titled group of setting rows."""

    def __init__(self, title: str, subtitle: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent, padding=18, spacing=14)
        self.body.addWidget(SectionHeader(title, subtitle, self))

    def add_setting(self, label: str, control: QWidget, description: str = "") -> QWidget:
        if self.body.count() > 1:
            self.body.addWidget(horizontal_rule(self))
        row = SettingRow(label, control, description, self)
        self.body.addWidget(row)
        return control


class SettingsPage(BasePage):
    """Persisted preferences across five sections."""

    key = "settings"
    title = "Settings"
    subtitle = "Preferences are saved as you change them."

    appearance_changed = pyqtSignal()
    database_path_changed = pyqtSignal()
    search_settings_changed = pyqtSignal()
    general_changed = pyqtSignal()

    def __init__(self, context, parent: QWidget | None = None) -> None:
        super().__init__(context, parent)
        self._loading = True

        self.banner = StateBanner("", StateKind.SUCCESS, self.surface, dismissible=True)
        self.banner.hide()
        self.content.addWidget(self.banner)

        self.tabs = QTabWidget(self.surface)
        self.tabs.setDocumentMode(True)
        self.content.addWidget(self.tabs, 1)

        self.tabs.addTab(self._build_general(), "General")
        self.tabs.addTab(self._build_database(), "Database")
        self.tabs.addTab(self._build_appearance(), "Appearance")
        self.tabs.addTab(self._build_search(), "Search")
        self.tabs.addTab(self._build_advanced(), "Advanced")

        self.load()
        self._loading = False

    # -- section builders --------------------------------------------------
    @staticmethod
    def _page() -> tuple[QWidget, Any]:
        page = QWidget()
        layout = vbox(page, margins=(2, 16, 2, 16), spacing=14)
        return page, layout

    def _build_general(self) -> QWidget:
        page, layout = self._page()
        section = SettingsSection(
            "Startup and window",
            "How Bin-Tel behaves when it opens and closes.",
            page,
        )
        self.start_with_system = section.add_setting(
            "Start with the system",
            QCheckBox(),
            "Register Bin-Tel to launch when you sign in. Applied on the next sign-in.",
        )
        self.minimize_to_tray = section.add_setting(
            "Minimise to the system tray",
            QCheckBox(),
            "Keep Bin-Tel running in the notification area instead of closing it.",
        )
        self.confirm_before_closing = section.add_setting(
            "Confirm before closing", QCheckBox(), "Ask before the window is closed."
        )
        self.remember_window_size = section.add_setting(
            "Remember window size", QCheckBox(), "Reopen at the size you last used."
        )
        self.remember_window_position = section.add_setting(
            "Remember window position", QCheckBox(), "Reopen where you last left the window."
        )
        layout.addWidget(section)
        layout.addItem(expanding_spacer(horizontal=False))

        for widget in (
            self.start_with_system,
            self.minimize_to_tray,
            self.confirm_before_closing,
            self.remember_window_size,
            self.remember_window_position,
        ):
            widget.toggled.connect(self._save_general)  # type: ignore[attr-defined]
        return page

    def _build_database(self) -> QWidget:
        page, layout = self._page()

        location = SettingsSection(
            "Database location",
            "Where the local intelligence database is stored.",
            page,
        )
        location_widget = QWidget(location)
        location_layout = hbox(location_widget, spacing=8)
        self.path_label = QLabel("", location_widget)
        self.path_label.setProperty("role", "mono")
        self.path_label.setStyleSheet("font-size: 9pt;")
        self.path_label.setWordWrap(True)
        location_layout.addWidget(self.path_label, 1)
        change_button = QPushButton("Change…", location_widget)
        change_button.clicked.connect(self._choose_database_directory)
        location_layout.addWidget(change_button)
        reset_button = QPushButton("Use default", location_widget)
        reset_button.setProperty("variant", "ghost")
        reset_button.clicked.connect(self._reset_database_directory)
        location_layout.addWidget(reset_button)
        location.body.addWidget(location_widget)
        layout.addWidget(location)

        updates = SettingsSection(
            "Updates",
            "Bin-Tel never forces an update. You decide when a new database is installed.",
            page,
        )
        self.automatic_updates = updates.add_setting(
            "Check for updates automatically",
            QCheckBox(),
            "Bin-Tel fetches a small metadata file — never the whole database — to see "
            "whether a newer one exists.",
        )
        frequency = QComboBox()
        for option in UpdateFrequency:
            frequency.addItem(option.label, option.value)
        self.update_frequency = updates.add_setting(
            "Update frequency", frequency, "How often that check runs."
        )
        self.download_automatically = updates.add_setting(
            "Download updates automatically",
            QCheckBox(),
            "Start the download as soon as a newer database is found.",
        )
        self.install_automatically = updates.add_setting(
            "Install updates automatically",
            QCheckBox(),
            "Activate a downloaded database once it has passed verification.",
        )
        self.backup_before_update = updates.add_setting(
            "Back up before updating",
            QCheckBox(),
            "Keep a snapshot so a failed update can be rolled back automatically.",
        )
        retention = QSpinBox()
        retention.setRange(1, 20)
        self.max_backups = updates.add_setting(
            "Backups to keep", retention, "Older snapshots are pruned automatically."
        )

        check_now = QPushButton("Check for Updates Now", updates)
        check_now.setProperty("variant", "primary")
        check_now.clicked.connect(lambda: self.navigate("updates"))
        updates.body.addWidget(check_now, 0, Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(updates)

        source = SettingsSection(
            "Database source",
            "The distribution endpoint Bin-Tel asks for new database packages.",
            page,
        )
        source_widget = QWidget(source)
        source_layout = hbox(source_widget, spacing=8)
        from PyQt6.QtWidgets import QLineEdit

        self.manifest_url = QLineEdit(source_widget)
        self.manifest_url.setAccessibleName("Database manifest URL")
        self.manifest_url.editingFinished.connect(self._save_manifest_url)
        source_layout.addWidget(self.manifest_url, 1)
        browse = QPushButton("Use a local package…", source_widget)
        browse.clicked.connect(self._choose_local_manifest)
        source_layout.addWidget(browse)
        source.body.addWidget(source_widget)
        layout.addWidget(source)

        layout.addItem(expanding_spacer(horizontal=False))

        self.automatic_updates.toggled.connect(self._save_database)  # type: ignore[attr-defined]
        self.update_frequency.currentIndexChanged.connect(self._save_database)  # type: ignore[attr-defined]
        for widget in (
            self.download_automatically,
            self.install_automatically,
            self.backup_before_update,
        ):
            widget.toggled.connect(self._save_database)  # type: ignore[attr-defined]
        self.max_backups.valueChanged.connect(self._save_database)  # type: ignore[attr-defined]
        return page

    def _build_appearance(self) -> QWidget:
        page, layout = self._page()
        section = SettingsSection(
            "Appearance", "Themes apply immediately — no restart needed.", page
        )

        theme = QComboBox()
        theme.setMinimumWidth(220)
        self.theme_combo = section.add_setting(
            "Theme", theme, "Five complete designs, each with its own palette."
        )

        scale_widget = QWidget(section)
        scale_layout = hbox(scale_widget, spacing=10)
        self.scale_slider = QSlider(Qt.Orientation.Horizontal, scale_widget)
        self.scale_slider.setRange(75, 200)
        self.scale_slider.setSingleStep(5)
        self.scale_slider.setPageStep(10)
        self.scale_slider.setTickInterval(25)
        self.scale_slider.setTickPosition(QSlider.TickPosition.TicksBelow)
        self.scale_slider.setMinimumWidth(180)
        scale_layout.addWidget(self.scale_slider)
        self.scale_value = QLabel("100%", scale_widget)
        self.scale_value.setProperty("role", "muted")
        self.scale_value.setMinimumWidth(46)
        scale_layout.addWidget(self.scale_value)
        section.add_setting(
            "Interface scale", scale_widget, "Enlarge or reduce every element of the interface."
        )

        self.compact_mode = section.add_setting(
            "Compact mode", QCheckBox(), "Tighter spacing, useful on smaller displays."
        )

        sidebar = QComboBox()
        for option in SidebarBehavior:
            sidebar.addItem(option.label, option.value)
        self.sidebar_behavior = section.add_setting(
            "Sidebar", sidebar, "Whether the navigation rail starts expanded or collapsed."
        )

        layout.addWidget(section)

        preview = SettingsSection("Theme preview", parent=page)
        self.theme_description = QLabel("", preview)
        self.theme_description.setProperty("role", "pageSubtitle")
        self.theme_description.setWordWrap(True)
        preview.body.addWidget(self.theme_description)
        layout.addWidget(preview)
        layout.addItem(expanding_spacer(horizontal=False))

        self.theme_combo.currentIndexChanged.connect(self._save_appearance)  # type: ignore[attr-defined]
        self.scale_slider.valueChanged.connect(self._on_scale_changed)
        self.scale_slider.sliderReleased.connect(self._save_appearance)
        self.compact_mode.toggled.connect(self._save_appearance)  # type: ignore[attr-defined]
        self.sidebar_behavior.currentIndexChanged.connect(self._save_appearance)  # type: ignore[attr-defined]
        return page

    def _build_search(self) -> QWidget:
        page, layout = self._page()
        section = SettingsSection("Search", "How the lookup surfaces behave.", page)

        default_lookup = QComboBox()
        for option in LookupMode:
            default_lookup.addItem(option.label, option.value)
        self.default_lookup = section.add_setting(
            "Default lookup", default_lookup, "Which page Ctrl+K takes you to."
        )

        behavior = QComboBox()
        for option in SearchBehavior:
            behavior.addItem(option.label, option.value)
        self.search_behavior = section.add_setting(
            "Search behaviour", behavior, "Search on every keystroke, or only when you press Enter."
        )

        delay = QSpinBox()
        delay.setRange(0, 2000)
        delay.setSingleStep(50)
        delay.setSuffix(" ms")
        self.search_delay = section.add_setting(
            "Search-as-you-type delay", delay, "How long Bin-Tel waits before searching."
        )

        per_page = QSpinBox()
        per_page.setRange(10, 500)
        per_page.setSingleStep(10)
        self.results_per_page = section.add_setting(
            "Results per page", per_page, "Large result sets are always paginated."
        )

        history = QSpinBox()
        history.setRange(0, 200)
        self.max_history = section.add_setting(
            "Recent searches to remember", history, "Set to 0 to keep no history."
        )

        clear_history = QPushButton("Clear search history", section)
        clear_history.setProperty("variant", "ghost")
        clear_history.clicked.connect(self._clear_history)
        section.body.addWidget(clear_history, 0, Qt.AlignmentFlag.AlignLeft)

        layout.addWidget(section)
        layout.addItem(expanding_spacer(horizontal=False))

        self.default_lookup.currentIndexChanged.connect(self._save_search)  # type: ignore[attr-defined]
        self.search_behavior.currentIndexChanged.connect(self._save_search)  # type: ignore[attr-defined]
        self.search_delay.valueChanged.connect(self._save_search)  # type: ignore[attr-defined]
        self.results_per_page.valueChanged.connect(self._save_search)  # type: ignore[attr-defined]
        self.max_history.valueChanged.connect(self._save_search)  # type: ignore[attr-defined]
        return page

    def _build_advanced(self) -> QWidget:
        page, layout = self._page()

        logging_section = SettingsSection(
            "Logging", "Bin-Tel never logs cardholder data or credentials.", page
        )
        level = QComboBox()
        for option in LogLevel:
            level.addItem(option.value.title(), option.value)
        self.log_level = logging_section.add_setting(
            "Logging level", level, "Raise this only when diagnosing a problem."
        )
        retention = QSpinBox()
        retention.setRange(1, 365)
        retention.setSuffix(" days")
        self.log_retention = logging_section.add_setting(
            "Keep logs for", retention, "Older log files are rotated away."
        )
        self.verify_on_startup = logging_section.add_setting(
            "Verify the database on startup",
            QCheckBox(),
            "Runs a fast integrity check each time Bin-Tel opens.",
        )
        layout.addWidget(logging_section)

        tools = SettingsSection("Maintenance", parent=page)
        tools_widget = QWidget(tools)
        tools_grid = grid(tools_widget, spacing=10)
        for index, (label, handler) in enumerate(
            (
                ("Verify database", lambda: self.navigate("database")),
                ("Open application data folder", self._open_data_folder),
                ("Open log folder", self._open_log_folder),
                ("Clear cache", self._clear_cache),
                ("Reset application settings", self._reset_settings),
            )
        ):
            button = QPushButton(label, tools_widget)
            button.setProperty("variant", "danger" if "Reset" in label else "")
            button.clicked.connect(handler)
            tools_grid.addWidget(button, index // 3, index % 3)
        tools.body.addWidget(tools_widget)

        self.storage_label = QLabel("", tools)
        self.storage_label.setProperty("role", "muted")
        self.storage_label.setWordWrap(True)
        tools.body.addWidget(self.storage_label)
        layout.addWidget(tools)
        layout.addItem(expanding_spacer(horizontal=False))

        self.log_level.currentIndexChanged.connect(self._save_advanced)  # type: ignore[attr-defined]
        self.log_retention.valueChanged.connect(self._save_advanced)  # type: ignore[attr-defined]
        self.verify_on_startup.toggled.connect(self._save_advanced)  # type: ignore[attr-defined]
        return page

    # -- load / save ------------------------------------------------------
    def load(self) -> None:
        self._loading = True
        settings = self.context.config.settings

        general = settings.general
        self.start_with_system.setChecked(general.start_with_system)
        self.minimize_to_tray.setChecked(general.minimize_to_tray)
        self.confirm_before_closing.setChecked(general.confirm_before_closing)
        self.remember_window_size.setChecked(general.remember_window_size)
        self.remember_window_position.setChecked(general.remember_window_position)

        database = settings.database
        self.automatic_updates.setChecked(database.automatic_updates)
        self._select_data(self.update_frequency, database.update_frequency.value)
        self.download_automatically.setChecked(database.download_automatically)
        self.install_automatically.setChecked(database.install_automatically)
        self.backup_before_update.setChecked(database.backup_before_update)
        self.max_backups.setValue(database.max_backups)
        self.manifest_url.setText(database.manifest_url)
        self.path_label.setText(str(self.context.database.path))

        appearance = settings.appearance
        self._populate_themes()
        self._select_data(self.theme_combo, appearance.theme)
        self.scale_slider.setValue(appearance.ui_scale)
        self.scale_value.setText(f"{appearance.ui_scale}%")
        self.compact_mode.setChecked(appearance.compact_mode)
        self._select_data(self.sidebar_behavior, appearance.sidebar_behavior.value)

        search = settings.search
        self._select_data(self.default_lookup, search.default_lookup.value)
        self._select_data(self.search_behavior, search.behavior.value)
        self.search_delay.setValue(search.search_delay_ms)
        self.results_per_page.setValue(search.results_per_page)
        self.max_history.setValue(search.max_history)

        advanced = settings.advanced
        self._select_data(self.log_level, advanced.log_level.value)
        self.log_retention.setValue(advanced.log_retention_days)
        self.verify_on_startup.setChecked(advanced.verify_on_startup)

        self._update_storage_summary()
        self._loading = False

    def _populate_themes(self) -> None:
        self.theme_combo.blockSignals(True)
        self.theme_combo.clear()
        themes = getattr(self, "_theme_catalogue", None)
        if themes is None:
            from app.ui.themes.palette import BUILTIN_THEMES

            themes = list(BUILTIN_THEMES)
        for theme in themes:
            self.theme_combo.addItem(theme.display_name, theme.name)
        self.theme_combo.blockSignals(False)

    def set_theme_catalogue(self, themes: list) -> None:
        """Injected by the main window so custom themes appear here too."""
        self._theme_catalogue = themes
        current = self.context.config.settings.appearance.theme
        self._populate_themes()
        self._select_data(self.theme_combo, current)
        self._update_theme_description()

    @staticmethod
    def _select_data(combo: QComboBox, value: object) -> None:
        index = combo.findData(value)
        combo.setCurrentIndex(index if index >= 0 else 0)

    def _persist(self, message: str = "") -> None:
        self.context.config.save_settings()
        if message:
            self.banner.show_message(message, StateKind.SUCCESS)

    def _save_general(self) -> None:
        if self._loading:
            return
        general = self.context.config.settings.general
        general.start_with_system = self.start_with_system.isChecked()
        general.minimize_to_tray = self.minimize_to_tray.isChecked()
        general.confirm_before_closing = self.confirm_before_closing.isChecked()
        general.remember_window_size = self.remember_window_size.isChecked()
        general.remember_window_position = self.remember_window_position.isChecked()
        self._persist()
        self.general_changed.emit()

    def _save_database(self) -> None:
        if self._loading:
            return
        database = self.context.config.settings.database
        database.automatic_updates = self.automatic_updates.isChecked()
        database.update_frequency = UpdateFrequency(self.update_frequency.currentData())
        database.download_automatically = self.download_automatically.isChecked()
        database.install_automatically = self.install_automatically.isChecked()
        database.backup_before_update = self.backup_before_update.isChecked()
        database.max_backups = int(self.max_backups.value())
        self._persist()
        self.context.apply_settings()

    def _save_manifest_url(self) -> None:
        if self._loading:
            return
        value = self.manifest_url.text().strip()
        database = self.context.config.settings.database
        try:
            database.manifest_url = value
        except Exception:  # noqa: BLE001 - validation error from Pydantic
            self.banner.show_message(
                "That database source is not a valid https, http or file URL.",
                StateKind.DANGER,
            )
            self.manifest_url.setText(database.manifest_url)
            return
        self.manifest_url.setText(database.manifest_url)
        self._persist("Database source updated.")
        self.context.configure_providers()

    def _save_appearance(self) -> None:
        if self._loading:
            return
        appearance = self.context.config.settings.appearance
        appearance.theme = str(self.theme_combo.currentData() or appearance.theme)
        appearance.ui_scale = int(self.scale_slider.value())
        appearance.compact_mode = self.compact_mode.isChecked()
        appearance.sidebar_behavior = SidebarBehavior(self.sidebar_behavior.currentData())
        self._persist()
        self._update_theme_description()
        self.appearance_changed.emit()

    def _on_scale_changed(self, value: int) -> None:
        self.scale_value.setText(f"{value}%")

    def _save_search(self) -> None:
        if self._loading:
            return
        search = self.context.config.settings.search
        search.default_lookup = LookupMode(self.default_lookup.currentData())
        search.behavior = SearchBehavior(self.search_behavior.currentData())
        search.search_delay_ms = int(self.search_delay.value())
        search.results_per_page = int(self.results_per_page.value())
        search.max_history = int(self.max_history.value())
        self._persist()
        self.search_settings_changed.emit()

    def _save_advanced(self) -> None:
        if self._loading:
            return
        advanced = self.context.config.settings.advanced
        advanced.log_level = LogLevel(self.log_level.currentData())
        advanced.log_retention_days = int(self.log_retention.value())
        advanced.verify_on_startup = self.verify_on_startup.isChecked()
        self._persist()
        set_level(advanced.log_level.value)

    def _update_theme_description(self) -> None:
        themes = getattr(self, "_theme_catalogue", None)
        if not themes:
            from app.ui.themes.palette import BUILTIN_THEMES

            themes = list(BUILTIN_THEMES)
        name = self.theme_combo.currentData()
        for theme in themes:
            if theme.name == name:
                self.theme_description.setText(theme.description)
                return
        self.theme_description.setText("")

    # -- actions ----------------------------------------------------------
    def _choose_database_directory(self) -> None:
        directory = QFileDialog.getExistingDirectory(
            self, "Choose a folder for the Bin-Tel database", str(self.context.database.path.parent)
        )
        if not directory:
            return
        target = Path(directory)
        confirmed = ConfirmDialog.ask(
            self,
            "Move the database?",
            f"Bin-Tel will copy the database to {target}, verify the copy, and only then "
            "remove the original.",
            confirm_text="Move database",
        )
        if not confirmed:
            return
        try:
            new_path = self.context.database.move_to(target)
        except Exception as exc:  # noqa: BLE001 - shown in a dialog
            self.show_error(exc)
            return
        self.context.config.settings.database.database_directory = str(new_path.parent)
        self._persist("Database location updated.")
        self.context.apply_database_path(new_path)
        self.path_label.setText(str(new_path))
        self.database_path_changed.emit()

    def _reset_database_directory(self) -> None:
        default = self.context.paths.database_file
        if default == self.context.database.path:
            return
        try:
            new_path = self.context.database.move_to(default.parent)
        except Exception as exc:  # noqa: BLE001 - shown in a dialog
            self.show_error(exc)
            return
        self.context.config.settings.database.database_directory = ""
        self._persist("Database moved back to the default location.")
        self.context.apply_database_path(new_path)
        self.path_label.setText(str(new_path))
        self.database_path_changed.emit()

    def _choose_local_manifest(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select a Bin-Tel database manifest",
            str(self.context.paths.downloads_dir),
            "Database manifest (*.json)",
        )
        if not path:
            return
        self.manifest_url.setText(Path(path).as_uri())
        self._save_manifest_url()

    def _clear_history(self) -> None:
        self.context.config.state.search_history = []
        self.context.config.save_state()
        self.banner.show_message("Search history cleared.", StateKind.SUCCESS)

    def _open_data_folder(self) -> None:
        reveal_in_file_manager(self.context.paths.data_dir)

    def _open_log_folder(self) -> None:
        open_path(self.context.paths.logs_dir)

    def _clear_cache(self) -> None:
        removed = 0
        freed = 0
        for directory in (self.context.paths.cache_dir, self.context.paths.downloads_dir):
            if not directory.exists():
                continue
            for item in directory.iterdir():
                if item.is_file() and item.name != "database-manifest.json":
                    try:
                        freed += item.stat().st_size
                        item.unlink()
                        removed += 1
                    except OSError:  # pragma: no cover - permissions
                        continue
        self.banner.show_message(
            f"Cleared {removed} cached file(s), freeing {format_bytes(freed)}.",
            StateKind.SUCCESS,
        )
        self._update_storage_summary()

    def _reset_settings(self) -> None:
        confirmed = ConfirmDialog.ask(
            self,
            "Reset all settings?",
            "Every preference returns to its default. Your database, backups and "
            "search history are not affected.",
            confirm_text="Reset settings",
            destructive=True,
        )
        if not confirmed:
            return
        self.context.config.reset_settings()
        self.load()
        self.context.apply_settings()
        self.appearance_changed.emit()
        self.general_changed.emit()
        self.search_settings_changed.emit()
        self.banner.show_message("Settings restored to their defaults.", StateKind.SUCCESS)

    def _update_storage_summary(self) -> None:
        def folder_size(path: Path) -> int:
            if not path.exists():
                return 0
            return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())

        paths = self.context.paths
        self.storage_label.setText(
            f"Application data: {paths.data_dir}\n"
            f"Database {format_bytes(self.context.database.size_bytes())} · "
            f"backups {format_bytes(folder_size(paths.backups_dir))} · "
            f"downloads {format_bytes(folder_size(paths.downloads_dir))} · "
            f"logs {format_bytes(folder_size(paths.logs_dir))}"
        )

    def refresh(self) -> None:
        self.path_label.setText(str(self.context.database.path))
        self._update_storage_summary()
