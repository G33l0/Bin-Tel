"""Applies a theme to the running application.

Switching a theme rebuilds the palette, the stylesheet and the icon cache, then
repolishes every widget — no restart required. The selection is persisted
through :class:`~app.core.config.ConfigManager`.
"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtGui import QColor, QPalette
from PyQt6.QtWidgets import QApplication

from app.core.config import ConfigManager
from app.core.logging_config import get_logger
from app.ui.themes.palette import (
    BUILTIN_THEMES,
    DEFAULT_THEME,
    ThemeTokens,
    builtin_theme_map,
    load_theme_overrides,
)
from app.ui.themes.stylesheet import build_stylesheet

logger = get_logger(__name__)


class ThemeManager(QObject):
    """Owns the active theme and broadcasts changes."""

    theme_changed = pyqtSignal(object)  # ThemeTokens

    def __init__(self, config: ConfigManager, themes_dir: Path | None = None) -> None:
        super().__init__()
        self._config = config
        self._themes: dict[str, ThemeTokens] = builtin_theme_map()
        if themes_dir is not None:
            for name, theme in load_theme_overrides(themes_dir).items():
                self._themes[name] = theme
        self._current: ThemeTokens = self._themes.get(
            config.settings.appearance.theme, self._themes[DEFAULT_THEME]
        )

    # -- catalogue --------------------------------------------------------
    @property
    def current(self) -> ThemeTokens:
        return self._current

    @property
    def themes(self) -> list[ThemeTokens]:
        """Built-ins first, in their designed order, then any custom themes."""
        ordered = [self._themes[theme.name] for theme in BUILTIN_THEMES if theme.name in self._themes]
        extras = sorted(
            (theme for name, theme in self._themes.items() if name not in {t.name for t in BUILTIN_THEMES}),
            key=lambda theme: theme.display_name,
        )
        return ordered + extras

    def get(self, name: str) -> ThemeTokens:
        return self._themes.get(name, self._themes[DEFAULT_THEME])

    # -- application ------------------------------------------------------
    def apply(self, name: str | None = None, *, persist: bool = True) -> ThemeTokens:
        """Make *name* the active theme and restyle the application."""
        appearance = self._config.settings.appearance
        theme = self.get(name or appearance.theme)
        self._current = theme

        application = QApplication.instance()
        if isinstance(application, QApplication):
            application.setPalette(build_palette(theme))
            application.setStyleSheet(
                build_stylesheet(
                    theme,
                    scale=appearance.ui_scale / 100.0,
                    compact=appearance.compact_mode,
                )
            )
            # Repolish so property-based selectors re-evaluate immediately.
            for widget in application.allWidgets():
                style = widget.style()
                if style is not None:
                    style.unpolish(widget)
                    style.polish(widget)
                widget.update()

        if persist and appearance.theme != theme.name:
            appearance.theme = theme.name
            self._config.save_settings()

        from app.ui.themes.icons import IconProvider

        IconProvider.instance().set_theme(theme)
        logger.info("Theme applied", extra={"context": {"theme": theme.name}})
        self.theme_changed.emit(theme)
        return theme

    def reapply(self) -> ThemeTokens:
        """Re-render after a scale or compact-mode change."""
        return self.apply(self._current.name, persist=False)

    def next_theme(self) -> ThemeTokens:
        """Cycle to the next theme (the header's theme button)."""
        catalogue = self.themes
        index = next(
            (i for i, theme in enumerate(catalogue) if theme.name == self._current.name), -1
        )
        return self.apply(catalogue[(index + 1) % len(catalogue)].name)


def build_palette(theme: ThemeTokens) -> QPalette:
    """A QPalette matching the stylesheet.

    Native widgets that ignore stylesheets (some file dialogs, tooltips on
    certain platforms) read the palette instead, so the two must agree.
    """
    palette = QPalette()
    role = QPalette.ColorRole
    group = QPalette.ColorGroup

    palette.setColor(role.Window, QColor(theme.window_bg))
    palette.setColor(role.WindowText, QColor(theme.text_primary))
    palette.setColor(role.Base, QColor(theme.input_bg))
    palette.setColor(role.AlternateBase, QColor(theme.table_row_alt_bg))
    palette.setColor(role.Text, QColor(theme.text_primary))
    palette.setColor(role.PlaceholderText, QColor(theme.input_placeholder))
    palette.setColor(role.Button, QColor(theme.button_bg))
    palette.setColor(role.ButtonText, QColor(theme.button_fg))
    palette.setColor(role.BrightText, QColor(theme.danger))
    palette.setColor(role.Highlight, QColor(theme.selection_bg))
    palette.setColor(role.HighlightedText, QColor(theme.selection_fg))
    palette.setColor(role.ToolTipBase, QColor(theme.tooltip_bg))
    palette.setColor(role.ToolTipText, QColor(theme.tooltip_fg))
    palette.setColor(role.Link, QColor(theme.primary))
    palette.setColor(role.LinkVisited, QColor(theme.primary_pressed))
    palette.setColor(role.Mid, QColor(theme.border))
    palette.setColor(role.Dark, QColor(theme.border_strong))
    palette.setColor(role.Shadow, QColor(theme.sidebar_bg))

    for disabled_role in (role.WindowText, role.Text, role.ButtonText):
        palette.setColor(group.Disabled, disabled_role, QColor(theme.disabled_fg))
    palette.setColor(group.Disabled, role.Base, QColor(theme.disabled_bg))
    palette.setColor(group.Disabled, role.Button, QColor(theme.disabled_bg))
    palette.setColor(group.Disabled, role.Highlight, QColor(theme.disabled_bg))
    return palette
