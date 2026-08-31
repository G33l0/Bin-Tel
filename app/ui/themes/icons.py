"""Theme-aware SVG icon loading.

Icons ship as single-colour SVGs drawn with ``currentColor``. The provider
substitutes the requested colour, renders at the device pixel ratio and caches
the result, so an icon is crisp on a HiDPI display and recolours instantly when
the theme changes.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from PyQt6.QtCore import QByteArray, QSize, Qt
from PyQt6.QtGui import QColor, QIcon, QImage, QPainter, QPixmap
from PyQt6.QtSvg import QSvgRenderer

from app.core.logging_config import get_logger
from app.core.paths import get_paths
from app.ui.themes.palette import DEFAULT_THEME, ThemeTokens, builtin_theme_map

logger = get_logger(__name__)

_MISSING_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
    'stroke="currentColor" stroke-width="1.6"><rect x="4" y="4" width="16" height="16" rx="3"/>'
    "</svg>"
)


@lru_cache(maxsize=128)
def _read_svg(path: str) -> str:
    try:
        return Path(path).read_text(encoding="utf-8")
    except OSError:
        logger.warning("Icon not found: %s", path)
        return _MISSING_SVG


def render_svg(source: str, color: str | None, size: int, ratio: float = 1.0) -> QPixmap:
    """Render an SVG string to a pixmap, optionally recoloured."""
    markup = source.replace("currentColor", color) if color else source
    renderer = QSvgRenderer(QByteArray(markup.encode("utf-8")))
    pixel_size = max(1, int(round(size * ratio)))
    image = QImage(pixel_size, pixel_size, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    renderer.render(painter)
    painter.end()
    pixmap = QPixmap.fromImage(image)
    pixmap.setDevicePixelRatio(ratio)
    return pixmap


class IconProvider:
    """Process-wide, theme-aware icon cache."""

    _instance: IconProvider | None = None

    def __init__(self, icons_dir: Path | None = None, theme: ThemeTokens | None = None) -> None:
        self._icons_dir = icons_dir or get_paths().icons_dir
        self._theme = theme or builtin_theme_map()[DEFAULT_THEME]
        self._cache: dict[tuple[str, str, int], QIcon] = {}

    @classmethod
    def instance(cls) -> IconProvider:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def set_theme(self, theme: ThemeTokens) -> None:
        self._theme = theme
        self._cache.clear()

    @property
    def theme(self) -> ThemeTokens:
        return self._theme

    def path_for(self, name: str) -> Path:
        return self._icons_dir / f"{name}.svg"

    def icon(self, name: str, color: str | None = None, size: int = 20) -> QIcon:
        """A themed :class:`QIcon`, with normal/disabled/selected variants."""
        colour = color or self._theme.text_secondary
        key = (name, colour, size)
        cached = self._cache.get(key)
        if cached is not None:
            return cached

        source = _read_svg(str(self.path_for(name)))
        icon = QIcon()
        for ratio in (1.0, 2.0):
            icon.addPixmap(
                render_svg(source, colour, size, ratio),
                QIcon.Mode.Normal,
                QIcon.State.Off,
            )
            icon.addPixmap(
                render_svg(source, self._theme.disabled_fg, size, ratio),
                QIcon.Mode.Disabled,
                QIcon.State.Off,
            )
            icon.addPixmap(
                render_svg(source, self._theme.nav_active_fg, size, ratio),
                QIcon.Mode.Selected,
                QIcon.State.On,
            )
        self._cache[key] = icon
        return icon

    def accent_icon(self, name: str, size: int = 20) -> QIcon:
        return self.icon(name, self._theme.primary, size)

    def pixmap(self, name: str, color: str | None = None, size: int = 20) -> QPixmap:
        source = _read_svg(str(self.path_for(name)))
        return render_svg(source, color or self._theme.text_secondary, size)

    # -- branding ---------------------------------------------------------
    def brand_pixmap(self, asset: str = "bintel-mark", size: int = 32) -> QPixmap:
        """Render a full-colour branding asset (no recolouring)."""
        path = get_paths().branding_dir / f"{asset}.svg"
        source = _read_svg(str(path))
        return render_svg(source, None, size)

    def brand_wide_pixmap(self, asset: str, width: int, height: int) -> QPixmap:
        """Render a non-square branding asset such as the logo lockup."""
        path = get_paths().branding_dir / f"{asset}.svg"
        markup = _read_svg(str(path)).replace("currentColor", self._theme.text_primary)
        renderer = QSvgRenderer(QByteArray(markup.encode("utf-8")))
        image = QImage(width * 2, height * 2, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        renderer.render(painter)
        painter.end()
        pixmap = QPixmap.fromImage(image)
        pixmap.setDevicePixelRatio(2.0)
        return pixmap

    @staticmethod
    def theme_color(value: str, alpha: int | None = None) -> QColor:
        """A QColor from a theme token, for widgets that paint their own text."""
        return color(value, alpha)

    def app_icon(self) -> QIcon:
        """The window/taskbar icon, at every size a desktop needs."""
        source = _read_svg(str(get_paths().branding_dir / "bintel-mark.svg"))
        icon = QIcon()
        for size in (16, 24, 32, 48, 64, 128, 256):
            icon.addPixmap(render_svg(source, None, size))
        return icon


def icon(name: str, color: str | None = None, size: int = 20) -> QIcon:
    """Module-level shorthand used throughout the UI."""
    return IconProvider.instance().icon(name, color, size)


def icon_size(size: int) -> QSize:
    return QSize(size, size)


def color(value: str, alpha: int | None = None) -> QColor:
    result = QColor(value)
    if alpha is not None:
        result.setAlpha(alpha)
    return result
